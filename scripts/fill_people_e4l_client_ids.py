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

BATCH = 500


def read_rows(path):
    cx = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        rows = cx.execute(
            "SELECT client_id, email FROM e4l_clients "
            "WHERE email IS NOT NULL AND TRIM(email)<>''").fetchall()
    finally:
        cx.close()
    seen, out = set(), []
    for cid, email in rows:
        e = (email or "").strip().lower()
        if e and "@" in e and e not in seen:
            seen.add(e)
            out.append({"email": e, "client_id": str(cid)})
    return out


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

    rows = read_rows(os.path.expanduser(args.db))
    print(f"e4l.db: {len(rows)} clients with a usable email")
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
    totals = {}
    for i in range(0, len(rows), BATCH):
        out = post(base, key, rows[i:i + BATCH], apply=args.apply)
        if not out.get("ok", True) or "rows" not in out:
            print(f"Production refused batch {i // BATCH + 1}: {out}", file=sys.stderr)
            return 1
        for k, v in out.items():
            if isinstance(v, int) and not isinstance(v, bool):
                totals[k] = totals.get(k, 0) + v
    print(("APPLIED: " if args.apply else "Count only, nothing written: ")
          + json.dumps(totals, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
