"""When each product's formula was last reviewed, and how far each review has got.

Glen approved the plan on 2026-11-09 (to-do #66), with one change: "The app gets the
formula first, FileMaker gets a backup copy". So the dates live here, in the app.

One row per product and step, in `formula_review_events`. A product goes through the
steps in STEPS order. The decision step carries a value from DECISIONS. A row is never
updated or deleted: a later row for the same step is the newer answer.

The same (slug, step, date, value) is stored once, so the analyzer can re-post a row and
a seed can be re-run without stacking duplicates.

Not built yet (left for later, by plan): FileMaker export of the review date, backfill of
past dates from the vault's product folders, the formulation analyzer's hook.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timezone

from dashboard import db

STEPS = (
    ("review", "Review"),
    ("decision", "Decision"),
    ("app_formula_updated", "App formula updated"),
    ("filemaker_backup", "FileMaker backup copy"),
    ("label_printed", "Label printed"),
    ("store_page_updated", "Store page updated"),
)
STEP_KEYS = tuple(k for k, _ in STEPS)
DECISIONS = ("approved", "approved with changes", "kept as is", "waiting")

_CATALOG_FN = None
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def set_catalog_fn(fn):
    """app.py registers its in-memory catalog ({slug: product})."""
    global _CATALOG_FN
    _CATALOG_FN = fn


def _catalog(catalog=None) -> dict:
    if catalog is not None:
        return catalog
    if _CATALOG_FN is not None:
        return _CATALOG_FN() or {}
    return {}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── schema ──────────────────────────────────────────────────────────────────────

def init_tables(cx):
    """Callers run this first, as the stock routes do: the helpers below assume it."""
    # value: the decision ('' for every other step). NOT NULL so the unique index
    # treats two blank values as equal on both SQLite and Postgres.
    cx.execute("""CREATE TABLE IF NOT EXISTS formula_review_events (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        slug        TEXT NOT NULL,
        step        TEXT NOT NULL,
        event_date  TEXT NOT NULL,
        value       TEXT NOT NULL DEFAULT '',
        link        TEXT NOT NULL DEFAULT '',
        note        TEXT NOT NULL DEFAULT '',
        who         TEXT NOT NULL DEFAULT '',
        created_at  TEXT NOT NULL)""")
    cx.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_formula_review_events "
               "ON formula_review_events(slug, step, event_date, value)")
    cx.execute("CREATE INDEX IF NOT EXISTS ix_formula_review_events_slug "
               "ON formula_review_events(slug)")
    cx.commit()


# ── writes ──────────────────────────────────────────────────────────────────────

def validate(slug, step, event_date, value=None, catalog=None) -> dict:
    """The cleaned row, or ValueError with a line a person can act on."""
    slug = str(slug or "").strip()
    step = str(step or "").strip()
    event_date = str(event_date or "").strip()
    value = str(value or "").strip().lower()
    if not slug:
        raise ValueError("A product slug is needed.")
    if slug not in _catalog(catalog):
        raise ValueError(f"No product has the slug {slug!r}.")
    if step not in STEP_KEYS:
        raise ValueError("The step must be one of: " + ", ".join(STEP_KEYS) + ".")
    if not _DATE_RE.match(event_date):
        raise ValueError("The date must be written YYYY-MM-DD.")
    try:
        date.fromisoformat(event_date)
    except ValueError:
        raise ValueError(f"{event_date} is not a real date.") from None
    if step == "decision":
        if value not in DECISIONS:
            raise ValueError("A decision must be one of: " + ", ".join(DECISIONS) + ".")
    elif value:
        raise ValueError("Only the decision step carries a value.")
    return {"slug": slug, "step": step, "event_date": event_date, "value": value}


def add_event(cx, slug, step, event_date, value=None, link="", note="", who="",
              catalog=None) -> dict:
    """Add one row. Returns {"id", "created"}: created is False when the same
    (slug, step, date, value) was already there, and id is then the existing row's."""
    row = validate(slug, step, event_date, value, catalog=catalog)
    # RETURNING id, never cur.lastrowid: the Postgres adapter raises on lastrowid. The
    # translator turns INSERT OR IGNORE into ON CONFLICT DO NOTHING before RETURNING, so
    # a duplicate yields no row on either backend.
    cur = cx.execute(
        "INSERT OR IGNORE INTO formula_review_events "
        "(slug, step, event_date, value, link, note, who, created_at) "
        "VALUES (?,?,?,?,?,?,?,?) RETURNING id",
        (row["slug"], row["step"], row["event_date"], row["value"],
         str(link or "").strip(), str(note or "").strip(), str(who or "").strip(), _now()))
    got = cur.fetchone()
    cx.commit()
    if got:
        return {"id": int(got[0]), "created": True}
    old = cx.execute(
        "SELECT id FROM formula_review_events "
        "WHERE slug=? AND step=? AND event_date=? AND value=?",
        (row["slug"], row["step"], row["event_date"], row["value"])).fetchone()
    return {"id": int(old[0]) if old else None, "created": False}


# ── reads ───────────────────────────────────────────────────────────────────────

_COLS = ("id", "slug", "step", "event_date", "value", "link", "note", "who", "created_at")


def list_events(cx, slug=None) -> list[dict]:
    """Every row, newest date first, or one product's rows."""
    sql = "SELECT " + ", ".join(_COLS) + " FROM formula_review_events"
    args = ()
    if slug:
        sql += " WHERE slug=?"
        args = (str(slug).strip(),)
    sql += " ORDER BY event_date DESC, id DESC"
    return [dict(zip(_COLS, r)) for r in cx.execute(sql, args).fetchall()]


def overview(cx, catalog=None) -> dict:
    """The console page's data. Every product that is not inactive, with its last review
    date and the latest date for each step.

    `reviewed` is oldest review first, so what is due sits at the top. `never_reviewed`
    is its own group, by name. A row for a product that is inactive or gone from the
    catalog is kept in the table but not listed here."""
    cat = _catalog(catalog)
    latest = {}                                  # slug -> step -> event
    for ev in sorted(list_events(cx), key=lambda e: (e["event_date"], e["id"])):
        latest.setdefault(ev["slug"], {})[ev["step"]] = ev
    reviewed, never = [], []
    for slug, p in cat.items():
        if not isinstance(p, dict) or p.get("inactive"):
            continue
        name = str(p.get("name") or slug)
        steps = latest.get(slug, {})
        item = {"slug": slug, "name": name,
                "last_review": (steps.get("review") or {}).get("event_date"),
                "decision": (steps.get("decision") or {}).get("value") or None,
                "review_link": (steps.get("review") or {}).get("link") or None,
                "steps": {k: (steps.get(k) or {}).get("event_date") for k in STEP_KEYS}}
        (reviewed if item["last_review"] else never).append(item)
    reviewed.sort(key=lambda x: (x["last_review"], x["name"].lower()))
    never.sort(key=lambda x: x["name"].lower())
    return {"steps": [{"key": k, "label": label} for k, label in STEPS],
            "decisions": list(DECISIONS),
            "reviewed": reviewed, "never_reviewed": never}
