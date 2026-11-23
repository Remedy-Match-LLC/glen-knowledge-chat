"""Rae's pay-link page and the invoice's Ships to block (Glen, 2026-11-22/23).

Rae types a name and sees that client's unpaid app orders, and their household's. Open
email writes Glen's approved message for her own mail. Nothing is sent from here. Mark
sent records the date only when Rae clicks it. The email and the invoice carry the
ship-to address, the pickup plan, or a request for an address.
"""
import json
import sqlite3

import pytest

from dashboard import orders as O
from dashboard import paylink as PL

ADDR = {"name": "Donna Banks", "street": "12 Kilauea Ave", "city": "Hilo",
        "state": "HI", "zip": "96720"}


# ── the email ────────────────────────────────────────────────────────────────

def _body(order, ship, owed=60000):
    return PL.email(order, "https://illtowell.com/invoice/T", owed, ship)


def test_email_is_glens_wording_with_the_address():
    ship = PL.ship_to({"address": ADDR}, 3)
    subject, body = _body({"name": "Donna Banks"}, ship)
    assert subject == "Your invoice from Remedy Match"
    assert body == (
        "Aloha Donna,\n\n"
        "Your invoice for $600.00 is ready.\n\n"
        "You can view and pay it here: https://illtowell.com/invoice/T\n\n"
        "The page takes a card, Zelle or Wise.\n\n"
        "We'll send your order to:\n\n"
        "Donna Banks\n12 Kilauea Ave\nHilo, HI 96720\n\n"
        "If it should go somewhere else, reply with the new address.\n\n"
        "We'll get your order moving as soon as your payment comes through.\n\n"
        "Mahalo,\n\nRae & Dr. Glen")


def test_pickup_names_riverbend_house():
    ship = PL.ship_to({"channel": "pickup", "address": ADDR}, 2)
    _, body = _body({"name": "Jo"}, ship)
    assert ("You can arrange to pick up your order at Riverbend House, "
            "351 Wailuku Drive in Hilo.") in body
    assert "We'll send your order to" not in body


def test_no_address_asks_for_one():
    ship = PL.ship_to({"address": {"name": "Jo"}}, 2)
    assert ship["kind"] == "missing"
    _, body = _body({"name": "Jo"}, ship)
    assert "Please reply with the address where you'd like your order sent." in body


def test_nothing_to_ship_leaves_the_address_and_order_lines_out():
    ship = PL.ship_to({"address": ADDR}, 0)
    assert ship == {"kind": "none", "lines": []}
    _, body = _body({"name": "Jo"}, ship)
    assert "address" not in body and "order moving" not in body
    assert body.endswith("Mahalo,\n\nRae & Dr. Glen")


def test_email_names_no_retired_destination():
    _, body = _body({"name": "Jo"}, PL.ship_to({"address": ADDR}, 1))
    for bad in ("practicebetter", "Practice Better", "l.bttr.to", "skool", "clientclub", "—"):
        assert bad.lower() not in body.lower()


def test_owed_amount_uses_thousands_separator():
    _, body = _body({"name": "Jo"}, {"kind": "none", "lines": []}, owed=123456)
    assert "$1,234.56" in body


# ── which orders show ────────────────────────────────────────────────────────

@pytest.fixture
def cx(tmp_path):
    c = sqlite3.connect(str(tmp_path / "chat_log.db"))
    c.row_factory = sqlite3.Row
    O.init_orders_table(c)
    c.execute("CREATE TABLE people (id INTEGER PRIMARY KEY, email TEXT, name TEXT, "
              "first_name TEXT, last_name TEXT, tags TEXT)")
    c.commit()
    yield c
    c.close()


def _order(cx, ref, email, name, status="proposed", total=5000, pay_status=None):
    oid = O.upsert_order(cx, source="in-house", external_ref=ref, email=email, name=name,
                         items=[{"slug": "x", "name": "X", "qty": 1}], total_cents=total,
                         status=status, address=ADDR)
    if pay_status:
        cx.execute("UPDATE orders SET pay_status=? WHERE id=?", (pay_status, oid))
        cx.commit()
    return oid


def test_find_shows_unpaid_open_orders_only(cx):
    keep = _order(cx, "a", "d@x.com", "Donna Banks")
    _order(cx, "b", "d@x.com", "Donna Banks", status="paid", pay_status="paid")
    _order(cx, "c", "d@x.com", "Donna Banks", status="cancelled")
    _order(cx, "d", "d@x.com", "Donna Banks", total=0)
    ids = [int(o["id"]) for o in PL.find(cx, "donna", O.search_client_orders)]
    assert ids == [keep]


def test_find_brings_in_the_household(cx):
    mine = _order(cx, "a", "sharon@x.com", "Sharon Connour")
    theirs = _order(cx, "b", "kid@x.com", "Kid Connour")
    _order(cx, "c", "stranger@x.com", "Someone Else")
    cx.execute("INSERT INTO people (email, name, tags) VALUES (?,?,?)",
               ("sharon@x.com", "Sharon Connour", json.dumps(["household:connour"])))
    cx.execute("INSERT INTO people (email, name, tags) VALUES (?,?,?)",
               ("kid@x.com", "Kid Connour", json.dumps(["household:connour"])))
    cx.execute("INSERT INTO people (email, name, tags) VALUES (?,?,?)",
               ("stranger@x.com", "Someone Else", json.dumps(["household:connourx"])))
    cx.commit()
    found = PL.find(cx, "sharon", O.search_client_orders)
    assert sorted(int(o["id"]) for o in found) == sorted([mine, theirs])
    assert [bool(o.get("household")) for o in found if int(o["id"]) == theirs] == [True]


