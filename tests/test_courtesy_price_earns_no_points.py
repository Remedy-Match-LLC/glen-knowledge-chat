"""Glen, 2026-10-10: "No points on courtesy prices; close the gap." Rule (a): an order
earns points only when every line is at full store price. Volume and member rates earn
nothing, as in the store cart.

The gap: an in-house invoice puts a courtesy or volume price into the line's unit_cents,
so discount_cents stays 0 and the old full-price test passed. Debra Herndon's $50 lines
against the $70 store price earned 5% on orders 63 and 165.
"""
import sqlite3

import app as appmod
from dashboard import points

SLUG = "brain-cleanse"


def _settle(monkeypatch, tmp_path, items, email="a@x.com", ref="INV-C"):
    db = str(tmp_path / "t.db")
    monkeypatch.setattr(appmod, "LOG_DB", db)
    cx = sqlite3.connect(db); cx.row_factory = sqlite3.Row
    points.init_points_table(cx); cx.commit()
    total = sum(int(i["unit_cents"]) * int(i["qty"]) for i in items)
    order = {"email": email, "total_cents": total, "shipping_cents": 0,
             "discount_cents": 0, "points_redeemed_cents": 0, "source": "in-house",
             "items": items}
    appmod._settle_order_points(order, order_ref=ref)
    return points.balance(cx, email)


def _store(slug):
    p = appmod._PRODUCTS["products"][slug]
    return int(p.get("price_cents") or appmod._PRODUCTS.get("default_price_cents", 7000))


def test_a_courtesy_line_earns_nothing(monkeypatch, tmp_path):
    items = [{"slug": SLUG, "qty": 1, "unit_cents": _store(SLUG) - 2000}]
    assert _settle(monkeypatch, tmp_path, items) == 0


def test_one_courtesy_line_stops_the_whole_order(monkeypatch, tmp_path):
    items = [{"slug": SLUG, "qty": 2, "unit_cents": _store(SLUG)},
             {"slug": "lipid-cleanse", "qty": 1, "unit_cents": _store("lipid-cleanse") - 741}]
    assert _settle(monkeypatch, tmp_path, items) == 0


def test_a_free_line_earns_nothing(monkeypatch, tmp_path):
    items = [{"slug": SLUG, "qty": 1, "unit_cents": _store(SLUG)},
             {"slug": "biofield-analysis", "qty": 1, "unit_cents": 0}]
    assert _settle(monkeypatch, tmp_path, items) == 0


def test_every_line_at_store_price_still_earns(monkeypatch, tmp_path):
    items = [{"slug": SLUG, "qty": 2, "unit_cents": _store(SLUG)},
             {"slug": "lipid-cleanse", "qty": 1, "unit_cents": _store("lipid-cleanse")}]
    total = 2 * _store(SLUG) + _store("lipid-cleanse")
    assert _settle(monkeypatch, tmp_path, items) == round(total * 0.05)


def test_a_full_member_at_the_member_price_still_earns(monkeypatch, tmp_path):
    """The store charges a full member the member price and earns on it; so does this."""
    p = appmod._PRODUCTS["products"]["biofield-analysis"]
    mp = int(p["member_price_cents"])
    monkeypatch.setattr(appmod, "_is_paid_member", lambda e: True)
    items = [{"slug": "biofield-analysis", "qty": 1, "unit_cents": mp}]
    assert _settle(monkeypatch, tmp_path, items) == round(mp * 0.05)


def test_a_non_member_at_the_member_price_earns_nothing(monkeypatch, tmp_path):
    p = appmod._PRODUCTS["products"]["biofield-analysis"]
    monkeypatch.setattr(appmod, "_is_paid_member", lambda e: False)
    items = [{"slug": "biofield-analysis", "qty": 1, "unit_cents": int(p["member_price_cents"])}]
    assert _settle(monkeypatch, tmp_path, items) == 0


def test_a_line_with_no_catalog_product_is_not_judged(monkeypatch, tmp_path):
    items = [{"slug": SLUG, "qty": 1, "unit_cents": _store(SLUG)},
             {"slug": "", "name": "Custom line", "qty": 1, "unit_cents": 100}]
    assert _settle(monkeypatch, tmp_path, items) == round((_store(SLUG) + 100) * 0.05)


def test_the_record_payment_settler_applies_the_same_rule(tmp_path):
    """dashboard.orders.settle_order_points is a second earn path (Record payment, the
    in-house Stripe return). It must refuse a courtesy line too."""
    from dashboard import orders
    assert orders._below_store_price_check is appmod._order_has_line_below_store_price
    cx = sqlite3.connect(str(tmp_path / "o.db")); cx.row_factory = sqlite3.Row
    points.init_points_table(cx)
    base = {"email": "b@x.com", "total_cents": 5000, "shipping_cents": 0,
            "discount_cents": 0, "points_redeemed_cents": 0}
    orders.settle_order_points(cx, {**base, "external_ref": "R1",
                                    "items": [{"slug": SLUG, "qty": 1, "unit_cents": 5000}]})
    assert points.balance(cx, "b@x.com") == 0
    full = _store(SLUG)
    orders.settle_order_points(cx, {**base, "external_ref": "R2", "total_cents": full,
                                    "items": [{"slug": SLUG, "qty": 1, "unit_cents": full}]})
    assert points.balance(cx, "b@x.com") == round(full * 0.05)


def test_a_retired_slug_is_judged_against_its_live_product(monkeypatch, tmp_path):
    """emf is retired at $0; its live twin costs more. A cheap emf line is below store."""
    live = appmod._superseded("emf")
    assert live != "emf" and _store(live) > 0
    items = [{"slug": "emf", "qty": 1, "unit_cents": _store(live) - 1000}]
    assert _settle(monkeypatch, tmp_path, items) == 0


def test_the_old_97_price_still_counts_as_full_price(monkeypatch, tmp_path):
    """The 10-02 rounding lifted $69.97 to $70; an invoice issued before it still earns."""
    assert _store(SLUG) % 100 == 0
    unit = _store(SLUG) - 3
    items = [{"slug": SLUG, "qty": 1, "unit_cents": unit}]
    assert _settle(monkeypatch, tmp_path, items) == round(unit * 0.05)


def test_four_cents_under_is_not_the_rounding(monkeypatch, tmp_path):
    items = [{"slug": SLUG, "qty": 1, "unit_cents": _store(SLUG) - 4}]
    assert _settle(monkeypatch, tmp_path, items) == 0


def test_a_review_gift_line_blocks_earn(monkeypatch, tmp_path):
    """A $0 gift line is a line below store price. Kept as the rule reads until Glen says
    otherwise."""
    items = [{"slug": SLUG, "qty": 1, "unit_cents": _store(SLUG)},
             {"slug": "lipid-cleanse", "qty": 1, "unit_cents": 0, "gift": True}]
    assert _settle(monkeypatch, tmp_path, items) == 0


def test_the_record_payment_settler_fails_closed(monkeypatch, tmp_path):
    from dashboard import orders

    def boom(order):
        raise ValueError("bad price")
    monkeypatch.setattr(orders, "_below_store_price_check", boom)
    cx = sqlite3.connect(str(tmp_path / "f.db")); cx.row_factory = sqlite3.Row
    points.init_points_table(cx)
    full = _store(SLUG)
    orders.settle_order_points(cx, {"email": "c@x.com", "external_ref": "F1",
                                    "total_cents": full, "shipping_cents": 0,
                                    "discount_cents": 0, "points_redeemed_cents": 0,
                                    "items": [{"slug": SLUG, "qty": 1, "unit_cents": full}]})
    assert points.balance(cx, "c@x.com") == 0
