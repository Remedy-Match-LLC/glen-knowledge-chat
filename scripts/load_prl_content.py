"""Load clinical's PRL ratings and focus-area map into the Biofield Intake database.

Files, written by the clinical pillar:
    ~/AI-Training/clinical/biofield-sections/prl/prl-ratings.json
    ~/AI-Training/clinical/biofield-sections/prl/focus-area-conditions.json

A dry run by default: the whole load runs in a transaction, prints counts and
rolls back. --commit keeps it, and runs only on Glen's word.

    python3 scripts/load_prl_content.py <prl folder> --client-test a49
    python3 scripts/load_prl_content.py <prl folder> --client-test a49 --commit

Ratings load not reviewed, written for --client-test, so only that client sees
them until Glen approves each on the page. Nothing is ticked. A rated name that
is not a seed product in that focus area is refused, not loaded. A josie_note
becomes a note on that client's product only.
"""
import argparse
import json
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dashboard import biofield_prl_section as ps  # noqa: E402
from dashboard.biofield_clinical_checklist import _norm  # noqa: E402


def load(cx, folder, client_test):
    ps.ensure_schema(cx)
    report = {"ratings_added": 0, "ratings_updated": 0, "notes": 0,
              "not_in_seed": [], "areas_mapped": 0, "areas_unmapped": [],
              "display_names": {}}
    names = {}
    ratings = json.load(open(os.path.join(folder, "prl-ratings.json"), encoding="utf-8"))
    for area in ratings.get("focus_areas") or []:
        fa = int(area["focus_area_id"])
        seed = {_norm(p["name"]) for p in ps.products_for(fa)}
        if area.get("display_name"):
            names[fa] = area["display_name"]
        for item in area.get("items") or []:
            if _norm(item.get("name")) not in seed:
                report["not_in_seed"].append(f"{fa}: {item.get('name')}")
                continue
            outcome = ps.upsert_rating(cx, fa, item, origin_test=client_test)
            report[f"ratings_{outcome}"] += 1
            note = str(item.get("josie_note") or "").strip()
            if note:
                ps._set_pick(cx, client_test, fa, item["name"], note=note[:600])
                report["notes"] += 1
    mapping = json.load(open(os.path.join(folder, "focus-area-conditions.json"), encoding="utf-8"))
    for area in mapping.get("focus_areas") or []:
        fa = int(area["focus_area_id"])
        keys = area.get("condition_keys") or []
        name = area.get("display_name") or names.get(fa) or ""
        ps.save_condition_map(cx, fa, keys, display_name=name)
        if name:
            report["display_names"][fa] = name
        if keys:
            report["areas_mapped"] += 1
        else:
            report["areas_unmapped"].append(area.get("focus_area_name") or str(fa))
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("folder")
    parser.add_argument("--db", default=os.environ.get("BIOFIELD_DB") or
                        os.path.expanduser("~/deploy-chat/chat_log.db"))
    parser.add_argument("--client-test", required=True,
                        help="the client the ratings were written for, e.g. a49")
    parser.add_argument("--commit", action="store_true")
    args = parser.parse_args()
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
