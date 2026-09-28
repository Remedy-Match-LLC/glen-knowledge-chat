"""Focus Neuro-Magnesium Powder is the stocked jar; neuro-magnesium is the Founding Batch presale.

Production's store correction (production/05 Formulations/neuro-magnesium/2026-09-28/
store-correction.json), approved by Glen 2026-09-28 ("approve"). The stocked jar's references
pointed at the presale listing; they move to focus-neuro-magnesium-powder. The presale is not
touched."""
import json
import sqlite3

from dashboard import biofield_portal_publish as bpp
from dashboard import client_portal as cp
from dashboard import portal_biofield_reports as pbr

FOCUS, PRESALE = "focus-neuro-magnesium-powder", "neuro-magnesium"


def test_filemaker_431_is_the_stocked_jar():
    assert json.load(open("data/fmp_slug_map.json"))["resolved"]["431"] == FOCUS


def test_the_catalog_carries_the_label_and_leaves_the_presale_alone():
    import subprocess
    prods = json.load(open("data/products.json"))["products"]
    p = prods[FOCUS]
    assert p["name"] == "Focus Neuro-Magnesium Powder" and p["fmp_id"] == "431"
    assert p["ingredients"][0] == {"name": "Lithium Orotate", "dose": p["ingredients"][0]["dose"]}
    assert all(i["dose"] for i in p["ingredients"])
    for k in ("notes", "routing_work", "apply_as"):
        assert k not in p
    was = json.loads(subprocess.run(["git", "show", "origin/main:data/products.json"],
                                    capture_output=True, text=True).stdout)["products"]
    assert prods[PRESALE] == was[PRESALE]
    for k in ("price_cents", "regular_cents", "qty_pricing", "bottle_type", "no_groovekart",
              "pinecone_title"):
        assert p.get(k) == was[FOCUS].get(k), k
    assert json.load(open("data/products-manual-corrections.json"))[FOCUS]["ingredients"] == p["ingredients"]


def _content(remedies, slugs):
    return {"layers": [{"n": i + 1, "remedy": r} for i, r in enumerate(remedies)],
            "reorder_items": [{"slug": s, "qty": 1, "price_cents": 5000} for s in slugs]}


def _db():
    cx = sqlite3.connect(":memory:")
    cx.row_factory = sqlite3.Row
    pbr.init_table(cx)
    cp.init_client_portal_table(cx)
    rows = {
        "focus@x.com": _content(["Vitality", "Focus, Neuromagnesium"], ["vitality", PRESALE]),
        "presale@x.com": _content(["Neuro Magnesium"], [PRESALE]),
        "both@x.com": _content(["Neuro Magnesium", "Focus Neuro-Magnesium"], [PRESALE]),
        "plus@x.com": _content(["Chelation + Focus Neuro-Magnesium"], ["chelation", PRESALE]),
    }
    for email, c in rows.items():
        cx.execute("INSERT INTO portal_biofield_reports (email, scan_date, content_json, status) "
                   "VALUES (?, '2026-09-01', ?, 'published')", (email, json.dumps(c)))
    cp.upsert_portal(cx, "portal@x.com", "P", _content(["Focus Neuro-Magnesium"], [PRESALE]))
    cx.commit()
    return cx


def _slugs(cx, table, email):
    raw = cx.execute(f"SELECT content_json FROM {table} WHERE email=?", (email,)).fetchone()[0]
    return [i["slug"] for i in json.loads(raw)["reorder_items"]]


def test_only_focus_rows_move_to_the_stocked_jar():
    cx = _db()
    assert bpp.rehome_focus_reorder_items(cx) == 3
    assert _slugs(cx, "portal_biofield_reports", "focus@x.com") == ["vitality", FOCUS]
    assert _slugs(cx, "portal_biofield_reports", "plus@x.com") == ["chelation", FOCUS]
    assert _slugs(cx, "client_portals", "portal@x.com") == [FOCUS]
    assert _slugs(cx, "portal_biofield_reports", "presale@x.com") == [PRESALE]   # its own name
    assert _slugs(cx, "portal_biofield_reports", "both@x.com") == [PRESALE]      # ambiguous: left
    assert bpp.rehome_focus_reorder_items(cx) == 0                                # idempotent


def test_a_report_holding_both_lines_is_left_alone():
    """Round 2: merging two lines guessed at quantities; both lines may be real."""
    cx = sqlite3.connect(":memory:")
    pbr.init_table(cx)
    c = _content(["Focus Neuro-Magnesium"], [PRESALE, FOCUS])
    cx.execute("INSERT INTO portal_biofield_reports (email, scan_date, content_json) VALUES "
               "('j@x.com', '2026-09-01', ?)", (json.dumps(c),))
    assert bpp.rehome_focus_reorder_items(cx) == 0
    assert _slugs(cx, "portal_biofield_reports", "j@x.com") == [PRESALE, FOCUS]


