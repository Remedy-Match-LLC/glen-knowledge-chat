"""A timed sale price on the new store (Glen, platform tab, 2026-11-26: "yes, Pollen Power
with those defaults"): $40 until midnight Pacific Monday 30 Nov, no other discount on
top, the regular price back at the end with no deploy, and no cart for more bottles
than the shelf count holds."""
from datetime import datetime, timedelta, timezone

import pytest

import app

SLUG = "pollen-power"
SHIP = {"country": "US", "state": "HI", "zip": "96720"}


def _set_sale(monkeypatch, **sale):
    rec = dict(app._PRODUCTS["products"][SLUG])
    rec["sale"] = sale
    monkeypatch.setitem(app._PRODUCTS["products"], SLUG, rec)


def _iso(delta):
    return (datetime.now(timezone.utc) + delta).isoformat()


def _lines(pc):
    return [(l["slug"], l["unit_cents"], l["line_cents"]) for l in pc["items_rec"]]


def test_glens_sale_is_in_the_catalog():
    s = app._PRODUCTS["products"][SLUG]["sale"]
    assert s["price_cents"] == 4000
    assert s["ends_at"] == "2026-12-01T08:00:00Z"  # midnight Pacific, 22:00 HST Monday
    assert app._PRODUCTS["products"][SLUG]["price_cents"] == 7000


def test_the_end_is_midnight_pacific():
    from zoneinfo import ZoneInfo
    end = datetime.fromisoformat("2026-12-01T08:00:00+00:00")
    assert end.astimezone(ZoneInfo("America/Los_Angeles")).strftime("%Y-%m-%d %H:%M") == \
        "2026-12-01 00:00"
    assert end.astimezone(ZoneInfo("Pacific/Honolulu")).strftime("%Y-%m-%d %H:%M") == \
        "2026-11-30 22:00"


def test_page_shows_the_sale_with_the_regular_price_struck(monkeypatch):
    _set_sale(monkeypatch, price_cents=4000, ends_at=_iso(timedelta(days=1)))
    d = app.app.test_client().get(f"/begin/product-data/{SLUG}").get_json()
    assert d["price_cents"] == 4000 and d["price"] == "$40.00"
    assert d["regular"] == "$70.00"
    assert d["qty_pricing"] is None  # no quantity tiers shown on the sale price


def test_cart_charges_the_sale_price_and_nothing_stacks(monkeypatch):
    _set_sale(monkeypatch, price_cents=4000, ends_at=_iso(timedelta(days=1)))
    pc = app._price_cart([{"slug": SLUG, "qty": 6}], ship=SHIP)
    assert _lines(pc) == [(SLUG, 4000, 24000)]
    assert pc["discount_cents"] == 0
    pc = app._price_cart([{"slug": SLUG, "qty": 6}], ship=SHIP, coupon_pct=20)
    assert pc["discount_cents"] == 0
    pc = app._price_cart([{"slug": SLUG, "qty": 6}], ship=SHIP, subscriber_tier_pct=15)
    assert pc["discount_cents"] == 0


def test_after_the_end_the_regular_price_and_discounts_return(monkeypatch):
    _set_sale(monkeypatch, price_cents=4000, ends_at=_iso(timedelta(seconds=-1)))
    d = app.app.test_client().get(f"/begin/product-data/{SLUG}").get_json()
    assert d["price_cents"] == 7000 and d["regular"] == ""
    assert d["qty_pricing"]  # the tiers are back
    pc = app._price_cart([{"slug": SLUG, "qty": 6}], ship=SHIP)
    assert _lines(pc) == [(SLUG, 7000, 42000)]
    assert pc["discount_cents"] > 0


def test_the_exact_end_instant_is_regular_price():
    rec = dict(app._PRODUCTS["products"][SLUG], slug=SLUG)
    end = datetime.fromisoformat("2026-12-01T08:00:00+00:00")
    assert app._sale_priced(rec, now=end - timedelta(seconds=1))["price_cents"] == 4000
    assert app._sale_priced(rec, now=end)["price_cents"] == 7000


def test_a_future_start_is_not_on_yet(monkeypatch):
    _set_sale(monkeypatch, price_cents=4000, starts_at=_iso(timedelta(hours=1)),
              ends_at=_iso(timedelta(days=1)))
    assert app._get_product(SLUG)["price_cents"] == 7000


