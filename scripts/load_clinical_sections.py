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
- biofield_clinical_section_item: every food, nutrient and formulation, with its
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
    _norm, ensure_catalog_schema, forgotten_remedies, remedies_for)
from dashboard import biofield_clinical_sections as cs  # noqa: E402

LOADED = (cs.FORMULATIONS, "foods_emphasize", "foods_avoid", cs.NUTRIENTS)


def load(cx, folder, client_test):
    cs.ensure_schema(cx)
    ensure_catalog_schema(cx)
    report = {"files": 0, "skipped_files": [], "added": {}, "updated": {},
              "formulations_new_to_condition": [], "remedies_kept_deleted": [], "notes": 0,
              "fullscript_types_not_loaded": 0, "new_conditions": []}
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
            raise SystemExit(f"{path}: label {label!r} does not match its condition_key")
        if key not in known:
            report["new_conditions"].append(label)
        sections = data["sections"]
        report["fullscript_types_not_loaded"] += len(sections.get("fullscript_wanted") or [])
        offered = {_norm(row["remedy"]) for row in remedies_for(
            cx, label, historical=stress_suggestions(cx, label))}
        deleted = forgotten_remedies(cx, label)
        for section in LOADED:
            for item in sections.get(section) or []:
                outcome = cs.upsert_item(cx, label, section, item, origin_test=client_test)
                report[outcome][section] = report[outcome].get(section, 0) + 1
                name_key = _norm(item.get("name"))
                if section == cs.FORMULATIONS:
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
