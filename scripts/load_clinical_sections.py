"""Load the five-sections content into the Biofield Intake's shared catalog.

Spec: vault clinical/specs/2026-10-17-clinical-summary-five-sections.md.
Content: one JSON file per condition, written by the clinical pillar, for example
~/AI-Training/clinical/biofield-sections/gut.json. Files without a condition_key and
sections are skipped (fullscript-matches.json holds candidates Glen has not chosen).

A dry run by default: it does the whole load inside a transaction, prints the
counts, and rolls back. --commit keeps it, and runs only on Glen's word.

    python3 scripts/load_clinical_sections.py <dir> --client-test a49
    python3 scripts/load_clinical_sections.py <dir> --client-test a49 --commit

What it writes:
- biofield_clinical_section_item: every food, nutrient, formulation and infoceutical, with its
  scores, reason, source and dose, marked not reviewed and added for the
  --client-test client. Only that client sees an item until Glen approves it on
  the page. Nothing is ticked for anyone.
- Each josie_note becomes a note on that client's item only.

The shared remedy list (biofield_clinical_catalog) is never written. A formulation
the condition does not already offer is listed as new; one Glen deleted from that
condition stays deleted and is listed too.

fullscript_wanted holds product types, not products, so it is never loaded.
"""
import argparse
import glob
import json
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dashboard.biofield_authoring import stress_suggestions  # noqa: E402
from dashboard.biofield_clinical_checklist import (  # noqa: E402
    _norm, display_labels, ensure_catalog_schema, ensure_display_schema, forgotten_remedies,
    remedies_for)
from dashboard import biofield_clinical_sections as cs  # noqa: E402

LOADED = (cs.FORMULATIONS, cs.INFOCEUTICALS, "foods_emphasize", "foods_avoid",
          cs.NUTRIENTS, cs.BOTANICALS, cs.ESSENTIAL_OILS, cs.HEALING_TOOLS)
# Keys a condition file may carry that hold no items to load.
NOT_ITEMS = ("fullscript_wanted",)
ROUTES = ("inhale and skin", "inhale only")


def _prune(cx, label, sections, client_test, chosen, report):
    """Remove what this content added earlier and no longer lists, so an item clinical
    moved (curcumin from Nutrients to Botanicals, 2026-10-18) is not in both.
    Only items still unreviewed and added for this client go. One Glen approved,
    ticked or rated for the client stays, and is listed instead."""
    key = _norm(label)
    for section in LOADED:
        listed = {_norm(item.get("name")) for item in sections.get(section) or []}
        rows = cx.execute(
            "SELECT name_key,name FROM biofield_clinical_section_item WHERE item_key=? "
            "AND section=? AND reviewed=0 AND origin_test=?",
            (key, section, client_test)).fetchall()
        for name_key, name in rows:
            if name_key in listed:
                continue
            pick = cx.execute(
                "SELECT checked,overall FROM biofield_clinical_section_pick WHERE "
                "test_id=? AND item_key=? AND section=? AND name_key=?",
                (client_test, key, section, name_key)).fetchone()
            ticked = name_key in chosen if section in cs.REMEDY_SECTIONS \
                else bool(pick and pick[0])
            if ticked or (pick and pick[1] is not None):
                report["kept_not_listed"].append(f"{label}: {section}: {name}")
                continue
            cx.execute("DELETE FROM biofield_clinical_section_item WHERE item_key=? "
                       "AND section=? AND name_key=?", (key, section, name_key))
            cx.execute("DELETE FROM biofield_clinical_section_pick WHERE test_id=? "
                       "AND item_key=? AND section=? AND name_key=?",
                       (client_test, key, section, name_key))
            report["removed"].append(f"{label}: {section}: {name}")


