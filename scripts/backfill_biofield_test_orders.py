#!/usr/bin/env python3
"""Propose which production order is each authored Biofield test's invoice.

Tests made before 2026-10-23 have no stored link, so the "Your authored tests" list
matches them by email and marks the invoice cells approximate. This proposes a link
per test, from the client's Biofield orders made between this test and their next.

A dry run by default: it reads the local DB read-only, reads prod's orders (one GET
per client email, the same read the invoice lookup uses), and prints proposals by
test id and order number, with no client names. It writes only with --apply, and then
only the single-candidate proposals, into the local biofield_test_orders table.

Usage (on Glen's Mac, where the local Biofield app's DB lives):
  doppler run --project remedy-match --config prd -- \\
    python3 scripts/backfill_biofield_test_orders.py [--db PATH] [--apply]
"""
import argparse
import os
import sqlite3
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _default_db():
    return os.environ.get("BIOFIELD_DB", str(ROOT / "chat_log.db"))


def run(db_path, apply=False, fetch=None, out=print, chain_fetch=None):
    from dashboard import biofield_invoice, biofield_progress as bp
    fetch = fetch or biofield_invoice.client_orders_with_status
    # Read-only connection, and a plain SELECT: list_authored would create tables.
    with bp.open_ro(db_path) as cx:
        authored = ([{"test_id": "a" + str(i), "email": e or "", "date": d or ""}
                     for i, e, d in cx.execute(
                         "SELECT id, email, date_test FROM biofield_auth_tests ORDER BY id")]
                    if bp._has_table(cx, "biofield_auth_tests") else [])
        links = bp.stored_links(cx)
    emails = sorted({(t.get("email") or "").strip().lower() for t in authored
                     if (t.get("email") or "").strip() and t["test_id"] not in links})
    orders_by_email = {}
    for e in emails:
        rows, err = fetch(e)
        orders_by_email[e] = None if err else rows
    proposals = bp.propose_links(authored, links, orders_by_email)
    for p in proposals:
        cands = ",".join(f"#{c}" for c in p["candidates"])
        target = f"#{p['order_id']}" if p["order_id"] else "-"
        out(f"{p['test_id']:>6}  {p['status']:<9} {target:<7} {cands:<20} {p['reason']}")
    counts = Counter(p["status"] for p in proposals)
    out("counts: " + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    if not apply:
        out("DRY RUN: nothing written. Re-run with --apply to store the 'propose' rows.")
        return proposals, 0
    # Replacement chains, so an order another test reaches through a replaced order is
    # refused. No chains, no writes: a guess here would store a shared invoice.
    chain_fetch = chain_fetch or bp.fetch_order
    with bp.open_ro(db_path) as cx:
        held = set(bp.stored_links(cx).values())
    want = held | {p["order_id"] for p in proposals if p["status"] == "propose"}
    chains, err = chain_fetch(sorted(want)) if want else ([], None)
    if err:
        out(f"NOT APPLIED: could not read replacement chains ({err}). Nothing written.")
        return proposals, 0
    with sqlite3.connect(db_path) as cx:
        n = bp.apply_links(cx, proposals, out=out, resolve=bp.resolver(chains))
    out(f"APPLIED: stored {n} link(s).")
    return proposals, n


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", default=_default_db())
    ap.add_argument("--apply", action="store_true", help="write the proposals")
    a = ap.parse_args(argv)
    run(a.db, apply=a.apply)


if __name__ == "__main__":
    main()
