"""Operator-granted shipping credit.

ship_credit.grant() existed but was reachable from exactly ONE place: the
combined-shipment recalc. When a household combines AFTER paying, and the
operator settles it differently from the recalc's pro-rata split, there was no
way to record the credit at all -- it lived in someone's memory.

Live case 2026-09-03: orders #154 and #168 combined into one $23 parcel after
both had paid. JC's order was re-billed to $0 shipping while his $13 payment
stood, so he holds a $13 credit that nothing could record.
"""
import json
import sqlite3

import pytest

from dashboard import ship_credit


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setenv("SHIP_CREDIT_AUTOAPPLY_ENABLED", "1")
    import app as _app
    _app.app.config["TESTING"] = True
    return _app.app.test_client(), _app


def _hdr(app):
    return {"X-Console-Key": app.dashboard.CONSOLE_SECRET or "", "Content-Type": "application/json"}


def test_a_grant_requires_owner_auth(client):
    c, app = client
    r = c.post("/api/console/ship-credit", json={"email": "a@x.com", "cents": 1300,
                                                 "source_ref": "order:1"},
               headers={"Content-Type": "application/json"})
    assert r.status_code in (401, 403), "an unauthenticated grant must be refused"


def test_it_rejects_a_missing_or_bad_amount(client):
    c, app = client
    for body in ({"email": "a@x.com", "source_ref": "r1"},
                 {"email": "a@x.com", "cents": 0, "source_ref": "r1"},
                 {"email": "a@x.com", "cents": -500, "source_ref": "r1"},
                 {"cents": 1300, "source_ref": "r1"},
                 {"email": "a@x.com", "cents": 1300}):
        r = c.post("/api/console/ship-credit", json=body, headers=_hdr(app))
        assert r.status_code in (400, 401, 403), "accepted a bad grant: %s" % body


def test_grant_is_idempotent_on_the_source_ref(tmp_path):
    """The endpoint's value depends on grant() not double-crediting when an
    operator clicks twice; pin that at the ledger, which is what it relies on."""
    cx = sqlite3.connect(str(tmp_path / "p.db"))
    from dashboard import points as _points
    _points.init_points_table(cx)
    ship_credit.grant(cx, "jc@x.com", 1300, source_ref="order:168")
    first = ship_credit.balance(cx, "jc@x.com")
    ship_credit.grant(cx, "jc@x.com", 1300, source_ref="order:168")
    assert ship_credit.balance(cx, "jc@x.com") == first == 1300


def test_the_route_exists_and_is_registered():
    import app as _app
    rules = {str(r) for r in _app.app.url_map.iter_rules()}
    assert "/api/console/ship-credit" in rules
