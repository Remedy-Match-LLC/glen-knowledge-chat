# tests/test_pg_cursor_parity.py
"""`cx.cursor()` must work on both backends.

Journey review finding 12. `/learn`, `/learn/sitemap.xml`, `/learn/<slug>` and
`/mentors` all returned 500 in production -- the SEO content hub and its sitemap,
so those pages were neither reachable nor crawlable.

Reproduced against a real local Postgres:

    File "dashboard/topic_pages.py", line 154, in list_pages
        cur = cx.cursor()
    AttributeError: '_PgConn' object has no attribute 'cursor'

`cx.cursor()` is ordinary sqlite3, and the Postgres adapter exposed
execute/executemany/executescript but never `cursor()`. It is not one page:
**26 call sites across seven modules** use the idiom -- topic_pages,
mentor_pages, ingredient_pages, product_reviews, review_gifts, biofield_reveals.
Every one of them was broken on Postgres, which is prod.

Fixed at the ADAPTER rather than at 26 call sites: `_PgConn.execute` was already
constructing a `_PgCursor` internally, so `cursor()` just returns one.
"""

import sqlite3

import pytest

from dashboard import db


def _sqlite():
    cx = sqlite3.connect(":memory:")
    cx.execute("CREATE TABLE t (slug TEXT, state TEXT)")
    cx.execute("INSERT INTO t VALUES ('a','approved')")
    cx.execute("INSERT INTO t VALUES ('b','draft')")
    cx.commit()
    return cx


def test_the_adapter_exposes_cursor():
    """The regression itself: the attribute simply was not there."""
    assert hasattr(db._PgConn, "cursor"), (
        "_PgConn.cursor() is gone again; every cx.cursor() caller 500s on Postgres")


def test_cursor_returns_the_adapters_cursor_type():
    import inspect
    src = inspect.getsource(db._PgConn.cursor)
    assert "_PgCursor" in src, "cursor() must return the translating cursor, not a raw one"


def test_sqlite_supports_the_same_idiom():
    """Parity baseline: this is what the callers were written against."""
    cx = _sqlite()
    cur = cx.cursor()
    cur.row_factory = sqlite3.Row
    rows = cur.execute("SELECT slug, state FROM t ORDER BY slug").fetchall()
    assert [r["state"] for r in rows] == ["approved", "draft"]


def test_the_callers_still_use_the_idiom():
    """If these were rewritten to cx.execute(), this test is obsolete -- but while
    they exist, the adapter must support them."""
    import pathlib
    repo = pathlib.Path(__file__).resolve().parent.parent
    hits = []
    for mod in ("topic_pages", "mentor_pages", "ingredient_pages",
                "product_reviews", "review_gifts", "biofield_reveals"):
        p = repo / "dashboard" / f"{mod}.py"
        if p.exists() and "cx.cursor()" in p.read_text(encoding="utf-8"):
            hits.append(mod)
    assert hits, "no caller uses cx.cursor(); this guard may be retired"


# ---------------------------------------------------------------------------
# Against a real Postgres when one is available
# ---------------------------------------------------------------------------

def _pg_dsn():
    import os
    return os.environ.get("TEST_PG_DSN")


@pytest.mark.skipif(not _pg_dsn(), reason="TEST_PG_DSN not set")
def test_cursor_roundtrip_on_real_postgres():
    import os
    os.environ["PG_DSN"] = _pg_dsn()
    os.environ["DB_BACKEND"] = "postgres"
    cx = db.connect("chat_log.db")
    try:
        cx.execute("CREATE TABLE IF NOT EXISTS t_parity (slug TEXT, state TEXT)")
        cx.execute("DELETE FROM t_parity")
        cx.execute("INSERT INTO t_parity VALUES (?,?)", ("a", "approved"))
        cx.commit()
        cur = cx.cursor()
        cur.row_factory = sqlite3.Row          # harmless on PG, kept for parity
        rows = cur.execute("SELECT slug, state FROM t_parity").fetchall()
        assert [r["state"] for r in rows] == ["approved"]
        assert rows[0]["slug"] == "a"          # string-key access must survive
    finally:
        cx.close()
