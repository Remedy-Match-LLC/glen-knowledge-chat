"""Bill with caregiver: a member's invoice lines go onto the caregiver's order.

Glen, 2026-09-19, on Sharon and Hershey Connour, then yes to four rules the same day:
fee lines move too; re-billing replaces the member's lines; a new invoice opens when the
caregiver has none open; whole-order quantity pricing only with a family plan.
"""
import importlib
import json
import sqlite3
import sys
from pathlib import Path

import pytest

from dashboard import rbac as _rbac

CARER, PET = "sharon@x.com", "hershey@x.com"
FF = "clear-lens-eye-drops"          # a formulation: quantity pricing applies
INFO = "mb5-esr-emotional-stress-release-infoceutical"


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    db = str(tmp_path / "chat_log.db")
    from dashboard import orders as O, household as H
    with sqlite3.connect(db) as cx:
        O.init_orders_table(cx)
        H.init_household_tables(cx)
        H.add_member(cx, CARER, PET, label="Hershey", relationship="pet")
        cx.commit()
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
    # Shipping is not under test; a flat quote keeps it secretless and deterministic.
    monkeypatch.setattr(appmod, "_hold_new_order_and_invite", lambda *a, **k: None, raising=False)
    return appmod, appmod.app.test_client(), db


def _orders(db):
    from dashboard import orders as O
    with sqlite3.connect(db) as cx:
        cx.row_factory = sqlite3.Row
        ids = [r[0] for r in cx.execute("SELECT id FROM orders ORDER BY id")]
        return {i: O.get_order(cx, i) for i in ids}


def _manual(client, email, name, lines, **extra):
    return client.post("/api/orders/manual",
                       json={"customer": {"email": email, "name": name}, "lines": lines, **extra})


def _slugs(o, member=None):
    return sorted(i["slug"] for i in o["items"]
                  if member is None or i.get("billed_for") == member)


def test_board_moves_a_pet_order_onto_the_carers_open_invoice(env):
    appmod, client, db = env
    carer = _manual(client, CARER, "Sharon Connour", [{"slug": FF, "qty": 1}]).get_json()
    pet = _manual(client, PET, "Hershey Connour", [{"slug": INFO, "qty": 1}]).get_json()
    r = client.post(f"/api/orders/{pet['order_id']}/bill-with-caregiver", json={})
    body = r.get_json()
    assert r.status_code == 200, body
    assert body["order_id"] == carer["order_id"] and body["created"] is False
    os_ = _orders(db)
    target, old = os_[carer["order_id"]], os_[pet["order_id"]]
    assert _slugs(target) == sorted([FF, INFO])
    moved = [i for i in target["items"] if i["slug"] == INFO][0]
    assert moved["billed_for"] == PET and moved["note"] == "For Hershey"
    assert old["status"] == "cancelled" and old["superseded_by_order_id"] == carer["order_id"]


def test_the_choice_is_remembered_and_a_new_raise_goes_to_the_carer(env):
    appmod, client, db = env
    carer = _manual(client, CARER, "Sharon Connour", [{"slug": FF, "qty": 1}]).get_json()
    pet = _manual(client, PET, "Hershey Connour", [{"slug": INFO, "qty": 1}]).get_json()
    client.post(f"/api/orders/{pet['order_id']}/bill-with-caregiver", json={})
    # The Biofield panel raises Hershey's invoice again, with a changed remedy set.
    r = _manual(client, PET, "Hershey Connour", [{"slug": FF, "qty": 2}],
                update_order_id=pet["order_id"]).get_json()
    assert r["billed_to_caregiver"] is True and r["order_id"] == carer["order_id"]
    target = _orders(db)[carer["order_id"]]
    # Replaced, not added: only the new pet line remains for Hershey.
    assert [(i["slug"], i["qty"]) for i in target["items"] if i.get("billed_for") == PET] == [(FF, 2)]
    assert [i["slug"] for i in target["items"] if not i.get("billed_for")] == [FF]


