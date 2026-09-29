"""Console, Sell, Orders: a search box that shows ALL of one client's orders.

Glen, 2026-09-28 (via the primary): a search box at the top right to view a particular
client's cards, matching name, email, phone and order number; "show all that client's orders",
cancelled and completed included; clearing it restores the normal board. The board loads the
newest 2,000 without cancelled ones, so the search runs on the server, not on that set."""
import os
import shutil
import sqlite3
import subprocess
from pathlib import Path

import pytest

from dashboard import orders as O

ROOT = Path(__file__).resolve().parent.parent


def _app():
    import importlib
    import sys
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    try:
        return importlib.import_module("app")
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"app not importable in this env: {e}")


@pytest.fixture
def cx():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    O.init_orders_table(c)
    O.upsert_order(c, source="funnel", external_ref="GK-1001", email="ann@x.com", name="Ann Lee",
                   phone="(808) 555-0199")
    O.upsert_order(c, source="funnel", external_ref="GK-1002", email="ANN@x.com", name="A. Lee")
    O.upsert_order(c, source="funnel", external_ref="GK-1003", email="ann@x.com", status="cancelled")
    O.upsert_order(c, source="funnel", external_ref="GK-2001", email="bo@x.com", name="Bo Kim")
    c.commit()
    return c


def _refs(rows):
    return sorted(r["external_ref"] for r in rows)


@pytest.mark.parametrize("q", ["Ann Lee", "ann@x", "808-555-0199", "5550199", "GK-1001"])
def test_a_search_returns_every_order_for_that_client(cx, q):
    assert _refs(O.search_client_orders(cx, q)) == ["GK-1001", "GK-1002", "GK-1003"]


def test_an_order_number_finds_its_client(cx):
    oid = cx.execute("SELECT id FROM orders WHERE external_ref='GK-1002'").fetchone()[0]
    for q in (str(oid), f"#{oid}"):
        assert _refs(O.search_client_orders(cx, q)) == ["GK-1001", "GK-1002", "GK-1003"]


def test_a_search_never_brings_in_another_client(cx):
    assert _refs(O.search_client_orders(cx, "Bo")) == ["GK-2001"]


def test_a_too_short_search_returns_nothing(cx):
    assert O.search_client_orders(cx, "a") == []
    assert O.search_client_orders(cx, "  ") == []


def test_old_orders_beyond_the_board_limit_are_included(cx):
    for i in range(30):
        O.upsert_order(cx, source="funnel", external_ref=f"X-{i}", email="bulk@x.com")
    O.upsert_order(cx, source="funnel", external_ref="GK-1004", email="ann@x.com")
    cx.commit()
    assert "GK-1004" in _refs(O.search_client_orders(cx, "ann@x.com"))
    assert len(O.list_orders(cx, limit=10)) == 10           # the board's own limit


def test_the_route_serves_the_search(monkeypatch, tmp_path):
    app = _app()
    db = str(tmp_path / "o.db")
    monkeypatch.setattr(app, "LOG_DB", db)
    c = sqlite3.connect(db)
    c.row_factory = sqlite3.Row
    O.init_orders_table(c)
    O.upsert_order(c, source="funnel", external_ref="R1", email="cy@x.com", name="Cy Doe")
    O.upsert_order(c, source="funnel", external_ref="R2", email="cy@x.com", status="cancelled")
    O.upsert_order(c, source="funnel", external_ref="R3", email="zed@x.com")
    c.commit()
    c.close()
    key = app.dashboard.CONSOLE_SECRET or ""
    r = app.app.test_client().get("/api/orders?q=cy%20doe", headers={"X-Console-Key": key})
    assert r.status_code == 200
    assert sorted(o["external_ref"] for o in r.get_json()["data"]) == ["R1", "R2"]


# ── The page ──────────────────────────────────────────────────────────────────
JS = r"""
const assert = require('assert');
BLOCK
assert.strictEqual(ordersUrl(''), '/api/orders?limit=2000');
assert.strictEqual(ordersUrl('  '), '/api/orders?limit=2000');
assert.strictEqual(ordersUrl('Ann Lee'), '/api/orders?q=Ann%20Lee&limit=500');
const lanes = searchLanes([["done", "Done"]], 'ann');
assert.deepStrictEqual(lanes.map(l => l[0]), ["done", "cancelled"]);
assert.deepStrictEqual(searchLanes([["done", "Done"]], '').map(l => l[0]), ["done"]);
console.log('OK');
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_page_searches_on_the_server_and_shows_cancelled(tmp_path):
    page = (ROOT / "static" / "console-orders.html").read_text()
    a, b = page.find("// BEGIN order search"), page.find("// END order search")
    assert a != -1 and b > a
    assert 'id="order-search"' in page
    js = tmp_path / "s.js"
    js.write_text(JS.replace("BLOCK", page[a:b]))
    out = subprocess.run(["node", str(js)], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0 and "OK" in out.stdout, out.stderr + out.stdout


# ── Review ────────────────────────────────────────────────────────────────────
def test_a_long_number_matches_exactly_never_inside_another_number(cx):
    """A GrooveKart ref '5123' must not pull in order #15123 or a phone containing 5123."""
    O.upsert_order(cx, source="groovekart", external_ref="5123", email="gk@x.com")
    O.upsert_order(cx, source="funnel", external_ref="GK-51230", email="other@x.com",
                   phone="(808) 555-1234")
    cx.commit()
    assert _refs(O.search_client_orders(cx, "5123")) == ["5123"]


def test_a_phone_needs_seven_digits_and_ignores_a_country_code(cx):
    assert _refs(O.search_client_orders(cx, "+1 808 555 0199")) == ["GK-1001", "GK-1002", "GK-1003"]
    assert O.search_client_orders(cx, "0199") == []


def test_a_name_known_only_from_people_is_found(cx):
    """The board shows a name from People when the order has none; search must find it."""
    cx.execute("CREATE TABLE IF NOT EXISTS people (id INTEGER PRIMARY KEY, email TEXT, name TEXT, "
               "first_name TEXT, last_name TEXT)")
    cx.execute("INSERT INTO people (email, name) VALUES ('bo@x.com', 'Bohdan Kimura')")
    cx.commit()
    assert _refs(O.search_client_orders(cx, "kimura")) == ["GK-2001"]


PAGE_RACE = r"""
const assert = require('assert');
BLOCK
let t = beginSearch(); let u = beginSearch();
assert.strictEqual(isCurrent(t), false);     // a slower, older response is dropped
assert.strictEqual(isCurrent(u), true);
console.log('OK');
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_an_older_response_never_overwrites_a_newer_search(tmp_path):
    page = (ROOT / "static" / "console-orders.html").read_text()
    a, b = page.find("// BEGIN order search"), page.find("// END order search")
    js = tmp_path / "r.js"
    js.write_text(PAGE_RACE.replace("BLOCK", page[a:b]))
    out = subprocess.run(["node", str(js)], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0 and "OK" in out.stdout, out.stderr + out.stdout
    assert "if (!isCurrent(my)) return;" in page
