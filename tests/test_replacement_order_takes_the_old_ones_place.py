"""An invoice update that makes a new order replaces the old one, in place.

Glen, 2026-09-19: "When we make an update to an invoice, it makes a new order. It should
replace the old order functionally, even if it needs a new order number. That would
cancel the old order and put the new order into an existing household order as a
replacement." And: "old links should open the new invoice."

The case that forced it: Steve Fox's re-issued hand-off cancelled #183, created #195
outside combined shipment 9, and left #183 inside it. Michael Hill's #182 stayed grouped
with a cancelled order and there was no way to repair it from the board.
"""
import importlib
import sqlite3
import sys
from pathlib import Path

import pytest

from dashboard import rbac as _rbac

A, B, C = "michael@x.com", "steve@x.com", "third@x.com"
ITEM = "clear-lens-eye-drops"
ADDR = {"street": "1 Main St", "city": "Hilo", "state": "HI", "zip": "96720", "country": "US"}


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("COMBINED_SHIPMENTS_ENABLED", "1")
    repo = Path(__file__).resolve().parent.parent
    if str(repo) not in sys.path:
        sys.path.insert(0, str(repo))
    try:
        import app as appmod
        importlib.reload(appmod)
    except Exception as e:
        pytest.skip(f"app not importable: {e}")
    appmod.app.config["TESTING"] = True
    monkeypatch.setattr(appmod, "_bos_actor", lambda: _rbac.Actor(role="owner", name="glen"))
    monkeypatch.setattr(appmod, "_push_invoice_edit_to_qbo", lambda *a, **k: {"pushed": False})
    recomputed = []
    monkeypatch.setattr(appmod, "_recompute_combined_shipping",
                        lambda cx, sid: recomputed.append(sid) or {"ok": True})
    invites = []
    monkeypatch.setattr(appmod, "_hold_new_order_and_invite",
                        lambda cx, oid: invites.append(oid))
    db = str(tmp_path / "chat_log.db")
    from dashboard import orders as O, db as D
    with sqlite3.connect(db) as cx:
        O.init_orders_table(cx)
        if not D.column_exists(cx, "orders", "portal_published"):   # prod has it
            cx.execute("ALTER TABLE orders ADD COLUMN portal_published INTEGER DEFAULT 0")
        cx.commit()
    return appmod, appmod.app.test_client(), db, recomputed, invites


def _cx(db):
    cx = sqlite3.connect(db)
    cx.row_factory = sqlite3.Row
    return cx


def _order(db, oid):
    from dashboard import orders as O
    with _cx(db) as cx:
        return O.get_order(cx, oid)


def _handoff(client, email, name, *, replace=False):
    body = {"customer": {"email": email, "name": name, "address": ADDR},
            "lines": [{"slug": ITEM, "qty": 1}]}
    if replace:
        body["replace_open"] = True
    r = client.post("/api/orders/manual", json=body)
    assert r.status_code == 200, r.get_json()
    return r.get_json()["order_id"]


def _combine(db, ids):
    from dashboard import combined_shipments as S
    with _cx(db) as cx:
        S.init_combined_shipments_table(cx)
        return S.create_shipment(cx, ids)["id"]


def test_a_reissued_handoff_takes_its_predecessors_place_in_the_shipment(env):
    appmod, client, db, recomputed, invites = env
    a = _handoff(client, A, "Michael Hill")
    b = _handoff(client, B, "Steve Fox")
    sid = _combine(db, [a, b])
    b2 = _handoff(client, B, "Steve Fox", replace=True)
    old, new = _order(db, b), _order(db, b2)
    assert old["status"] == "cancelled" and old["superseded_by_order_id"] == b2
    assert old["group_shipment_id"] is None, "the cancelled order is still in the shipment"
    assert new["group_shipment_id"] == sid, "the replacement did not take its place"
    assert _order(db, a)["group_shipment_id"] == sid
    assert sid in recomputed


def test_a_replacement_joins_the_old_hold_group_with_no_new_invite(env):
    appmod, client, db, recomputed, invites = env
    from dashboard import household_holds as H, orders as O
    a = _handoff(client, A, "Michael Hill")
    with _cx(db) as cx:
        H.init_hold_tables(cx)
        gid = H.open_or_join_hold(cx, a, caregiver_email=A, household_key="hh")["group_id"]
    invites.clear()
    a2 = _handoff(client, A, "Michael Hill", replace=True)
    assert _order(db, a2)["hold_group_id"] == gid
    assert _order(db, a)["hold_group_id"] is None
    with _cx(db) as cx:
        assert cx.execute("SELECT status FROM household_holds WHERE id=?", (gid,)).fetchone()[0] == "open"
    assert invites == [], "a replacement must not open a new hold or send a new invite"


def test_cancelling_one_of_three_re_splits_the_shipping(env):
    appmod, client, db, recomputed, invites = env
    from dashboard import orders as O
    ids = [_handoff(client, e, n) for e, n in ((A, "M"), (B, "S"), (C, "T"))]
    sid = _combine(db, ids)
    recomputed.clear()
    with _cx(db) as cx:
        O.set_order_status(cx, ids[2], "cancelled")
    assert recomputed == [sid]
    assert _order(db, ids[0])["group_shipment_id"] == sid


def test_the_hold_release_adds_to_an_open_shipment_instead_of_failing(env):
    appmod, client, db, recomputed, invites = env
    a, b = _handoff(client, A, "M"), _handoff(client, B, "S")
    sid = _combine(db, [a, b])
    c = _handoff(client, C, "T")
    with _cx(db) as cx:
        got = appmod._release_to_shipment(cx, [a, b, c], created_by="test")
    assert got == sid and _order(db, c)["group_shipment_id"] == sid


def test_an_old_invoice_link_opens_the_newest_order(env):
    appmod, client, db, recomputed, invites = env
    from dashboard import practitioner_portal as PP
    b = _handoff(client, B, "Steve Fox")
    tok = PP.create_order_invoice_token(b)
    b2 = _handoff(client, B, "Steve Fox", replace=True)
    b3 = _handoff(client, B, "Steve Fox", replace=True)
    assert appmod._invoice_order_for_token(tok)["id"] == b3


def test_a_cancelled_unpaid_order_link_opens_nothing_but_a_paid_one_still_opens(env):
    appmod, client, db, recomputed, invites = env
    from dashboard import practitioner_portal as PP, orders as O
    x = _handoff(client, C, "T")
    tok = PP.create_order_invoice_token(x)
    with _cx(db) as cx:
        O.set_order_status(cx, x, "cancelled")
    assert appmod._invoice_order_for_token(tok) is None
    with _cx(db) as cx:
        cx.execute("UPDATE orders SET pay_status='paid' WHERE id=?", (x,))
        cx.commit()
    assert appmod._invoice_order_for_token(tok)["id"] == x
