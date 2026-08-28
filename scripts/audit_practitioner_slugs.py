#!/usr/bin/env python3
"""Roster audit: does every LIVE practitioner slug satisfy the new validator?

MERGE GATE for the practitioner-site branch. Spec section 1
(docs/superpowers/specs/2026-08-27-practitioner-website-design.md).

`dashboard/practitioner_slugs.check_shape` was written AFTER slugs had already
been minted by `dashboard/affiliate_dashboard._mint_affiliate_slug`, which could
emit values the validator refuses (trailing hyphen from `.strip("-")` before
`[:30]`, doubled hyphen from the collision suffix, sub-MIN_LEN short names, and
bare reserved words from the email-local-part fallback).

That matters because `/p/<slug>` on the portal host now redirects to `/<slug>`.
If a live slug fails `check_shape`, that redirect lands on a 404 -- and a
practitioner's already-distributed URL breaks.

Run this READ-ONLY against the production LOG_DB before merging. It must report
zero failures. Anything it lists needs a decision (rename + redirect, or relax
the validator) before the branch ships.

    python3 scripts/audit_practitioner_slugs.py            # uses DATA_DIR/chat_log.db
    python3 scripts/audit_practitioner_slugs.py /path/to/chat_log.db
"""
import os
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dashboard import practitioner_slugs as ps  # noqa: E402


def main(db_path=None):
    if not db_path:
        root = os.environ.get("DATA_DIR") or str(Path(__file__).resolve().parent.parent)
        db_path = str(Path(root) / "chat_log.db")
    print(f"auditing: {db_path}")

    cx = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)   # read-only, no DDL
    rows = cx.execute(
        "SELECT slug, name, email, status FROM affiliate_signups"
        " WHERE status='approved' ORDER BY slug").fetchall()

    # Route-segment collisions need the live url_map; import app only if we can.
    try:
        import app as appmod
        reserved = ps.reserved_for(appmod.app.url_map)
        reserved_note = "live url_map + EXTRA_RESERVED"
    except Exception as e:
        reserved = ps.EXTRA_RESERVED
        reserved_note = f"EXTRA_RESERVED only (app import failed: {e!r})"
    print(f"reserved set: {reserved_note} ({len(reserved)} words)\n")

    bad_shape, reserved_hit = [], []
    for slug, name, email, _status in rows:
        try:
            ps.check_shape(slug)
        except ps.SlugError as e:
            bad_shape.append((slug, name, email, str(e)))
            continue
        try:
            ps.check_not_reserved(slug, reserved)
        except ps.SlugError as e:
            reserved_hit.append((slug, name, email, str(e)))

    print(f"approved practitioners: {len(rows)}")
    print(f"FAIL check_shape:       {len(bad_shape)}")
    print(f"FAIL reserved word:     {len(reserved_hit)}")

    for label, group in (("SHAPE", bad_shape), ("RESERVED", reserved_hit)):
        for slug, name, email, why in group:
            print(f"  [{label}] {slug!r}  ({name} <{email}>)  -- {why}")

    total = len(bad_shape) + len(reserved_hit)
    print("\nRESULT:", "PASS - safe to merge" if total == 0
          else f"FAIL - {total} slug(s) need a decision before merge")
    return 1 if total else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else None))
