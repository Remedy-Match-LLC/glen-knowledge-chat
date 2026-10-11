"""Own-purchase points redeem on services only; points held on unpaid orders are not
offered twice; referral and other non-purchase points also redeem on products.

Glen, 2026-10-10: "the 5% is earned on purchase of products at full price, and redeemable
on services", and "membership and courses, too". Glen, 2026-10-11, on the $13,997 Oasis
tools install: "It could pay for a portion of it, say up to $1000". Glen, 2026-10-10:
"put the credit on hold while payment is pending so it can't be used again." Glen,
2026-10-11: the limit applies to points from the client's own full-price orders; referral
tier 1 and 2 points, and review, image-pick and milestone points ("referral rule for those
too"), also reduce product prices, within the existing points floor.
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


def test_a_product_allowance_reaches_products_after_services():
    r = pricing.compute([_item("estro-clear", 7000), _item("biofield-analysis", 30000)],
                        settings=S, points_to_redeem_cents=50000, product_points_cents=2000)
    assert [ln["points_cents"] for ln in r["lines"]] == [2000, 17100]
    assert r["points_product_cents"] == 2000


def test_a_product_allowance_stops_at_the_points_floor():
    r = pricing.compute([_item("estro-clear", 7000)], settings=S,
                        points_to_redeem_cents=90000, product_points_cents=90000)
    assert r["lines"][0]["line_total_cents"] == 3100   # 43% of $70, next whole dollar


def test_services_take_points_before_products():
    # $150 asked, $100 of it may go on products: the service takes its $85 first.
    r = pricing.compute([_item("estro-clear", 7000), _item("evox-session", 15000)],
                        settings=S, points_to_redeem_cents=15000, product_points_cents=10000)
    assert [ln["points_cents"] for ln in r["lines"]] == [3900, 8500]


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


def _app(tmp_path, monkeypatch, *, orders, earn=5000, referral=0, ledger=None):
    """orders: list of (external_ref, slug, points_redeemed_cents, pay_status). A paid
    order's points leave the ledger after the credits, as settlement does. ledger, when
    given, replaces the credits: (reason, cents, order_ref) rows in time order, a
    negative row being a redeem."""
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
        if ledger is not None:
            for reason, cents, ref in ledger:
                if cents > 0:
                    P.credit(cx, EMAIL, value_cents=cents, reason=reason, order_ref=ref)
                else:
                    P.redeem(cx, EMAIL, value_cents=-cents, order_ref=ref)
        else:
            if earn:
                P.credit(cx, EMAIL, value_cents=earn, reason="earn", order_ref="OLD-1")
            if referral:
                P.credit(cx, EMAIL, value_cents=referral, reason="referral_reward",
                         order_ref="referral:friend@x.com")
            for ref, slug, pts, pay in orders:
                if pay == "paid" and pts:
                    P.redeem(cx, EMAIL, value_cents=pts, order_ref=ref)
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
    # The paid order's $30 has left the ledger; nothing more is held for it.
    assert _order(db, 2)["points_redeemed_cents"] == 2000


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


def test_referral_points_reach_a_product_invoice(tmp_path, monkeypatch):
    appmod, client, db = _app(tmp_path, monkeypatch, orders=[("INH-1", "thing", 0, "unpaid")],
                              earn=5000, referral=2000)
    monkeypatch.setattr("dashboard.practitioner_portal._LOG_DB", Path(db))
    assert _apply(appmod, client, db, 1, 9000).status_code == 200
    assert _order(db, 1)["points_redeemed_cents"] == 2000   # the referral points only


def test_referral_points_on_one_unpaid_product_order_are_not_offered_again(tmp_path, monkeypatch):
    appmod, client, db = _app(tmp_path, monkeypatch,
                              orders=[("INH-1", "thing", 1500, "unpaid"),
                                      ("INH-2", "thing", 0, "unpaid")],
                              earn=5000, referral=2000)
    monkeypatch.setattr("dashboard.practitioner_portal._LOG_DB", Path(db))
    assert _apply(appmod, client, db, 2, 9000).status_code == 200
    assert _order(db, 2)["points_redeemed_cents"] == 500


def test_a_service_spend_uses_own_purchase_points_first(tmp_path, monkeypatch):
    # $30 already set on a service invoice comes from the $50 own-purchase points, so the
    # $20 of referral points is still free for a product.
    appmod, client, db = _app(tmp_path, monkeypatch,
                              orders=[("INH-1", "evox-session", 3000, "paid"),
                                      ("INH-2", "thing", 0, "unpaid")],
                              earn=5000, referral=2000)
    assert appmod._product_usable_points_cents(EMAIL) == 2000


def test_a_paid_product_spend_uses_up_the_referral_allowance(tmp_path, monkeypatch):
    appmod, client, db = _app(tmp_path, monkeypatch,
                              orders=[("INH-1", "thing", 2000, "paid"),
                                      ("INH-2", "thing", 0, "unpaid")],
                              earn=5000, referral=2000)
    assert appmod._product_usable_points_cents(EMAIL) == 0


def test_review_and_milestone_points_follow_the_referral_rule():
    from dashboard import points as P
    with sqlite3.connect(":memory:") as cx:
        P.init_points_table(cx)
        P.credit(cx, EMAIL, value_cents=500, reason="earn", order_ref="o1")
        P.credit(cx, EMAIL, value_cents=300, reason="review:evox-session", order_ref="r1")
        P.credit(cx, EMAIL, value_cents=200, reason="healing_milestone", order_ref="m1")
        P.credit(cx, EMAIL, value_cents=100, reason="image_pick", order_ref="i1")
        assert P.product_usable_credits(cx, EMAIL) == 600


def test_a_checkout_line_records_its_product_points(tmp_path, monkeypatch):
    appmod, _, db = _app(tmp_path, monkeypatch, orders=[], earn=0, referral=2000)
    monkeypatch.setattr(appmod, "_get_product",
                        lambda s: {**SERVICE, "slug": s} if s == "evox-session" else
                        {**PRODUCT, "slug": s, "service": True} if s == "thing" else None)
    pc = appmod._price_cart([{"slug": "thing", "qty": 1}, {"slug": "evox-session", "qty": 1}],
                            ship={"country": "US", "state": "CA"}, points_to_redeem_cents=12000,
                            product_points_cents=2000)
    # The service takes $85 first, then the product takes the $20 allowance.
    assert [r["points_product_cents"] for r in pc["items_rec"]] == [2000, 0]
    assert pc["points_redeemed_cents"] == 10500
    assert appmod._order_product_points_cents({"items": pc["items_rec"],
                                               "points_redeemed_cents": 10500}) == 2000


def test_referral_points_spent_on_a_service_do_not_return_when_points_are_earned_later(
        tmp_path, monkeypatch):
    # Review rounds 1 and 2: $20 referral, a $20 service paid with it, then $50 earned.
    # Only own-purchase points are left, so nothing may go on a product.
    appmod, _, _ = _app(tmp_path, monkeypatch,
                        orders=[("INH-1", "evox-session", 2000, "paid")],
                        ledger=[("referral_reward", 2000, "referral:f@x.com"),
                                ("redeem", -2000, "INH-1"),
                                ("earn", 5000, "OLD-1")])
    assert appmod._product_usable_points_cents(EMAIL) == 0


def test_a_service_paid_after_earning_uses_own_purchase_points(tmp_path, monkeypatch):
    appmod, _, _ = _app(tmp_path, monkeypatch,
                        orders=[("INH-1", "evox-session", 2000, "paid")],
                        ledger=[("referral_reward", 2000, "referral:f@x.com"),
                                ("earn", 5000, "OLD-1"),
                                ("redeem", -2000, "INH-1")])
    assert appmod._product_usable_points_cents(EMAIL) == 2000


def test_an_edit_that_swaps_a_service_for_a_product_drops_own_purchase_points(
        tmp_path, monkeypatch):
    # Review round 1: /update with no points field kept $50 on a product-only invoice.
    appmod, client, db = _app(tmp_path, monkeypatch,
                              orders=[("INH-1", "evox-session", 5000, "unpaid")])
    r = client.post("/api/orders/1/edit", json={"lines": [{"slug": "thing", "qty": 1}],
                                               "pickup": True})
    assert r.status_code == 200, r.get_json()
    o = _order(db, 1)
    assert int(o["points_redeemed_cents"] or 0) == 0
    assert int(o["total_cents"]) == PRODUCT["price_cents"]


def test_an_edit_keeps_points_that_still_fit(tmp_path, monkeypatch):
    appmod, client, db = _app(tmp_path, monkeypatch,
                              orders=[("INH-1", "evox-session", 3000, "unpaid")])
    r = client.post("/api/orders/1/edit", json={"lines": [{"slug": "evox-session", "qty": 1}],
                                               "pickup": True})
    assert r.status_code == 200, r.get_json()
    assert _order(db, 1)["points_redeemed_cents"] == 3000


def test_invoice_points_stamp_their_split(tmp_path, monkeypatch):
    # Review round 2: a later points change must not leave a stale split behind.
    appmod, client, db = _app(tmp_path, monkeypatch, orders=[("INH-1", "thing", 0, "unpaid")],
                              earn=0, referral=2000)
    monkeypatch.setattr("dashboard.practitioner_portal._LOG_DB", Path(db))
    assert _apply(appmod, client, db, 1, 2000).status_code == 200
    o = dict(_order(db, 1))
    assert o["items"][0]["points_split_of_cents"] == 2000
    assert appmod._order_product_points_cents(o) == 2000


def test_a_stale_split_is_ignored():
    import app as appmod
    items = [{"slug": "thing", "unit_cents": 7000, "qty": 1,
              "points_product_cents": 0, "points_split_of_cents": 0}]
    # Points were later set to $20 without a re-stamp: the split is rebuilt, not trusted.
    assert appmod._order_product_points_cents({"items": items,
                                               "points_redeemed_cents": 2000}) == 2000
