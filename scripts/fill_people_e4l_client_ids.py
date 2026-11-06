#!/usr/bin/env python3
"""One-time fill of people.e4l_client_id from the local e4l.db (scan share reward,
build piece 8 in the plan). A PRODUCTION DATA WRITE: run --apply only on Glen's yes.

Three modes, safest first:

  (default)        Local only. Reads e4l.db and prints what it would send. No network.
  --remote-count   Sends the rows to production with apply=false. Production counts
                   matches and writes nothing.
  --apply          Sends the rows with apply=true. Production sets e4l_client_id on
                   each matching people row where it is empty. It never overwrites an
                   id and never creates a person.

Production also needs SCAN_REWARD_ENABLED on, or the endpoint answers 404.

    python3 scripts/fill_people_e4l_client_ids.py --db ~/AI-Training/e4l.db
    python3 scripts/fill_people_e4l_client_ids.py --db ~/AI-Training/e4l.db --remote-count

The remote modes read the base URL from SCAN_REWARD_BASE_URL (default
https://illtowell.com) and the key from CONSOLE_SECRET in the environment. The key is
never printed.
"""
import argparse
import json
import os
import sqlite3
import sys
import urllib.request

def read_rows(path):
    """Every (email, client_id) in e4l.db, plus the emails that carry two different
    ids. Production refuses those too, and also catches two addresses of one person."""
    cx = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        raw = cx.execute(
            "SELECT client_id, email FROM e4l_clients "
            "WHERE email IS NOT NULL AND TRIM(email)<>''").fetchall()
    finally:
        cx.close()
    ids = {}
    for cid, email in raw:
        e = (email or "").strip().lower()
        if e and "@" in e and cid is not None:
            ids.setdefault(e, set()).add(str(cid))
    ambiguous = sorted(e for e, s in ids.items() if len(s) > 1)
    rows = [{"email": e, "client_id": next(iter(s))}
            for e, s in sorted(ids.items()) if len(s) == 1]
    return rows, ambiguous


def post(base, key, rows, apply):
    req = urllib.request.Request(
        f"{base.rstrip('/')}/api/console/people/e4l-client-ids",
        data=json.dumps({"rows": rows, "apply": bool(apply)}).encode(),
        headers={"Content-Type": "application/json", "X-Console-Key": key,
                 "User-Agent": "fill-people-e4l-client-ids/1"},
        method="POST")
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode())


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--db", required=True, help="path to the local e4l.db")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--remote-count", action="store_true",
                      help="ask production to count matches; writes nothing")
    mode.add_argument("--apply", action="store_true",
                      help="WRITE e4l_client_id in production (needs Glen's yes)")
    args = ap.parse_args(argv)

    rows, ambiguous = read_rows(os.path.expanduser(args.db))
    print(f"e4l.db: {len(rows)} clients with one email and one id; "
          f"{len(ambiguous)} emails carry two ids and are left out")
    if not (args.remote_count or args.apply):
        print("Local dry run. Nothing was sent. First 3 rows:")
        for r in rows[:3]:
            print(f"  {r['client_id']}  {r['email']}")
        return 0

    key = os.environ.get("CONSOLE_SECRET") or ""
    if not key:
        print("CONSOLE_SECRET is not set in the environment.", file=sys.stderr)
        return 2
    base = os.environ.get("SCAN_REWARD_BASE_URL") or "https://illtowell.com"
    # One request, so production judges ambiguity across the whole source.
    out = post(base, key, rows, apply=args.apply)
    if "rows" not in out:
        print(f"Production refused: {out}", file=sys.stderr)
        return 1
    print(("APPLIED: " if args.apply else "Count only, nothing written: ")
          + json.dumps(out, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
