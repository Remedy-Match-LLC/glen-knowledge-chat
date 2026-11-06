"""A shelf count of 0 refuses a product on every buying path (Glen, 2026-11-06).

Plan: platform/plans/2026-11-06-stock-counts-in-the-app.md. The count hooks into the one
predicate, practitioner_portal.waitlist_block, so every path that refuses an
`out_of_stock` product refuses a counted-out one, with the same line:
"{name} is out of stock." A count above 0 never blocks. No count never blocks. With
STOCK_COUNTS_ENABLED off, nothing changes. A hand-set flag still wins over a count.
"""
import sqlite3

import pytest

import app
from dashboard import cart_store as CS
from dashboard import practitioner_portal as pp
from dashboard import stock_counts as SC
from dashboard import wholesale_checkout as WC

SS = "scar-silk"
LINE = "Scar Silk is out of stock."
URL = "/begin/product/scar-silk"
BUNDLE = "test-stock-bundle"
OTHER = "moisturize"


@pytest.fixture(autouse=True)
def env(monkeypatch, tmp_path):
    monkeypatch.setenv("STOCK_COUNTS_ENABLED", "1")
    monkeypatch.setattr(app, "CONSOLE_SECRET", "test-console-secret")
    path = str(tmp_path / "chat_log.db")
    monkeypatch.setattr(app, "LOG_DB", path)
    monkeypatch.setattr(app, "_PORTAL_CART_ENABLED", True)
    sent = []
    monkeypatch.setattr(SC, "_notify", lambda s, b: sent.append((s, b)))
    products = app._PRODUCTS.setdefault("products", {})
    monkeypatch.setitem(products, BUNDLE, {
        "name": "Test Stock Bundle", "bundle": True, "price_cents": 14000,
        "bundle_component_slugs": [{"slug": OTHER, "qty": 1}, {"slug": SS, "qty": 1}]})
    monkeypatch.setattr(pp.pricing, "_load_catalog", lambda: products)
    cx = sqlite3.connect(path)
    CS.init_cart_tables(cx)
    cx.close()
    SC._invalidate()
    yield products
    SC._invalidate()


def _set(slug, n):
    cx = sqlite3.connect(app.LOG_DB)
    try:
        SC.set_count(cx, slug, n, who="test", reason="shelf count")
    finally:
        cx.close()


@pytest.fixture()
def client():
    app.app.config["TESTING"] = True
    return app.app.test_client()


def _refused(r):
    assert r.status_code == 400, (r.status_code, r.get_data(as_text=True)[:300])
    body = r.get_json() or {}
    assert body.get("error") == LINE, body
    assert body.get("url") == URL, body


def _all_paths_refuse(client, products):
    assert pp.is_orderable(SS, products) is False
    assert pp.waitlist_refusal(pp.waitlist_block(SS, products)) == LINE
    assert app._is_orderable(SS) is False
    assert app._is_orderable(SS, products[SS]) is False
    # a bundle holding it, via its component
    assert pp.is_orderable(BUNDLE, products) is False
    assert pp.waitlist_refusal(pp.waitlist_block(BUNDLE, products)) == LINE
    # product page and bundle page
    for slug in (SS, BUNDLE):
        d = client.get(f"/begin/product-page-data/{slug}").get_json()
        assert d["out_of_stock_line"] == LINE
        assert d["price"] == "" and d["price_cents"] is None and "cta_url" not in d
    # buy link, begin checkout, cart, cart pricing, console order line, programs
    r = client.get(f"/begin/buy/{SS}")
    assert r.status_code == 302 and r.headers["Location"].endswith(URL)
    _refused(client.post(f"/begin/checkout/{SS}", json={"email": "a@x.com", "method": "card"}))
    for slug in (SS, BUNDLE):
        _refused(client.post("/api/cart/add", json={"slug": slug}))
    with pytest.raises(app.CheckoutError) as e:
        app._price_cart([{"slug": OTHER, "qty": 1}, {"slug": SS, "qty": 1}],
                        ship={"country": "US"})
    assert str(e.value) == LINE
    assert app._cart_waitlist_refusal([{"slug": SS, "qty": 1}]) == LINE
    keep, nr = app._split_not_ready([{"slug": OTHER}, {"slug": SS}])
    assert keep == [{"slug": OTHER}] and nr == [{"name": "Scar Silk", "url": URL, "line": LINE}]
    # wholesale checkout
    res = WC.build_order([{"slug": SS, "qty": 1}], {"id": "x"}, catalog=products)
    assert res["ok"] is False and res["error"] == LINE


