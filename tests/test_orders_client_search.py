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


def test_an_order_number_shows_only_that_order(cx):
    # Glen, 2026-10-13: "203" buried order 203 under the client's 16 other orders.
    oid = cx.execute("SELECT id FROM orders WHERE external_ref='GK-1002'").fetchone()[0]
    for q in (str(oid), f"#{oid}", f" # {oid} "):
        rows = O.search_client_orders(cx, q)
        assert _refs(rows) == ["GK-1002"]
        assert rows[0]["search_match"] == "number" and rows[0]["search_replaced"] == []


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


# ── An order number shows one order (Glen, 2026-10-13) ────────────────────────
def _id(cx, ref):
    return cx.execute("SELECT id FROM orders WHERE external_ref=?", (ref,)).fetchone()[0]


def test_a_replaced_number_shows_the_order_that_replaced_it(cx):
    old, new = _id(cx, "GK-1001"), _id(cx, "GK-1002")
    O.supersede_order(cx, old, new)
    cx.commit()
    rows = O.search_client_orders(cx, str(old))
    assert _refs(rows) == ["GK-1002"] and rows[0]["search_replaced"] == [old]
    assert _refs(O.search_client_orders(cx, "GK-1001")) == []    # text search is unchanged


def test_a_chain_of_replacements_resolves_to_the_last(cx):
    a, b, c = _id(cx, "GK-1001"), _id(cx, "GK-1002"), _id(cx, "GK-1003")
    O.supersede_order(cx, a, b)
    O.supersede_order(cx, b, c)
    cx.commit()
    rows = O.search_client_orders(cx, f"#{a}")
    assert _refs(rows) == ["GK-1003"] and rows[0]["search_replaced"] == [a]


def test_a_replacement_loop_returns_nothing_rather_than_hanging(cx):
    a, b = _id(cx, "GK-1001"), _id(cx, "GK-1002")
    cx.execute("UPDATE orders SET superseded_by_order_id=? WHERE id=?", (b, a))
    cx.execute("UPDATE orders SET superseded_by_order_id=? WHERE id=?", (a, b))
    cx.commit()
    assert O.search_client_orders(cx, str(a)) == []


def test_a_name_still_brings_the_whole_history(cx):
    rows = O.search_client_orders(cx, "Ann Lee")
    assert _refs(rows) == ["GK-1001", "GK-1002", "GK-1003"]
    assert all("search_match" not in r for r in rows)


def test_digits_that_name_no_order_still_search_phones(cx):
    assert _refs(O.search_client_orders(cx, "8085550199")) == ["GK-1001", "GK-1002", "GK-1003"]
    assert O.search_client_orders(cx, "#8085550199") == []          # a # means an order number


