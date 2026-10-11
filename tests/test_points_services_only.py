"""Points redeem on services only, and points held on unpaid orders are not offered twice.

Glen, 2026-10-10: "the 5% is earned on purchase of products at full price, and redeemable
on services", and "membership and courses, too". Glen, 2026-10-11, on the $13,997 Oasis
tools install: "It could pay for a portion of it, say up to $1000". Glen, 2026-10-10:
"put the credit on hold while payment is pending so it can't be used again."
"""
import importlib
import sqlite3
import sys
from pathlib import Path

import pytest

from dashboard import pricing
from dashboard import rbac as _rbac

S = pricing.load_settings({})
EMAIL = "buyer@x.com"


def _item(slug, cents, qty=1):
    return {"slug": slug, "name": slug, "qty": qty, "product": {"slug": slug, "price_cents": cents},
            "unit_cents": cents, "months": 0, "volume_eligible": False}


def test_a_product_line_takes_no_points():
    r = pricing.compute([_item("estro-clear", 7000)], settings=S, points_to_redeem_cents=5000)
    assert r["points_redeemed_cents"] == 0


def test_points_skip_the_product_and_land_on_the_service():
    r = pricing.compute([_item("estro-clear", 7000), _item("biofield-analysis", 30000)],
                        settings=S, points_to_redeem_cents=50000)
    assert [ln["points_cents"] for ln in r["lines"]] == [0, 17100]   # 57% of $300, whole dollars


@pytest.mark.parametrize("slug", sorted(pricing.POINTS_REDEEM_SLUGS) + ["membership:year_prepay"])
def test_every_listed_service_takes_points(slug):
    r = pricing.compute([_item(slug, 10000)], settings=S, points_to_redeem_cents=1000)
    assert r["points_redeemed_cents"] == 1000, slug


def test_the_ionizer_installment_is_not_a_service():
    assert not pricing.points_redeemable("water-ionizer-installment")


def test_the_oasis_install_caps_at_a_thousand_dollars_per_order():
    r = pricing.compute([_item("oasis-tools-install-cert", 1399700, qty=2)], settings=S,
                        points_to_redeem_cents=900000)
    assert r["points_redeemed_cents"] == 100000
    # Split across two lines, the cap still holds for the order.
    r = pricing.compute([_item("oasis-tools-install-cert", 1399700),
                         _item("oasis-tools-install-cert", 1399700)], settings=S,
                        points_to_redeem_cents=900000)
    assert r["points_redeemed_cents"] == 100000


def test_the_invoice_room_matches_the_engine():
    lines = [{"slug": "oasis-tools-install-cert", "unit_cents": 1399700, "qty": 1},
             {"slug": "membership:year_prepay", "unit_cents": 99000, "qty": 1},
             {"slug": "estro-clear", "unit_cents": 7000, "qty": 3},
             {"slug": "biofield-analysis", "unit_cents": 0, "qty": 1, "gift": True}]
    # $1,000 cap + 57% of $990 = $564.30 -> $1,564 in whole dollars; products and gifts 0.
    assert pricing.points_room_cents(lines, S, lambda s: {}) == 156400


# ── App paths: the staff builder, the editor and the customer invoice ──────────────────

SERVICE = {"slug": "evox-session", "name": "EVOX", "price_cents": 15000, "service": True}
PRODUCT = {"slug": "thing", "name": "Thing", "price_cents": 7000}


