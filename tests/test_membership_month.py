"""One month of membership at a price the buyer chooses, Glen, platform tab, 2026-11-26:
"yes, sell the membership month as a store page"; "yes, a bought month adds 30 days
after any current term, outside the cap"; "offer the one month name your own price as an
upsell to any other purchase"; "yes, unticked add-on at every checkout, store page kept".
"""
import sqlite3
from datetime import datetime, timedelta

import pytest

import app

SLUG = app.MEMBERSHIP_MONTH_SLUG
SHIP = {"country": "US", "state": "HI", "zip": "96720"}
EMAIL = "m@example.test"


@pytest.fixture(autouse=True)
def _on(monkeypatch):
    monkeypatch.setenv("MEMBERSHIP_MONTH_OFFER", "true")
    # A first membership sends the portal welcome: never from a test.
    monkeypatch.setattr(app, "_member_join_welcome", lambda *a, **k: None)


def _line(pc, qty=1):
    return {"slug": SLUG, "qty": qty, "price_cents": pc}


def _rec(pc):
    return [r for r in app._price_cart([_line(pc)], ship=SHIP)["items_rec"]
            if r["slug"] == SLUG][0]


# ---- price ----------------------------------------------------------------------------

@pytest.mark.parametrize("pc", [100, 2500, 10000])
def test_any_whole_dollar_from_1_to_100(pc):
    r = _rec(pc)
    assert r["unit_cents"] == pc and r["line_cents"] == pc


@pytest.mark.parametrize("pc", [0, 99, 150, 10100, -100, None, "", "abc", "nan", "1e400"])
def test_anything_else_is_refused(pc):
    with pytest.raises(app.CheckoutError, match=r"whole-dollar price from \$1 to \$100"):
        app._price_cart([_line(pc)], ship=SHIP)


def test_the_flag_off_refuses_it(monkeypatch):
    monkeypatch.delenv("MEMBERSHIP_MONTH_OFFER")
    with pytest.raises(app.CheckoutError, match="not available"):
        app._price_cart([_line(2000)], ship=SHIP)


def test_one_at_a_time():
    r = [x for x in app._price_cart([_line(2000, qty=5)], ship=SHIP)["items_rec"]
         if x["slug"] == SLUG][0]
    assert r["qty"] == 1 and r["line_cents"] == 2000


def test_nothing_discounts_it():
    pc = app._price_cart([_line(4000), {"slug": "nerve-pulse", "qty": 6}], ship=SHIP,
                         program_member=True, coupon_pct=50)
    m = [l for l in pc["priced"]["lines"] if l["slug"] == SLUG][0]
    assert m["discount_cents"] == 0 and m["line_total_cents"] == 4000
    np = [l for l in pc["priced"]["lines"] if l["slug"] == "nerve-pulse"][0]
    assert np["discount_cents"] > 0


def test_no_points_pay_for_it():
    pc = app._price_cart([_line(4000)], ship=SHIP, points_to_redeem_cents=4000) \
        if "points_to_redeem_cents" in app._price_cart.__code__.co_varnames else None
    if pc is None:
        pytest.skip("pricer takes no points argument")
    m = [l for l in pc["priced"]["lines"] if l["slug"] == SLUG][0]
    assert m["points_cents"] == 0 and m["line_total_cents"] == 4000


def test_it_ships_nothing():
    alone = app._price_cart([_line(4000)], ship=SHIP)
    assert int(alone.get("shipping_cents") or 0) == 0


def test_it_earns_no_points_and_the_rest_still_earns():
    np = app._PRODUCTS["products"]["nerve-pulse"]["price_cents"]
    order = {"items": [{"slug": SLUG, "qty": 1, "unit_cents": 2500, "line_cents": 2500},
                       {"slug": "nerve-pulse", "qty": 1, "unit_cents": np,
                        "line_cents": np}]}
    assert app._buyer_priced_line_cents(order) == 2500
    assert app._order_has_line_below_store_price(order) is False


# ---- the 30 days ----------------------------------------------------------------------

@pytest.fixture
def cx():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    app.init_membership_tables(c)
    yield c
    c.close()


def _order(ref="o1", qty=1, status="paid"):
    return {"id": 1, "external_ref": ref, "email": EMAIL, "status": status,
            "items": [{"slug": SLUG, "qty": qty, "unit_cents": 1000}]}


def _ends(cx):
    return [app._supply_naive(r[0]) for r in cx.execute(
        "SELECT expires_at FROM memberships WHERE source=? ORDER BY expires_at",
        (app.MEMBERSHIP_MONTH_SOURCE,)).fetchall()]


def _add(cx, days, source):
    cx.execute("INSERT INTO memberships (id, email, granted_at, expires_at, source) "
               "VALUES (?,?,?,?,?)", (f"{source}-{days}", EMAIL,
                                      datetime.utcnow().isoformat(),
                                      (datetime.utcnow() + timedelta(days=days)).isoformat(),
                                      source))
    cx.commit()


def test_no_membership_starts_now(cx):
    assert app._grant_bought_month_on_paid(cx, _order()) == "granted"
    (end,) = _ends(cx)
    assert abs((end - datetime.utcnow()) - timedelta(days=30)) < timedelta(minutes=1)


@pytest.mark.parametrize("source", ["supply_months", "care_taster",
                                    "biofield_month_delivery", "prepay_annual"])
def test_it_starts_after_any_current_term(cx, source):
    _add(cx, 20, source)
    app._grant_bought_month_on_paid(cx, _order())
    (end,) = _ends(cx)
    assert abs((end - datetime.utcnow()) - timedelta(days=50)) < timedelta(minutes=1)


def test_the_trial_unlock_is_not_a_term(cx):
    _add(cx, 36500, "biofield_trial")
    app._grant_bought_month_on_paid(cx, _order())
    (end,) = _ends(cx)
    assert end - datetime.utcnow() < timedelta(days=31)


def test_no_biofield_or_cap_needed(cx):
    # Outside the remedy cap: nothing about Biofields is read.
    assert app._grant_bought_month_on_paid(cx, _order()) == "granted"


def test_once_per_order(cx):
    assert app._grant_bought_month_on_paid(cx, _order()) == "granted"
    assert app._grant_bought_month_on_paid(cx, _order()) == "already"
    assert len(_ends(cx)) == 1


def test_two_months_add_60_days(cx):
    app._grant_bought_month_on_paid(cx, _order(qty=2))
    (end,) = _ends(cx)
    assert abs((end - datetime.utcnow()) - timedelta(days=60)) < timedelta(minutes=1)


def test_a_cancelled_or_month_free_order_grants_nothing(cx):
    assert app._grant_bought_month_on_paid(cx, _order(status="cancelled")) == "none"
    o = _order(); o["items"] = [{"slug": "nerve-pulse", "qty": 1}]
    assert app._grant_bought_month_on_paid(cx, o) == "none"
    assert _ends(cx) == []