def test_any_presale_spelling_leaves_the_report_alone():
    cx = sqlite3.connect(":memory:")
    pbr.init_table(cx)
    c = _content(["Focus Neuro-Magnesium", "Neuro Magnesium (presale)"], [PRESALE])
    cx.execute("INSERT INTO portal_biofield_reports (email, scan_date, content_json) VALUES "
               "('v@x.com', '2026-09-01', ?)", (json.dumps(c),))
    assert bpp.rehome_focus_reorder_items(cx) == 0


def test_a_malformed_report_does_not_stop_the_rest():
    cx = sqlite3.connect(":memory:")
    pbr.init_table(cx)
    cx.execute("INSERT INTO portal_biofield_reports (email, scan_date, content_json) VALUES "
               "('bad@x.com', '2026-08-01', ?)", ('{"reorder_items": ["neuro-magnesium"',))  # broken JSON
    cx.execute("INSERT INTO portal_biofield_reports (email, scan_date, content_json) VALUES "
               "('odd@x.com', '2026-08-02', ?)", ('["neuro-magnesium"]',))   # valid, not a dict
    cx.execute("INSERT INTO portal_biofield_reports (email, scan_date, content_json) VALUES "
               "('ok@x.com', '2026-09-01', ?)",
               (json.dumps(_content(["Focus Neuro-Magnesium"], [PRESALE])),))
    assert bpp.rehome_focus_reorder_items(cx) == 1
    assert _slugs(cx, "portal_biofield_reports", "ok@x.com") == [FOCUS]


def test_a_report_changed_since_it_was_read_is_not_overwritten(monkeypatch):
    """Round 2: another worker's newer save must win."""
    cx = sqlite3.connect(":memory:")
    pbr.init_table(cx)
    cx.execute("INSERT INTO portal_biofield_reports (email, scan_date, content_json) VALUES "
               "('r@x.com', '2026-09-01', ?)", (json.dumps(_content(["Focus Neuro-Magnesium"], [PRESALE])),))
    newer = json.dumps(_content(["Focus Neuro-Magnesium", "Vitality"], [PRESALE, "vitality"]))
    real = bpp._rehome_one

    def race(c):
        cx.execute("UPDATE portal_biofield_reports SET content_json=?", (newer,))
        return real(c)
    monkeypatch.setattr(bpp, "_rehome_one", race)
    assert bpp.rehome_focus_reorder_items(cx) == 0
    assert cx.execute("SELECT content_json FROM portal_biofield_reports").fetchone()[0] == newer


def test_the_app_moves_them_at_startup(monkeypatch, tmp_path):
    import os
    os.environ.setdefault("PINECONE_API_KEY", "pc-dummy")
    os.environ.setdefault("OPENAI_API_KEY", "sk-dummy")
    import app
    db = str(tmp_path / "chat_log.db")
    cx = sqlite3.connect(db)
    pbr.init_table(cx)
    cx.execute("INSERT INTO portal_biofield_reports (email, scan_date, content_json) VALUES "
               "('s@x.com', '2026-09-01', ?)", (json.dumps(_content(["Focus Neuro-Magnesium"], [PRESALE])),))
    cx.commit()
    cx.close()
    monkeypatch.setattr(app, "LOG_DB", db)
    app._rehome_focus_reorder_at_startup()
    assert _slugs(sqlite3.connect(db), "portal_biofield_reports", "s@x.com") == [FOCUS]



def _pg_ok():
    import os
    dsn = os.environ.get("PG_DSN", "")
    return bool(dsn) and "test" in dsn          # never a real database


import pytest  # noqa: E402


@pytest.mark.skipif(not _pg_ok(), reason="PG_DSN for a test database not set")
def test_it_moves_the_row_on_postgres(monkeypatch):
    """Round 1: production is Postgres, which has no rowid; the fix did nothing there."""
    from dashboard import db
    monkeypatch.setenv("DB_BACKEND", "postgres")
    c = db.connect("/data/focus_rehome_test.db")
    try:
        for t in ("portal_biofield_reports", "client_portals"):
            c.execute(f'DROP TABLE IF EXISTS "{t}" CASCADE')
        c.commit()
        pbr.init_table(c)
        cp.init_client_portal_table(c)
        c.execute("INSERT INTO portal_biofield_reports (email, scan_date, content_json) VALUES "
                  "('pg@x.com', '2026-09-01', ?)", (json.dumps(_content(["Focus Neuro-Magnesium"], [PRESALE])),))
        c.execute("INSERT INTO portal_biofield_reports (email, scan_date, content_json) VALUES "
                  "('pp@x.com', '2026-09-01', ?)", (json.dumps(_content(["Neuro Magnesium"], [PRESALE])),))
        c.commit()
        assert bpp.rehome_focus_reorder_items(c) == 1
        got = {r[0]: [i["slug"] for i in json.loads(r[1])["reorder_items"]]
               for r in c.execute("SELECT email, content_json FROM portal_biofield_reports").fetchall()}
        assert got == {"pg@x.com": [FOCUS], "pp@x.com": [PRESALE]}
    finally:
        c.rollback()
        for t in ("portal_biofield_reports", "client_portals"):
            c.execute(f'DROP TABLE IF EXISTS "{t}" CASCADE')
        c.commit()