def _app(tmp_path, monkeypatch, *, orders):
    """orders: list of (external_ref, slug, points_redeemed_cents, pay_status)."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    db = str(tmp_path / "chat_log.db")
    from dashboard import orders as O, points as P
    with sqlite3.connect(db) as cx:
        O.init_orders_table(cx)
        for ref, slug, pts, pay in orders:
            unit = SERVICE["price_cents"] if slug == "evox-session" else PRODUCT["price_cents"]
            O.upsert_order(cx, source="in-house", external_ref=ref, status="proposed",
                           email=EMAIL, name="Buyer", channel="pickup",
                           items=[{"slug": slug, "name": slug, "qty": 1, "unit_cents": unit,
                                   "line_cents": unit}],
                           total_cents=unit - pts, points_redeemed_cents=pts)
            if pay == "paid":
                cx.execute("UPDATE orders SET pay_status='paid' WHERE external_ref=?", (ref,))
        P.init_points_table(cx)
        P.credit(cx, EMAIL, value_cents=5000, reason="earn", order_ref="OLD-1")
        cx.commit()
    repo = Path(__file__).resolve().parent.parent
    if str(repo) not in sys.path:
        sys.path.insert(0, str(repo))
    import app as appmod
    importlib.reload(appmod)
    appmod.app.config["TESTING"] = True
    monkeypatch.setattr(appmod, "_bos_actor", lambda: _rbac.Actor(role="owner", name="glen"))
    monkeypatch.setattr(appmod, "_get_product",
                        lambda s: dict(SERVICE) if s == "evox-session" else
                        dict(PRODUCT) if s == "thing" else None)
    monkeypatch.setattr(appmod, "_push_invoice_edit_to_qbo", lambda *a, **k: {"pushed": False})
    return appmod, appmod.app.test_client(), db


def _order(db, oid):
    from dashboard import orders as O
    with sqlite3.connect(db) as cx:
        cx.row_factory = sqlite3.Row
        return O.get_order(cx, oid)


def _apply(appmod, client, db, oid, cents):
    from dashboard import practitioner_portal as PP
    PP._LOG_DB = Path(db)
    token = PP.create_order_invoice_token(oid)
    return client.post(f"/api/invoice/{token}/apply-points", json={"cents": cents})


def test_the_customer_invoice_does_not_count_its_own_hold(tmp_path, monkeypatch):
    # $30 already set on this invoice; re-applying $50 must see the full $50 balance.
    appmod, client, db = _app(tmp_path, monkeypatch,
                              orders=[("INH-1", "evox-session", 3000, "unpaid")])
    monkeypatch.setattr("dashboard.practitioner_portal._LOG_DB", Path(db))
    assert _apply(appmod, client, db, 1, 5000).status_code == 200
    assert _order(db, 1)["points_redeemed_cents"] == 5000


def test_points_held_on_another_unpaid_order_are_not_offered_again(tmp_path, monkeypatch):
    appmod, client, db = _app(tmp_path, monkeypatch,
                              orders=[("INH-1", "evox-session", 3000, "unpaid"),
                                      ("INH-2", "evox-session", 0, "unpaid")])
    monkeypatch.setattr("dashboard.practitioner_portal._LOG_DB", Path(db))
    assert _apply(appmod, client, db, 2, 5000).status_code == 200
    assert _order(db, 2)["points_redeemed_cents"] == 2000      # $50 less $30 held
    assert appmod._invoice_summary(_order(db, 2))["points_balance_cents"] == 2000


def test_a_paid_order_holds_nothing(tmp_path, monkeypatch):
    appmod, client, db = _app(tmp_path, monkeypatch,
                              orders=[("INH-1", "evox-session", 3000, "paid"),
                                      ("INH-2", "evox-session", 0, "unpaid")])
    monkeypatch.setattr("dashboard.practitioner_portal._LOG_DB", Path(db))
    assert _apply(appmod, client, db, 2, 5000).status_code == 200
    assert _order(db, 2)["points_redeemed_cents"] == 5000


def test_the_invoice_shows_no_room_on_a_product_invoice(tmp_path, monkeypatch):
    appmod, _, db = _app(tmp_path, monkeypatch, orders=[("INH-1", "thing", 0, "unpaid")])
    assert appmod._invoice_summary(_order(db, 1))["points_room_cents"] == 0


def test_the_editor_respects_holds_and_services(tmp_path, monkeypatch):
    appmod, client, db = _app(tmp_path, monkeypatch,
                              orders=[("INH-1", "evox-session", 3000, "unpaid"),
                                      ("INH-2", "evox-session", 1000, "unpaid")])
    r = client.post("/api/orders/2/edit", json={"lines": [{"slug": "evox-session", "qty": 1}],
                                               "pickup": True, "points_redeem_cents": 9000})
    assert r.status_code == 200, r.get_json()
    # Balance $50 less the $30 held on order 1; order 2's own $10 is not counted against it.
    assert _order(db, 2)["points_redeemed_cents"] == 2000
    r = client.post("/api/orders/2/edit", json={"lines": [{"slug": "thing", "qty": 1}],
                                               "pickup": True, "points_redeem_cents": 9000})
    assert r.status_code == 200, r.get_json()
    assert int(_order(db, 2)["points_redeemed_cents"] or 0) == 0


def test_the_staff_builder_respects_holds(tmp_path, monkeypatch):
    appmod, _, db = _app(tmp_path, monkeypatch,
                         orders=[("INH-1", "evox-session", 3000, "unpaid")])
    priced = appmod._price_inhouse_invoice([{"slug": "evox-session", "qty": 1}], email=EMAIL,
                                           pickup=True, ship={}, points_redeem_cents_in=9000)
    assert priced["points_redeemed_cents"] == 2000
