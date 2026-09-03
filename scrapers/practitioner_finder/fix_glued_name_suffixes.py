"""fix_glued_name_suffixes.py — one-off, re-runnable script (NOT a migration)
to correct existing practitioners rows whose name still carries a source
directory's glued-digit disambiguation artifact.

Background: AANP's "Find an ND" directory appends a bare digit straight onto
<p class="name"> to tell apart several offices for the same person
("Stacie Han2", "Dr. Paul Giordano2"/"3", "Nicole Egenberger2",
"Lisa Arnold2"). scrapers/practitioner_finder/aanp.py copies that text
faithfully, and each row's distinct AANP member id in source_url confirms
these are one real person with several real offices, not duplicates — only
the displayed name is wrong.

scrapers/practitioner_finder/db.py's _normalize_for_write now strips that
pattern at the write boundary, so a re-scrape can no longer reintroduce it.
This script fixes rows written before that fix landed. Re-running it after a
successful --apply reports zero remaining candidates.

Defaults to REPORT ONLY. Pass --apply to write. Do not run --apply against
production without a human's go-ahead.

Invoke (report only):
  doppler run --project remedy-match --config prd -- \\
    python3 -m scrapers.practitioner_finder.fix_glued_name_suffixes

Invoke (write):
  doppler run --project remedy-match --config prd -- \\
    python3 -m scrapers.practitioner_finder.fix_glued_name_suffixes --apply
"""
import argparse
import sys

from db_supabase import supabase_cursor
from scrapers.practitioner_finder.normalize import strip_glued_name_suffix


def _rows_with_names() -> list[dict]:
    with supabase_cursor() as cur:
        cur.execute("SELECT id, name, source_url FROM practitioners WHERE name IS NOT NULL")
        return [dict(r) for r in cur.fetchall()]


def find_candidates(rows: list[dict]) -> list[tuple[str, str, str, str]]:
    """Pure: rows whose name would change under strip_glued_name_suffix.

    Returns [(id, source_url, old_name, new_name), ...], in input order."""
    out: list[tuple[str, str, str, str]] = []
    for row in rows:
        old = row.get("name")
        new = strip_glued_name_suffix(old)
        if new != old:
            out.append((str(row.get("id")), row.get("source_url") or "", old, new))
    return out


def apply_renames(candidates: list[tuple[str, str, str, str]]) -> int:
    """Write the corrected name for each candidate. Returns the count written."""
    if not candidates:
        return 0
    written = 0
    with supabase_cursor() as cur:
        for pid, _source_url, _old, new in candidates:
            cur.execute(
                "UPDATE practitioners SET name=%s, updated_at=now() WHERE id=%s",
                (new, pid),
            )
            written += 1
    return written


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true",
                    help="Write the corrected names. Without this flag the "
                         "script only reports what it would change.")
    args = ap.parse_args()

    rows = _rows_with_names()
    candidates = find_candidates(rows)

    if not candidates:
        print("No practitioner names carry a glued digit suffix. Nothing to do.")
        return 0

    print(f"{len(candidates)} name(s) carry a glued digit suffix:")
    for pid, source_url, old, new in candidates:
        print(f"  {pid}  {old!r} -> {new!r}  ({source_url})")

    if args.apply:
        written = apply_renames(candidates)
        print(f"\nwrote {written} row(s).")
    else:
        print("\nDRY RUN - no rows written. Re-run with --apply to write.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
