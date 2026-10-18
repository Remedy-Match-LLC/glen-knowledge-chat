"""Load clinical's Plan report wording: the merge map, the score-heading lines, the
report-level lines and the ingredient caution table.

Content: ~/AI-Training/clinical/biofield-sections/plan-report/merge-map.json,
score-lines.json and frame-lines.json, written by clinical. Only entries marked "approved": true are kept,
because Glen approves each merge and each line once (2026-10-18). The stored set is
replaced, so withdrawing an approval in the file withdraws it here.

A dry run by default: it loads inside a transaction, prints the counts, and rolls
back. --commit keeps it, and runs only on Glen's word.

    python3 scripts/load_plan_report_content.py <plan-report dir>
    python3 scripts/load_plan_report_content.py <plan-report dir> --commit
"""
import argparse
import json
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dashboard.biofield_plan_report import load_content  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("folder")
    parser.add_argument("--db", default=os.environ.get("BIOFIELD_DB") or
                        os.path.expanduser("~/deploy-chat/chat_log.db"))
    parser.add_argument("--commit", action="store_true")
    args = parser.parse_args()
    if not os.path.isdir(args.folder):
        raise SystemExit(f"not a folder: {args.folder}")
    cx = sqlite3.connect(args.db)
    try:
        report = load_content(cx, args.folder)
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