NUM_JS = r"""
const assert = require('assert');
function esc(s){ return String(s==null?'':s).replace(/[&<>"']/g,function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c];}); }
BLOCK
assert.strictEqual(numberHitHtml({id: 1, email: 'a@x.com'}), '');
const hit = numberHitHtml({id: 225, search_match: 'number', search_replaced: [222], email: 'l"m@x.com'});
assert.ok(hit.includes('Replaced #222'), hit);
assert.ok(hit.includes('data-q="l&quot;m@x.com"'), hit);
assert.ok(hit.includes("All of this client's orders"), hit);
assert.ok(!numberHitHtml({search_match: 'number', search_replaced: [], email: 'a@x.com'}).includes('Replaced'));
assert.strictEqual(numberHitHtml({search_match: 'number', search_replaced: [], email: ''}), '');
assert.ok(searchNote('203', [{search_match: 'number'}]).startsWith('Order "'));
assert.ok(searchNote('Ann', [{}, {}]).startsWith('All 2 orders'));
assert.ok(searchNote('<b>', []).includes('&lt;b&gt;'));
console.log('OK');
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_card_names_the_replaced_number_and_links_the_history(tmp_path):
    page = (ROOT / "static" / "console-orders.html").read_text()
    a, b = page.find("// BEGIN order search"), page.find("// END order search")
    assert "numberHitHtml(o)" in page[page.find("function cardHtml("):]
    assert "window.searchFor = " in page
    js = tmp_path / "n.js"
    js.write_text(NUM_JS.replace("BLOCK", page[a:b]))
    out = subprocess.run(["node", str(js)], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0 and "OK" in out.stdout, out.stderr + out.stdout


def test_a_whole_address_matches_exactly(cx):
    # The card's history link searches by address (review round 1).
    O.upsert_order(cx, source="funnel", external_ref="JO-1", email="joann@x.com")
    O.upsert_order(cx, source="funnel", external_ref="US-1", email="annXx@x.com")
    cx.commit()
    assert _refs(O.search_client_orders(cx, "ann@x.com")) == ["GK-1001", "GK-1002", "GK-1003"]
    assert _refs(O.search_client_orders(cx, " ANN@X.COM ")) == ["GK-1001", "GK-1002", "GK-1003"]
    assert _refs(O.search_client_orders(cx, "ann@x")) == ["GK-1001", "GK-1002", "GK-1003", "JO-1"]


# ── The ship-to name (Glen, 2026-10-13) ───────────────────────────────────────
def test_a_ship_to_name_finds_that_order_alone(cx):
    # A drop-ship: the buyer is the practitioner, the recipient lives only in the address.
    O.upsert_order(cx, source="dropship", external_ref="DS-1", email="ann@x.com", name="Ann Lee",
                   address={"name": "Tony Profaci", "city": "Hazlet"})
    cx.commit()
    rows = O.search_client_orders(cx, "profaci")
    assert _refs(rows) == ["DS-1"] and rows[0]["search_match"] == "ship_to"
    assert _refs(O.search_client_orders(cx, "Tony P")) == ["DS-1"]
    assert O.search_client_orders(cx, "Hazlet") == []                 # the name, not the address


def test_a_buyer_match_keeps_the_whole_history_and_no_ship_to_mark(cx):
    O.upsert_order(cx, source="dropship", external_ref="DS-1", email="ann@x.com", name="Ann Lee",
                   address={"name": "Ann Lee"})
    cx.commit()
    rows = O.search_client_orders(cx, "Ann Lee")
    assert _refs(rows) == ["DS-1", "GK-1001", "GK-1002", "GK-1003"]
    assert all("search_match" not in r for r in rows)


def test_a_superseded_ship_to_match_stays_hidden(cx):
    O.upsert_order(cx, source="dropship", external_ref="DS-1", email="ann@x.com",
                   address={"name": "Tony Profaci"})
    cx.commit()
    O.supersede_order(cx, _id(cx, "DS-1"), _id(cx, "GK-2001"))
    cx.commit()
    assert O.search_client_orders(cx, "Profaci") == []


def test_an_accented_ship_to_name_is_found(cx):
    O.upsert_order(cx, source="dropship", external_ref="DS-2", email="bo@x.com",
                   address={"name": "Zoë Ångström"})
    cx.commit()
    assert _refs(O.search_client_orders(cx, "ångström")) == ["DS-2"]


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_a_ship_to_card_links_the_history(tmp_path):
    page = (ROOT / "static" / "console-orders.html").read_text()
    a, b = page.find("// BEGIN order search"), page.find("// END order search")
    js = tmp_path / "s.js"
    js.write_text(NUM_JS.split("BLOCK")[0] + page[a:b] + r"""
const h = numberHitHtml({id: 203, search_match: 'ship_to', email: 'a@x.com'});
assert.ok(h.includes("All of this client's orders") && !h.includes('Replaced'), h);
assert.ok(searchNote('Profaci', [{search_match: 'ship_to'}]).startsWith('Order shipped to'));
assert.ok(searchNote('x', [{search_match: 'ship_to'}, {}]).startsWith('All 2 orders'));
console.log('OK');
""")
    out = subprocess.run(["node", str(js)], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0 and "OK" in out.stdout, out.stderr + out.stdout