@pytest.mark.parametrize("sale", [
    {"price_cents": 4000, "ends_at": "2099-12-01T08:00:00"},          # no zone
    {"price_cents": 7000, "ends_at": "2099-12-01T08:00:00Z"},         # not below regular
    {"price_cents": 9000, "ends_at": "2099-12-01T08:00:00Z"},
    {"price_cents": 0, "ends_at": "2099-12-01T08:00:00Z"},
    {"price_cents": "x", "ends_at": "2099-12-01T08:00:00Z"},
    {"price_cents": 4000, "ends_at": "soon"},
    {"price_cents": 4000},
    {"price_cents": 4000, "ends_at": "2099-12-01T08:00:00Z", "starts_at": "2026-11-01"},
])
def test_a_malformed_sale_is_ignored(monkeypatch, sale):
    _set_sale(monkeypatch, **sale)
    assert app._get_product(SLUG)["price_cents"] == 7000


def test_the_catalog_record_itself_is_not_changed(monkeypatch):
    _set_sale(monkeypatch, price_cents=4000, ends_at=_iso(timedelta(days=1)))
    app._get_product(SLUG)
    assert app._PRODUCTS["products"][SLUG]["price_cents"] == 7000
    assert "fixed_price" not in app._PRODUCTS["products"][SLUG]


def test_other_products_are_untouched(monkeypatch):
    _set_sale(monkeypatch, price_cents=4000, ends_at=_iso(timedelta(days=1)))
    p = app._get_product("nerve-pulse")
    assert "sale_ends_at" not in p and p["price_cents"] == \
        app._PRODUCTS["products"]["nerve-pulse"]["price_cents"]


# ---- the shelf count -----------------------------------------------------------------

@pytest.fixture
def shelf(monkeypatch):
    _set_sale(monkeypatch, price_cents=4000, ends_at=_iso(timedelta(days=1)))
    counts = {}
    monkeypatch.setattr(app._stock_counts, "enabled", lambda: True)
    monkeypatch.setattr(app._stock_counts, "countable", lambda p, slug=None: True)
    monkeypatch.setattr(app._stock_counts, "get_count", lambda cx, slug: counts.get(slug))
    return counts


def test_more_than_the_shelf_holds_is_refused(shelf):
    shelf[SLUG] = 5
    with pytest.raises(app.CheckoutError) as e:
        app._price_cart([{"slug": SLUG, "qty": 6}], ship=SHIP)
    assert str(e.value) == "Only 5 bottles of Pollen Power are left. Please lower the quantity."
    assert _lines(app._price_cart([{"slug": SLUG, "qty": 5}], ship=SHIP)) == \
        [(SLUG, 4000, 20000)]


def test_two_lines_of_it_are_added_together(shelf):
    shelf[SLUG] = 5
    with pytest.raises(app.CheckoutError):
        app._price_cart([{"slug": SLUG, "qty": 3}, {"slug": SLUG, "qty": 3}], ship=SHIP)


def test_no_count_or_a_read_failure_never_blocks(shelf, monkeypatch):
    assert app._sale_stock_refusal([{"slug": SLUG, "qty": 50}]) is None
    shelf[SLUG] = 1
    monkeypatch.setattr(app.db, "connect",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down")))
    assert app._sale_stock_refusal([{"slug": SLUG, "qty": 50}]) is None


def test_an_order_already_placed_is_not_refused(shelf):
    shelf[SLUG] = 1
    app._price_cart([{"slug": SLUG, "qty": 4}], ship=SHIP, allow_waitlist=True)


def test_counts_off_or_sale_over_means_no_check(shelf, monkeypatch):
    shelf[SLUG] = 1
    monkeypatch.setattr(app._stock_counts, "enabled", lambda: False)
    assert app._sale_stock_refusal([{"slug": SLUG, "qty": 4}]) is None
    monkeypatch.setattr(app._stock_counts, "enabled", lambda: True)
    _set_sale(monkeypatch, price_cents=4000, ends_at=_iso(timedelta(seconds=-1)))
    assert app._sale_stock_refusal([{"slug": SLUG, "qty": 4}]) is None


def test_zero_left_is_left_to_the_sold_out_refusal(shelf):
    shelf[SLUG] = 0
    assert app._sale_stock_refusal([{"slug": SLUG, "qty": 1}]) is None
