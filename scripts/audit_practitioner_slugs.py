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

It ALSO now reports the whole-namespace rule the page_slug feature added
(docs/superpowers/sdd/2026-09-01-practitioner-page-slug): a page_slug must be
unique against every row's slug AND every row's page_slug, at ANY status --
the same reason page_slug_is_taken and slug_is_taken check beyond 'approved'
-- because a pending row must never shadow a published URL the moment it is
approved. That check needs `affiliate_signups.page_slug` to exist; run against
a database that predates it and this audit says so and skips it, rather than
crashing.

It also reports the ONE thing that proves a Postgres deploy's backfill really
reached production rows: `SELECT COUNT(*) FROM affiliate_signups WHERE
page_slug IS NULL`. `dashboard/practitioner_slugs.init_page_slug` prints a
boot-time WARNING when this count is nonzero right after the backfill runs,
but that print only fires once, on a booting process, and is easy to miss in
a log stream. This is the same count, on demand, against any database.

Run this READ-ONLY before merging or deploying. It must report zero failures.
Anything it lists needs a decision (rename + redirect, or relax the
validator) before the branch ships.

Default backend is a genuinely read-only SQLite connection (`mode=ro` -- the
OS refuses any write, not just this script's own code path). Production runs
DB_BACKEND=postgres (see dashboard/db.py); psycopg has no connection-string
equivalent of mode=ro, so a Postgres run is read-only by discipline instead --
every query below is a SELECT, and the connection is never committed.

    python3 scripts/audit_practitioner_slugs.py            # uses DATA_DIR/chat_log.db
    python3 scripts/audit_practitioner_slugs.py /path/to/chat_log.db

    # against production (Doppler supplies DB_BACKEND=postgres + PG_DSN):
    doppler run -- python3 scripts/audit_practitioner_slugs.py chat_log

The reserved-word half needs the live Flask url_map (`import app`), which
needs Doppler env and will not import in a bare shell. When that import
fails, the collision half of this audit still runs in full -- only the
reserved-word checks fall back to the static EXTRA_RESERVED list and say so.
"""
import os
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dashboard import db  # noqa: E402
from dashboard import practitioner_slugs as ps  # noqa: E402


def _connect(db_path):
    """A read-only connection to `db_path`, on whichever backend is active."""
    if db.backend() == "postgres":
        return db.connect(str(db_path))
    return sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)   # read-only, no DDL


def _reserved():
    """The reserved-word set: the live url_map when `app` is importable, else
    the static EXTRA_RESERVED fallback. Split out so a bare shell (no Doppler
    env) still runs the collision half of this audit in full."""
    try:
        import app as appmod
        reserved = ps.reserved_for(appmod.app.url_map)
        note = "live url_map + EXTRA_RESERVED"
    except Exception as e:
        reserved = ps.EXTRA_RESERVED
        note = f"EXTRA_RESERVED only (app import failed: {e!r})"
    return reserved, note


def _namespace_collisions(rows):
    """rows: iterable of (slug, page_slug_or_None, name, email, status).

    Every normalized name a row claims -- its own slug, and its own page_slug
    when it differs -- goes into one map, at ANY status: claiming spans the
    whole namespace, per page_slug_is_taken's docstring, or a pending row
    could shadow a published URL the moment it is approved. Any name claimed
    by more than one OWNER (distinct affiliate slug) is a collision the
    unique index cannot be created over.

    Returns {normalized_name: [(owner_slug, field, name, email, status), ...]}
    for names claimed 2+ times, sorted by nothing in particular -- the caller
    sorts for display.
    """
    claims = defaultdict(list)
    for slug, page_slug, name, email, status in rows:
        s = ps.normalize(slug)
        if s:
            claims[s].append((slug, "slug", name, email, status))
        p = ps.normalize(page_slug) if page_slug else ""
        if p and p != s:
            claims[p].append((slug, "page_slug", name, email, status))
    return {name: owners for name, owners in claims.items() if len(owners) > 1}


def main(db_path=None):
    if not db_path:
        root = os.environ.get("DATA_DIR") or str(Path(__file__).resolve().parent.parent)
        db_path = str(Path(root) / "chat_log.db")
    print(f"auditing: {db_path}  (backend={db.backend()})")

    cx = _connect(db_path)
    reserved, reserved_note = _reserved()
    print(f"reserved set: {reserved_note} ({len(reserved)} words)\n")

    # ── Section 1: approved-roster shape/reserved audit ──────────────────────
    rows = cx.execute(
        "SELECT slug, name, email, status FROM affiliate_signups"
        " WHERE status='approved' ORDER BY slug").fetchall()

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

    # ── Section 2: whole-namespace page_slug audit ────────────────────────────
    has_page_slug = db.column_exists(cx, "affiliate_signups", "page_slug")
    collisions, reserved_page_hit, null_count = {}, [], 0

    if has_page_slug:
        all_rows = cx.execute(
            "SELECT slug, page_slug, name, email, status FROM affiliate_signups"
            " ORDER BY slug").fetchall()
        collisions = _namespace_collisions(all_rows)
        for slug, page_slug, name, email, _status in all_rows:
            p = ps.normalize(page_slug) if page_slug else ""
            if p and p != ps.normalize(slug):
                try:
                    ps.check_not_reserved(p, reserved)
                except ps.SlugError as e:
                    reserved_page_hit.append((slug, p, name, email, str(e)))
        null_row = cx.execute(
            "SELECT COUNT(*) FROM affiliate_signups WHERE page_slug IS NULL"
        ).fetchone()
        null_count = (null_row[0] if null_row else 0) or 0
        print(f"\nresidual NULL page_slug (any status): {null_count}")
    else:
        print("\naffiliate_signups.page_slug does not exist on this database"
              " -- skipping the whole-namespace collision, page_slug reserved-"
              "word, and residual-NULL checks (this DB predates the page_slug"
              " feature)")

    print(f"namespace collisions (slug/page_slug, any status): {len(collisions)}")
    for name in sorted(collisions):
        print(f"  [COLLISION] {name!r} claimed by:")
        for owner_slug, field, oname, oemail, ostatus in collisions[name]:
            print(f"      {owner_slug!r} via {field}"
                  f"  ({oname} <{oemail}>, {ostatus})")

    print(f"FAIL reserved word (page_slug): {len(reserved_page_hit)}")
    for slug, p, name, email, why in reserved_page_hit:
        print(f"  [RESERVED-PAGE] {p!r}  (owner slug {slug!r}, {name} <{email}>)"
              f"  -- {why}")

    total = (len(bad_shape) + len(reserved_hit) + len(collisions)
             + len(reserved_page_hit) + null_count)
    print("\nRESULT:", "PASS - safe to merge" if total == 0
          else f"FAIL - {total} issue(s) need a decision before merge")
    return 1 if total else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else None))