def load(cx, folder, client_test):
    cs.ensure_schema(cx)
    ensure_catalog_schema(cx)
    report = {"files": 0, "skipped_files": [], "added": {}, "updated": {},
              "formulations_new_to_condition": [], "remedies_kept_deleted": [], "notes": 0,
              "fullscript_types_not_loaded": 0, "new_conditions": [],
              "removed": [], "kept_not_listed": [],
              "extra_explanations_applied": 0, "extra_explanations_waiting": 0,
              "unknown_sections": [], "unknown_routes": [], "relabelled": []}
    from dashboard.biofield_clinical_proposals import selections
    chosen_by_row = selections(cx, client_test)
    known = {row[0] for row in cx.execute(
        "SELECT DISTINCT item_key FROM biofield_clinical_catalog")}
    for path in sorted(glob.glob(os.path.join(folder, "*.json"))):
        try:
            data = json.load(open(path, encoding="utf-8"))
        except ValueError as exc:
            report["skipped_files"].append(f"{os.path.basename(path)}: {exc}")
            continue
        if not isinstance(data, dict) or not data.get("condition_key") \
                or not isinstance(data.get("sections"), dict):
            report["skipped_files"].append(os.path.basename(path))
            continue
        report["files"] += 1
        label = data.get("label") or data["condition_key"]
        key = _norm(label)
        if key != _norm(data["condition_key"]):
            # A new name for the same condition (Glen, 2026-10-18: "Blood sugar" is
            # "Sugar Regulation"). Everything stays keyed by condition_key; the name is
            # the display override the Clinical Summary and the Plan already read.
            shown, label = label, data["condition_key"]
            key = _norm(label)
            ensure_display_schema(cx)
            before = display_labels(cx).get(key)
            if before != shown:
                cx.execute("INSERT INTO biofield_clinical_display (key,label,updated_at) "
                           "VALUES (?,?,CURRENT_TIMESTAMP) ON CONFLICT(key) DO UPDATE SET "
                           "label=excluded.label,updated_at=CURRENT_TIMESTAMP", (key, shown))
                report["relabelled"].append(
                    f"{key}: {before!r} -> {shown!r}" if before else f"{key}: {shown!r}")
        if key not in known:
            report["new_conditions"].append(label)
        sections = data["sections"]
        # A section this loader does not know would be skipped without a word, and an
        # item clinical moved into it would vanish from the Plan.
        report["unknown_sections"] += [f"{label}: {name}" for name in sections
                                       if name not in LOADED and name not in NOT_ITEMS]
        report["fullscript_types_not_loaded"] += len(sections.get("fullscript_wanted") or [])
        offered = {_norm(row["remedy"]) for row in remedies_for(
            cx, label, historical=stress_suggestions(cx, label))}
        deleted = forgotten_remedies(cx, label)
        chosen = {_norm(r) for r in chosen_by_row.get(key) or []}
        _prune(cx, label, sections, client_test, chosen, report)
        applied, waiting = cs.set_extra_explanations(cx, label, data.get("extra_explanations"))
        report["extra_explanations_applied"] += applied
        report["extra_explanations_waiting"] += waiting
        for section in LOADED:
            for item in sections.get(section) or []:
                outcome = cs.upsert_item(cx, label, section, item, origin_test=client_test)
                route = str(item.get("route") or "").strip()
                if section == cs.ESSENTIAL_OILS and route not in ROUTES:
                    report["unknown_routes"].append(f"{label}: {item.get('name')}: {route!r}")
                report[outcome][section] = report[outcome].get(section, 0) + 1
                name_key = _norm(item.get("name"))
                if section in cs.REMEDY_SECTIONS:
                    if name_key in deleted:
                        report["remedies_kept_deleted"].append(f"{label}: {item['name']}")
                    elif name_key not in offered:
                        report["formulations_new_to_condition"].append(
                            f"{label}: {item['name']}")
                note = str(item.get("josie_note") or "").strip()
                if note:
                    cs.save_pick(cx, client_test, label, section, item["name"],
                                 note=note, commit=False)
                    report["notes"] += 1
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("folder")
    parser.add_argument("--db", default=os.environ.get("BIOFIELD_DB") or
                        os.path.expanduser("~/deploy-chat/chat_log.db"))
    parser.add_argument("--client-test", required=True,
                        help="the client the content was written for, e.g. a49")
    parser.add_argument("--commit", action="store_true")
    args = parser.parse_args()
    if not os.path.isdir(args.folder):
        raise SystemExit(f"not a folder: {args.folder}")
    cx = sqlite3.connect(args.db)
    try:
        report = load(cx, args.folder, args.client_test)
        if args.commit and report["unknown_sections"]:
            cx.rollback()
            print(json.dumps(report, indent=1))
            raise SystemExit("Not committed: unknown sections "
                             + ", ".join(report["unknown_sections"]))
        if args.commit:
            cx.commit()
        else:
            cx.rollback()
    finally:
        cx.close()
    print(json.dumps(report, indent=1))
    print(("COMMITTED to " if args.commit else "Dry run, nothing kept: ") + args.db)


if __name__ == "__main__":
    main()