def test_state_reads_sent_and_claimed():
    assert PL.state({}) == "not_sent"
    assert PL.state({"invoice_sent_at": "2026-11-23T01:00:00"}) == "sent"
    assert PL.state({"invoice_sent_at": "x", "pay_status": "claimed"}) == "claimed"


# ── the routes ───────────────────────────────────────────────────────────────

@pytest.fixture
def client(monkeypatch, tmp_path):
    import app as appmod
    import dashboard as _dashboard
    from dashboard import practitioner_portal as _pp
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setattr(appmod, "LOG_DB", str(tmp_path / "chat_log.db"))
    monkeypatch.setattr(appmod, "CONSOLE_SECRET", "test-secret")
    monkeypatch.setattr(_dashboard, "CONSOLE_SECRET", "test-secret")
    monkeypatch.setattr(_pp, "create_order_invoice_token", lambda oid, **k: "TOK")
    sent = []
    import dashboard.inbox as _inbox
    monkeypatch.setattr(_inbox, "send_email", lambda *a, **k: sent.append(a) or True)
    with sqlite3.connect(appmod.LOG_DB) as c:
        c.row_factory = sqlite3.Row
        O.init_orders_table(c)
        oid = _order(c, "a", "d@x.com", "Donna Banks", total=60000)
    appmod.app.config["TESTING"] = True
    cl = appmod.app.test_client()
    cl.oid, cl.sent, cl.db = oid, sent, appmod.LOG_DB
    return cl


H = {"X-Console-Key": "test-secret"}


def test_routes_need_the_console_key(client):
    assert client.get("/api/console/paylink/search?q=donna").status_code == 401
    assert client.post(f"/api/console/paylink/{client.oid}/draft").status_code == 401
    assert client.post(f"/api/console/paylink/{client.oid}/sent").status_code == 401


def test_search_shows_no_token_and_draft_sends_nothing(client):
    r = client.get("/api/console/paylink/search?q=donna", headers=H).get_json()
    assert [x["name"] for x in r["rows"]] == ["Donna Banks"]
    assert r["rows"][0]["owed_cents"] == 60000 and r["rows"][0]["state"] == "not_sent"
    assert "TOK" not in json.dumps(r)
    d = client.post(f"/api/console/paylink/{client.oid}/draft", headers=H).get_json()
    assert d["ok"] and d["to"] == "d@x.com"
    assert d["subject"] == "Your invoice from Remedy Match"
    assert "/invoice/TOK" in d["body"] and "print=1" not in d["body"]
    assert client.sent == []
    with sqlite3.connect(client.db) as c:      # opening the draft does not mark it sent
        assert c.execute("SELECT invoice_sent_at FROM orders WHERE id=?",
                         (client.oid,)).fetchone()[0] in (None, "")


def test_mark_sent_records_the_date(client):
    assert client.post(f"/api/console/paylink/{client.oid}/sent", headers=H).status_code == 200
    r = client.get("/api/console/paylink/search?q=donna", headers=H).get_json()
    assert r["rows"][0]["state"] == "sent" and r["rows"][0]["sent_on"]
    assert client.post("/api/console/paylink/999999/sent", headers=H).status_code == 404


def test_invoice_shows_ships_to_with_only_kind_and_lines(client, monkeypatch):
    import app as appmod
    monkeypatch.setattr(appmod, "_order_physical_units", lambda o: 1)
    with sqlite3.connect(client.db) as c:
        c.row_factory = sqlite3.Row
        order = O.get_order(c, client.oid)
    st = appmod._invoice_summary(order)["ship_to"]
    assert st == {"kind": "ship", "lines": ["Donna Banks", "12 Kilauea Ave", "Hilo, HI 96720"]}


def test_invoice_page_and_nav_render_ships_to_and_pay_link():
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    inv = (root / "static" / "invoice.html").read_text()
    assert "function shipToText(st)" in inv and 'id="shipto"' in inv
    assert "shipToText(o.ship_to)" in inv           # the printed invoice too
    assert "We need your shipping address. Please reply to our email with it." in inv
    nav = (root / "static" / "op-nav.js").read_text()
    assert 'href:"/console/paylink"+qs' in nav
    page = (root / "static" / "console-paylink.html").read_text()
    assert "mailto:" in page and "Mark sent" in page


def test_ships_to_text_runs_in_the_page():
    import shutil, subprocess
    from pathlib import Path
    node = shutil.which("node")
    if not node:
        pytest.skip("node not installed")
    src = (Path(__file__).resolve().parent.parent / "static" / "invoice.html").read_text()
    start = src.index("function shipToText(")
    end = src.index("\n}\n", start) + 2
    js = src[start:end] + "\nconsole.log(JSON.stringify([" + ",".join(
        "shipToText(%s)" % json.dumps(x) for x in (
            {"kind": "ship", "lines": ["Jo", "1 A St", "Hilo, HI 96720"]},
            {"kind": "pickup", "lines": [PL.PICKUP_PLACE]},
            {"kind": "missing", "lines": []},
            {"kind": "none", "lines": []})) + "]));"
    out = json.loads(subprocess.run([node, "-e", js], capture_output=True, text=True,
                                    timeout=30, check=True).stdout)
    assert out[0] == {"k": "Ships to", "v": "Jo\n1 A St\nHilo, HI 96720"}
    assert out[1]["v"] == ("You can arrange to pick up your order at Riverbend House, "
                           "351 Wailuku Drive in Hilo.")
    assert out[2]["v"] == "We need your shipping address. Please reply to our email with it."
    assert out[3] is None
