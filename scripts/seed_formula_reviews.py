#!/usr/bin/env python3
"""Seed the formula review dates (Glen, 2026-11-09, to-do #66).

Two sources, both loaded through the same idempotent add path:

1. The starting rows: Liver Support and Mitochondrial Biogenesis, reviewed 9 November
   2026, decision waiting.
2. Optionally (--backfill FILE), the past dates the formulations tab proved from the
   vault (formula-review-backfill-2026-11-09.json). Each row names a product FOLDER, not
   a slug. The folder is mapped to a products.json slug with the app's existing resolver
   (dashboard.biofield_portal_publish.resolve_remedy_slug), no new matcher. Only steps
   with a date are loaded. The step's "basis" becomes the row's note, its "file" the link.

Dry-run by default: it prints what it would add and writes nothing anywhere, except the
list of unmatched backfill folders (--unmatched-out). Re-running is safe: the table holds
one row per (slug, step, date, value), and a repeat answers "already there".

  python3 scripts/seed_formula_reviews.py --backfill FILE          # dry run, prints counts
  python3 scripts/seed_formula_reviews.py --backfill FILE --db x.db --apply   # a local file
  doppler run -p remedy-match -c prd -- \\
    python3 scripts/seed_formula_reviews.py --backfill FILE --apply --base https://illtowell.com

The last line writes to PRODUCTION through POST /api/console/formula-reviews. Run it only
with Glen's yes, and only after the route is deployed.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dashboard import formula_reviews as FR  # noqa: E402

REVIEWS = "/Users/remedymatch/YouCloned/Agents/Build/work/jmw9kaqvz"
STARTING_ROWS = [
    {"slug": "liver-support", "step": "review", "date": "2026-11-09",
     "link": f"{REVIEWS}/liver-support/review.html"},
    {"slug": "liver-support", "step": "decision", "date": "2026-11-09", "value": "waiting"},
    {"slug": "mitochondrial-biogenesis", "step": "review", "date": "2026-11-09",
     "link": f"{REVIEWS}/mitochondrial-biogenesis/review.html"},
    {"slug": "mitochondrial-biogenesis", "step": "decision", "date": "2026-11-09",
     "value": "waiting"},
]

# The backfill file's step names -> the table's.
BACKFILL_STEPS = {"review": "review", "decision": "decision",
                  "app": "app_formula_updated", "filemaker": "filemaker_backup",
                  "label_printed": "label_printed", "store_page": "store_page_updated"}


def load_catalog() -> dict:
    return json.loads((ROOT / "data" / "products.json").read_text())["products"]


def map_folder(row, catalog):
    """(slug, how) for one backfill row, or (None, None). The folder itself as a slug
    first (moved to its live twin if retired), then the folder as a name, then the
    FileMaker name, each through the app's resolver."""
    from dashboard.biofield_portal_publish import resolve_remedy_slug
    from dashboard.products import superseded_slug
    folder = str(row.get("folder") or "").strip()
    if folder in catalog:
        live = superseded_slug(folder, catalog)
        if not (catalog.get(live) or {}).get("inactive"):
            return live, ("slug" if live == folder else "retired slug's live twin")
    words = " ".join(folder.replace("-", " ").split())
    if words:
        hit = resolve_remedy_slug(words, catalog)
        if hit:
            return hit, "folder name"
    fmp = str(row.get("fmp_name") or "").strip()
    if fmp and fmp.lower() != "unknown":
        hit = resolve_remedy_slug(fmp, catalog)
        if hit:
            return hit, "FileMaker name"
    return None, None