def _all_paths_allow(client, products):
    assert pp.is_orderable(SS, products) is True
    assert pp.waitlist_block(SS, products) is None
    assert pp.waitlist_block(BUNDLE, products) is None
    assert app._is_orderable(SS) is True
    d = client.get(f"/begin/product-page-data/{SS}").get_json()
    assert "out_of_stock_line" not in d and d["price_cents"]
    d = client.get(f"/begin/product-page-data/{BUNDLE}").get_json()
    assert "out_of_stock_line" not in d and d["price_cents"]
    assert app._cart_waitlist_refusal([{"slug": SS, "qty": 1}]) is None
    keep, nr = app._split_not_ready([{"slug": OTHER}, {"slug": SS}])
    assert nr == [] and len(keep) == 2
    r = client.post("/api/cart/add", json={"slug": SS})
    assert r.status_code == 200, r.get_data(as_text=True)[:300]


def test_count_zero_blocks_every_buying_path(client, env):
    _set(SS, 0)
    _all_paths_refuse(client, env)


@pytest.mark.parametrize("n", [1, 2, 50])
def test_count_above_zero_never_blocks(client, env, n):
    _set(SS, n)
    _all_paths_allow(client, env)


def test_no_count_never_blocks(client, env):
    _set(OTHER, 0)      # a different product is sold out; Scar Silk has no row
    assert pp.is_orderable(SS, env) is True
    assert app._is_orderable(SS) is True
    d = client.get(f"/begin/product-page-data/{SS}").get_json()
    assert "out_of_stock_line" not in d and d["price_cents"]


def test_no_count_at_all_never_blocks(client, env):
    _all_paths_allow(client, env)


def test_restocking_puts_it_back_on_sale_at_once(client, env):
    _set(SS, 0)
    assert app._is_orderable(SS) is False
    _set(SS, 12)
    _all_paths_allow(client, env)


def test_flag_off_changes_nothing_and_reads_no_database(client, env, monkeypatch):
    _set(SS, 0)
    monkeypatch.setenv("STOCK_COUNTS_ENABLED", "")
    SC._invalidate()
    calls = []
    monkeypatch.setattr(SC, "zero_slugs", lambda: calls.append(1) or frozenset({SS}))
    _all_paths_allow(client, env)
    assert calls == []


def test_a_hand_set_flag_wins_over_a_count(client, env, monkeypatch):
    _set(SS, 40)
    monkeypatch.setitem(env, SS, dict(env[SS], out_of_stock="Back in December."))
    assert pp.waitlist_refusal(pp.waitlist_block(SS, env)) == LINE + " Back in December."


def test_a_made_to_order_product_is_never_counted_out(client, env, monkeypatch):
    _set(SS, 0)
    monkeypatch.setitem(env, SS, dict(env[SS], made_to_order=True))
    assert pp.is_orderable(SS, env) is True
    with pytest.raises(ValueError):
        _set(SS, 3)


def test_a_waitlist_product_keeps_its_own_refusal(client, env, monkeypatch):
    _set(SS, 0)
    monkeypatch.setitem(env, SS, dict(env[SS], waitlist_only=True))
    assert pp.waitlist_refusal(pp.waitlist_block(SS, env)).startswith(
        "Scar Silk is not ready to order yet.")


def test_an_unreadable_database_fails_open(client, env, monkeypatch):
    _set(SS, 0)
    SC._invalidate()
    monkeypatch.setattr(app, "LOG_DB", "/nonexistent-dir/x/chat_log.db")
    assert app._is_orderable(SS) is True
