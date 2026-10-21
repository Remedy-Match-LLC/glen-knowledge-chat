"""The plan-choice email is mail from us, so the drop-ship switch covers it.

Glen, 2026-10-21: "Give them the option, with fallback being no emails." A drop-ship
client whose practitioner has client mail off gets no plan-choice email, in the bulk
band or one at a time. Review round 1, F6.
"""
import importlib
import json
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

HELD = "tony@client.example"


def _app():
    repo_root = Path(__file__).resolve().parent.parent
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
    try:
        return importlib.import_module("app")
    except Exception as e:
        pytest.skip(f"app module not importable: {e}")


def _setup(monkeypatch, tmp_db, *, switch=False):
    from dashboard import cohorts as _co
    from dashboard import orders as _orders
    from dashboard import practitioner_settings as _ps
    from dashboard.tracking import init_tracking_schema, record_shipment
    app = _app()
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
                    "done", "p1", HELD, json.dumps({"name": "Tony Client"})))
        if switch:
            _ps.set_client_review_emails(cx, "p1", True)
        delivered = (datetime.utcnow() - timedelta(days=25)).isoformat() + "Z"
        for n, (em, conf) in enumerate([(HELD, "high"), ("own@buyer.example", "high"),
                                        ("ashley@prac.example", "dropship-held")]):
            record_shipment(cx, tracking_number=f"T{n}", resolved_email=em,
                            match_confidence=conf, status="sent")
            cx.execute("UPDATE shipments SET delivered_at=? WHERE tracking_number=?",
                       (delivered, f"T{n}"))
        cx.commit()
    return app, sent


def test_the_bulk_band_leaves_out_a_held_client_and_a_held_draft(monkeypatch, tmp_db):
    app, _ = _setup(monkeypatch, tmp_db)
    assert app._plan_choice_pending() == ["own@buyer.example"]


def test_switch_on_the_client_is_in_the_band(monkeypatch, tmp_db):
    app, _ = _setup(monkeypatch, tmp_db, switch=True)
    assert sorted(app._plan_choice_pending()) == ["own@buyer.example", HELD]


def test_a_single_send_to_a_held_client_sends_nothing(monkeypatch, tmp_db):
    app, sent = _setup(monkeypatch, tmp_db)
    r = app.app.test_client().post("/api/console/plan-choice/send",
                                   headers={"X-Console-Key": "testkey"},
                                   json={"email": HELD})
    assert r.status_code == 200 and r.get_json()["count"] == 0 and sent == []