def test_with_no_open_carer_invoice_a_new_one_opens_in_her_name(env):
    appmod, client, db = env
    pet = _manual(client, PET, "Hershey Connour", [{"slug": INFO, "qty": 1}]).get_json()
    r = client.post(f"/api/orders/{pet['order_id']}/bill-with-caregiver", json={}).get_json()
    assert r["ok"] and r["created"] is True and r["order_id"] != pet["order_id"]
    new = _orders(db)[r["order_id"]]
    assert new["email"] == CARER and new["status"] == "proposed"
    assert _slugs(new, PET) == [INFO]


def test_without_a_family_plan_the_pet_line_keeps_its_own_price(env):
    appmod, client, db = env
    solo = _manual(client, "someone@x.com", "S", [{"slug": FF, "qty": 1}]).get_json()
    solo_unit = solo["lines"][0]["unit_cents"]
    carer = _manual(client, CARER, "Sharon Connour", [{"slug": FF, "qty": 3}]).get_json()
    pet = _manual(client, PET, "Hershey Connour", [{"slug": FF, "qty": 1}]).get_json()
    client.post(f"/api/orders/{pet['order_id']}/bill-with-caregiver", json={})
    line = [i for i in _orders(db)[carer["order_id"]]["items"] if i.get("billed_for") == PET][0]
    assert line["unit_cents"] == solo_unit and line.get("override") is True


def test_an_adult_without_pay_consent_cannot_be_billed_to_another(env):
    appmod, client, db = env
    from dashboard import household as H
    with sqlite3.connect(db) as cx:
        H.add_member(cx, CARER, "adult@x.com", label="Pat", relationship="partner")
        cx.commit()
    o = _manual(client, "adult@x.com", "Pat", [{"slug": INFO, "qty": 1}]).get_json()
    r = client.post(f"/api/orders/{o['order_id']}/bill-with-caregiver", json={})
    assert r.status_code == 400 and r.get_json()["caregivers"] == []
    assert _orders(db)[o["order_id"]]["status"] == "proposed"


def test_a_paid_order_does_not_move(env):
    appmod, client, db = env
    pet = _manual(client, PET, "Hershey Connour", [{"slug": INFO, "qty": 1}]).get_json()
    with sqlite3.connect(db) as cx:
        cx.execute("UPDATE orders SET pay_status='paid' WHERE id=?", (pet["order_id"],))
        cx.commit()
    r = client.post(f"/api/orders/{pet['order_id']}/bill-with-caregiver", json={})
    assert r.status_code == 409


def test_the_panel_control_reads_and_sets_the_preference(env):
    appmod, client, db = env
    r = client.get(f"/api/console/caregiver-billing?email={PET}").get_json()
    assert r["caregivers"] == [CARER] and r["remembered"] is None
    r = client.post("/api/console/caregiver-billing",
                    json={"email": PET, "caregiver_email": CARER, "on": True}).get_json()
    assert r["remembered"] == CARER
    r = client.post("/api/console/caregiver-billing",
                    json={"email": PET, "caregiver_email": CARER, "on": False}).get_json()
    assert r["remembered"] is None


def test_with_a_family_plan_the_pet_line_prices_with_the_whole_order(env):
    """Glen: whole-order quantity pricing "only when caregiver is a family level member"."""
    appmod, client, db = env
    from dashboard import family_plan as F
    with sqlite3.connect(db) as cx:
        F.init_family_plan_table(cx)
        F.activate(cx, CARER, next_charge_at="2099-01-01")
        cx.commit()
    carer = _manual(client, CARER, "Sharon Connour", [{"slug": FF, "qty": 3}]).get_json()
    pet = _manual(client, PET, "Hershey Connour", [{"slug": FF, "qty": 1}]).get_json()
    r = client.post(f"/api/orders/{pet['order_id']}/bill-with-caregiver", json={}).get_json()
    assert r["family_level"] is True
    line = [i for i in _orders(db)[carer["order_id"]]["items"] if i.get("billed_for") == PET][0]
    assert not line.get("override"), "a family-plan caregiver's order must re-price the pet line"
