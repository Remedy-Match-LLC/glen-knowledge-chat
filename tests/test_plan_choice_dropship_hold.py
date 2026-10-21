"""The plan-choice email is mail from us, so the drop-ship switch covers it.

Glen, 2026-10-21: "Give them the option, with fallback being no emails." A drop-ship
client whose practitioner has client mail off gets no plan-choice email, in the bulk
band or one at a time. The hold is the watcher's: the typed client email, or a
shipment to the held ship-to address and name. It is read again before each send,
and a failed read sends nothing. Review rounds 1 (F6), 2b and 3.
"""
import json
import sqlite3
from datetime import datetime, timedelta

import app as app_module

HELD = "tony@client.example"
OTHER = "tony@gmail.example"          # the same client, found by GHL, never typed
OWN = "own@buyer.example"


def _setup(monkeypatch, tmp_db, *, switch=False):
    from dashboard import cohorts as _co
    from dashboard import orders as _orders
    from dashboard import practitioner_settings as _ps
    from dashboard.tracking import init_tracking_schema, record_shipment
    app = app_module
    monkeypatch.setattr(app, "LOG_DB", tmp_db)
    monkeypatch.setattr(app, "CONSOLE_SECRET", "testkey")
    monkeypatch.setattr(_co, "choosable_cohorts", lambda cx: [{"key": "k"}])
    monkeypatch.setattr(_co, "member_cohorts", lambda cx, em: [])
    sent = []
    monkeypatch.setattr(app, "_send_full_report_email", lambda em, *a: sent.append(em))
    monkeypatch.setattr(app, "_mint_plan_choice_token", lambda em: "tok")
    with sqlite3.connect(tmp_db) as cx:
        _orders.init_orders_table(cx)
        _ps.init_settings_table(cx)
        init_tracking_schema(cx)
        cx.execute("INSERT INTO orders (created_at, source, external_ref, email, name, "
                   "status, practitioner_id, recipient_email, address_json) "
                   "VALUES (?,?,?,?,?,?,?,?,?)",
                   ("2026-09-01", "dropship", "d1", "ashley@prac.example", "Ashley King",
                    "done", "p1", HELD,
                    json.dumps({"name": "Tony Client", "street": "4 Hale St",
                                "zip": "96720"})))
        if switch:
            _ps.set_client_review_emails(cx, "p1", True)
        delivered = (datetime.utcnow() - timedelta(days=25)).isoformat() + "Z"
        tony = ("Tony Client", "4 HALE ST / HILO HI 96720-1234 US")
        own = ("Own Buyer", "9 MAUKA RD / HILO HI 96720-1234 US")
        for n, (em, conf, (name, block)) in enumerate([
                (HELD, "high", own), (OTHER, "high", tony), (OWN, "high", own),
                ("ashley@prac.example", "dropship-held", tony)]):
            record_shipment(cx, tracking_number=f"T{n}", resolved_email=em,
                            match_confidence=conf, status="sent", recipient_name=name,
                            address_block=block)
            cx.execute("UPDATE shipments SET delivered_at=? WHERE tracking_number=?",
                       (delivered, f"T{n}"))
        cx.commit()
    return app, sent


def _send(app, body):
    return app.app.test_client().post("/api/console/plan-choice/send",
                                      headers={"X-Console-Key": "testkey"}, json=body)


def test_the_bulk_band_leaves_out_held_clients_by_email_and_by_address(monkeypatch, tmp_db):
    app, _ = _setup(monkeypatch, tmp_db)
    assert app._plan_choice_pending() == [OWN]


def test_switch_on_the_clients_are_in_the_band(monkeypatch, tmp_db):
    app, _ = _setup(monkeypatch, tmp_db, switch=True)
    assert sorted(app._plan_choice_pending()) == [OWN, HELD, OTHER]


def test_a_single_send_to_a_held_client_sends_nothing(monkeypatch, tmp_db):
    app, sent = _setup(monkeypatch, tmp_db)
    r = _send(app, {"email": OTHER})
    assert r.status_code == 200 and r.get_json()["held"] == [OTHER] and sent == []


def test_an_unreadable_hold_list_empties_the_band(monkeypatch, tmp_db):
    from dashboard import tracking_recipient as tr
    app, _ = _setup(monkeypatch, tmp_db)
    monkeypatch.setattr(tr, "dropship_holds", lambda cx: None)
    assert app._plan_choice_pending() == []


def test_an_unreadable_hold_list_stops_a_single_send(monkeypatch, tmp_db):
    from dashboard import tracking_recipient as tr
    app, sent = _setup(monkeypatch, tmp_db)
    monkeypatch.setattr(tr, "dropship_holds", lambda cx: None)
    assert _send(app, {"email": OWN}).get_json()["count"] == 0 and sent == []


def test_the_hold_is_read_again_before_each_send(monkeypatch, tmp_db):
    """The switch goes off after the first email; the second client is held."""
    from dashboard import practitioner_settings as _ps
    app, sent = _setup(monkeypatch, tmp_db, switch=True)
    monkeypatch.setattr(app, "_plan_choice_pending", lambda: [OWN, OTHER])
    def send_then_switch_off(em, *a):
        sent.append(em)
        with sqlite3.connect(tmp_db) as cx:
            _ps.set_client_review_emails(cx, "p1", False)
    monkeypatch.setattr(app, "_send_full_report_email", send_then_switch_off)
    assert _send(app, {}).get_json()["held"] == [OTHER] and sent == [OWN]


def test_the_console_lists_held_shipments(monkeypatch, tmp_db):
    app, _ = _setup(monkeypatch, tmp_db)
    r = app.app.test_client().get("/api/console/shipments/held",
                                  headers={"X-Console-Key": "testkey"})
    assert [h["tracking_number"] for h in r.get_json()["held"]] == ["T3"]
