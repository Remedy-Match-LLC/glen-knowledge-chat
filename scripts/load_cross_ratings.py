"""Load clinical's cross-ratings for Glen's overview chart.

Content: ~/AI-Training/clinical/biofield-sections/cross-rate/cross-ratings-top40.json,
written by clinical and approved by Glen (2026-10-18). Only cells marked "approved":
true are kept, and a cell the condition files already rate is skipped. The stored set
is replaced, so withdrawing an approval in the file withdraws it here.

A dry run by default: it loads inside a transaction, prints the counts, and rolls
back. --commit keeps it, and runs only on Glen's word.

    python3 scripts/load_cross_ratings.py <file>
    python3 scripts/load_cross_ratings.py <file> --commit
"""
import argparse
import json
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dashboard.biofield_overview import load_cross_ratings  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("file")
    parser.add_argument("--db", default=os.environ.get("BIOFIELD_DB") or
                        os.path.expanduser("~/deploy-chat/chat_log.db"))
    parser.add_argument("--commit", action="store_true")
    args = parser.parse_args()
    if not os.path.isfile(args.file):
        raise SystemExit(f"not a file: {args.file}")
    cx = sqlite3.connect(args.db)
    try:
        report = load_cross_ratings(cx, args.file)
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
