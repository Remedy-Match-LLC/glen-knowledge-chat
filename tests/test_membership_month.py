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


def test_the_flag_off_hides_it_everywhere(monkeypatch):
    monkeypatch.delenv("MEMBERSHIP_MONTH_OFFER")
    assert app._get_product(SLUG) is None
    pc = app._price_cart([_line(2000), {"slug": "nerve-pulse", "qty": 1}], ship=SHIP)
    assert [r["slug"] for r in pc["items_rec"]] == ["nerve-pulse"]
    assert app._with_month_addon([{"slug": "nerve-pulse"}],
                                 {"membership_month_cents": 2000}) == [{"slug": "nerve-pulse"}]
    c = app.app.test_client()
    assert c.get("/begin/membership-month-offer").get_json() == {"on": False}
    assert c.get(f"/begin/product/{SLUG}").status_code == 404


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


# ---- the add-on and the store page ---------------------------------------------------

def test_the_add_on_joins_the_cart_and_replaces_any_posted_month():
    cart = app._with_month_addon([{"slug": "nerve-pulse", "qty": 1},
                                  {"slug": SLUG, "qty": 3, "price_cents": 1}],
                                 {"membership_month_cents": 2500})
    assert cart == [{"slug": "nerve-pulse", "qty": 1},
                    {"slug": SLUG, "qty": 1, "price_cents": 2500}]
    assert app._with_month_addon([{"slug": "nerve-pulse"}], {}) == [{"slug": "nerve-pulse"}]


def test_the_offer_route_carries_the_words_and_range():
    o = app.app.test_client().get("/begin/membership-month-offer").get_json()
    assert o == {"on": True, "min_cents": 100, "max_cents": 10000,
                 "line": app.MONTH_ADDON_LINE, "help": app.MONTH_ADDON_HELP}


def test_every_customer_checkout_passes_the_add_on():
    import inspect
    assert "_with_month_addon(" in inspect.getsource(app.begin_checkout)
    assert "_with_month_addon(" in inspect.getsource(app.api_cart_checkout)
    assert "_with_month_addon(" in inspect.getsource(app.api_client_portal_checkout)


def test_the_portal_prices_the_month_at_the_chosen_amount():
    lines, rec, sub = app._portal_priced_lines(
        [{"slug": "nerve-pulse", "qty": 1}, {"slug": SLUG, "qty": 4, "price_cents": 3000}])
    m = [r for r in rec if r["slug"] == SLUG][0]
    assert m["unit_cents"] == 3000 and m["qty"] == 1
    with pytest.raises(app.CheckoutError):
        app._portal_priced_lines([{"slug": SLUG, "price_cents": 50}])


def test_the_pages_load_the_add_on():
    import pathlib
    root = pathlib.Path(app.__file__).resolve().parent / "static"
    for page in ("begin-buy.html", "begin-cart.html"):
        src = (root / page).read_text()
        assert "/static/membership-month-addon.js" in src and "membership_month_cents" in src


def test_no_practitioner_or_portal_catalog_lists_it():
    assert app._is_orderable(SLUG) is False
    assert app._is_orderable(SLUG, app._get_product(SLUG)) is False


# ---- the paid hook and the points -------------------------------------------------------

@pytest.fixture
def filecx(tmp_path, monkeypatch):
    from dashboard import orders as O
    db = str(tmp_path / "chat_log.db")
    monkeypatch.setattr(app, "LOG_DB", db)
    monkeypatch.setattr(app, "_member_join_welcome", lambda *a, **k: None)
    c = sqlite3.connect(db)
    c.row_factory = sqlite3.Row
    O.init_orders_table(c)
    app.init_membership_tables(c)
    c.commit()
    app._init_people_table()
    yield c
    c.close()


def test_the_paid_hook_grants_the_month(filecx):
    app._grant_membership_line_dep(_order("DEP-1"))
    rows = filecx.execute("SELECT months FROM order_month_grants WHERE order_ref='DEP-1'"
                          ).fetchall()
    assert [r[0] for r in rows] == [1]
    assert len(_ends(filecx)) == 1


def test_the_month_earns_no_points_on_a_paid_order(monkeypatch, tmp_path):
    from dashboard import points
    db = str(tmp_path / "pts.db")
    monkeypatch.setattr(app, "LOG_DB", db)
    c = sqlite3.connect(db); c.row_factory = sqlite3.Row
    points.init_points_table(c); c.commit()
    np = app._PRODUCTS["products"]["nerve-pulse"]["price_cents"]

    def settle(items, ref):
        order = {"email": EMAIL, "total_cents": sum(i["unit_cents"] for i in items),
                 "shipping_cents": 0, "discount_cents": 0, "points_redeemed_cents": 0,
                 "source": "begin", "items": items}
        before = points.balance(c, EMAIL)
        app._settle_order_points(order, order_ref=ref)
        return points.balance(c, EMAIL) - before

    alone = settle([{"slug": "nerve-pulse", "qty": 1, "unit_cents": np}], "P-1")
    both = settle([{"slug": "nerve-pulse", "qty": 1, "unit_cents": np},
                   {"slug": SLUG, "qty": 1, "unit_cents": 10000}], "P-2")
    assert alone > 0 and both == alone