def load_backfill(data, catalog) -> dict:
    """The rows to add from the backfill file's parsed JSON, plus what did not map.
    Every row is checked with the table's own validator, so the dry run refuses exactly
    what the API would."""
    out = {"mapped": [], "unmatched": [], "rows": [], "skipped": []}
    for r in data.get("rows") or []:
        folder = str(r.get("folder") or "")
        slug, how = map_folder(r, catalog)
        dated = [(k, v) for k, v in (r.get("steps") or {}).items()
                 if isinstance(v, dict) and v.get("date")]
        if not slug:
            out["unmatched"].append({"folder": folder, "dated_steps": len(dated)})
            continue
        out["mapped"].append({"folder": folder, "slug": slug, "how": how})
        for k, v in dated:
            step = BACKFILL_STEPS.get(k)
            ev = {"slug": slug, "step": step, "date": str(v["date"])[:10],
                  "value": v.get("outcome") if step == "decision" else None,
                  "link": str(v.get("file") or ""), "note": str(v.get("basis") or "")}
            try:
                FR.validate(ev["slug"], ev["step"], ev["date"], ev["value"], catalog=catalog)
            except ValueError as e:
                out["skipped"].append({"folder": folder, "step": k, "why": str(e)})
                continue
            out["rows"].append(ev)
    return out


def apply_local(db_path, rows, catalog):
    from dashboard import db
    cx = db.connect(db_path)
    try:
        FR.init_tables(cx)
        return [FR.add_event(cx, e["slug"], e["step"], e["date"], value=e.get("value"),
                             link=e.get("link", ""), note=e.get("note", ""),
                             who="seed", catalog=catalog)["created"] for e in rows]
    finally:
        cx.close()


def apply_api(base, key, rows):
    created = []
    for e in rows:
        body = json.dumps({k: e.get(k) for k in ("slug", "step", "date", "value",
                                                 "link", "note")}).encode()
        req = urllib.request.Request(
            base.rstrip("/") + "/api/console/formula-reviews", data=body, method="POST",
            headers={"X-Console-Key": key, "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                created.append(bool(json.load(resp).get("created")))
        except urllib.error.HTTPError as err:
            raise SystemExit(f"Refused at {e['slug']} {e['step']}: {err.code} {err.read()[:200]!r}")
    return created


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--backfill", help="formula-review-backfill JSON from the formulations tab")
    ap.add_argument("--unmatched-out", default=str(ROOT / "backfill-unmatched.txt"))
    ap.add_argument("--apply", action="store_true", help="write; without it nothing is written")
    ap.add_argument("--db", help="with --apply: a local SQLite file to write instead")
    ap.add_argument("--base", help="with --apply: the app to POST to, e.g. https://illtowell.com")
    a = ap.parse_args(argv)

    catalog = load_catalog()
    rows = []
    for e in STARTING_ROWS:
        FR.validate(e["slug"], e["step"], e["date"], e.get("value"), catalog=catalog)
        rows.append(e)
    print("Starting rows:", len(STARTING_ROWS))
    if a.backfill:
        res = load_backfill(json.loads(Path(a.backfill).read_text()), catalog)
        rows += res["rows"]
        print(f"Backfill rows mapped:   {len(res['mapped'])}")
        print(f"Backfill rows unmatched: {len(res['unmatched'])}")
        print(f"Backfill step rows to insert: {len(res['rows'])}")
        for s in res["skipped"]:
            print(f"  skipped {s['folder']} {s['step']}: {s['why']}")
        for m in res["mapped"]:
            if m["how"] != "slug":
                print(f"  mapped by {m['how']}: {m['folder']} -> {m['slug']}")
        Path(a.unmatched_out).write_text("".join(
            f"{u['folder']}\t{u['dated_steps']} dated steps\n" for u in res["unmatched"]))
        print("Unmatched folders written to", a.unmatched_out)

    if not a.apply:
        print(f"Dry run: {len(rows)} rows would be sent. Nothing written.")
        return 0
    if a.db:
        created = apply_local(a.db, rows, catalog)
    elif a.base:
        key = os.environ.get("CONSOLE_SECRET", "")
        if not key:
            raise SystemExit("CONSOLE_SECRET is not set.")
        created = apply_api(a.base, key, rows)
    else:
        raise SystemExit("--apply needs --db or --base.")
    print(f"Added {sum(created)}, already there {len(created) - sum(created)}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
