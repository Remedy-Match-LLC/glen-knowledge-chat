"""Runs begin-cart.html's Zelle code in a real browser with the server calls mocked.

Review round 2 (2026-10-10) found the page could drop an order key it still needed:
on a lost reply, on another shopper's lookup, or when storage failed. These tests drive
the page itself rather than grepping its source.
"""
import json
from pathlib import Path

import pytest

pytest.importorskip("playwright.sync_api")
from playwright.sync_api import sync_playwright  # noqa: E402

PAGE = (Path(__file__).resolve().parent.parent / "static" / "begin-cart.html").read_text()
ORIGIN = "http://cart.test"
CART = {"items": [{"slug": "brain-boost", "name": "Brain Boost", "qty": 1,
                   "format": "bottle", "available": True}]}
ZELLE = {"label": "Zelle (US)", "to": "pay@example.com", "memo": "Order 12",
         "pay_link": "", "bank_url": "https://enroll.zellepay.com/", "note": "n"}


def _order(ref, oid=12):
    return {"order_id": oid, "order_ref": ref, "total_cents": 8265, "shipping_cents": 1265,
            "pay_instructions": dict(ZELLE, memo=f"Order {oid}")}


def _run(handlers, steps, storage=None):
    """handlers: {path: fn(request_json) -> (status, body) or None to abort}."""
    seen = {"posts": []}
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page()

        def route(r):
            url = r.request.url[len(ORIGIN):].split("?")[0]
            if url == "/begin/cart":
                return r.fulfill(status=200, content_type="text/html", body=PAGE)
            if url.endswith(".js") or url.endswith(".css"):
                return r.fulfill(status=200, body="")
            if url == "/api/cart" and r.request.method == "GET":
                return r.fulfill(status=200, content_type="application/json",
                                 body=json.dumps(handlers.get("cart", CART)))
            fn = handlers.get(url)
            if fn is None:
                return r.fulfill(status=404, body="{}")
            body = json.loads(r.request.post_data or "{}")
            seen["posts"].append((url, body))
            out = fn(body)
            if out is None:
                return r.abort()
            return r.fulfill(status=out[0], content_type="application/json",
                             body=json.dumps(out[1]))
        pg.route("**/*", route)
        if storage is not None:
            pg.add_init_script(
                f"try{{localStorage.setItem('rm_cart_zelle_keys', {json.dumps(json.dumps(storage))})}}catch(e){{}}")
        pg.goto(ORIGIN + "/begin/cart")
        pg.wait_for_selector(".line")
        steps(pg, seen)
        b.close()
    return seen


def _keys(pg):
    return pg.evaluate("JSON.parse(localStorage.getItem('rm_cart_zelle_keys') || '[]')")


def test_a_zelle_reply_shows_the_instructions_and_keeps_its_key():
    def checkout(body):
        return 200, {"ok": True, "method": "zelle", **_order(body["checkout_request_id"])}

    def steps(pg, seen):
        pg.check('input[name="payMethod"][value="zelle"]')
        pg.click("#cart-checkout")
        pg.wait_for_selector(".zelle-box")
        text = pg.inner_text("#zelle-done")
        assert "#12" in text and "$82.65" in text and "pay@example.com" in text and "Order 12" in text
        posted = [b for u, b in seen["posts"] if u == "/api/cart/checkout"][0]
        assert posted["method"] == "zelle"
        assert [k["r"] for k in _keys(pg)] == [posted["checkout_request_id"]]
    _run({"/api/cart/checkout": checkout, "/api/cart/zelle-orders": lambda b: (200, {"ok": True, "orders": [], "closed": []})}, steps)


def test_a_lost_reply_is_recovered_by_lookup():
    state = {"ref": None, "asks": 0}

    def checkout(body):
        state["ref"] = body["checkout_request_id"]
        return None   # the connection drops after the server took the order

    def lookup(body):
        state["asks"] += 1
        if state["asks"] < 2:   # the order is still being written on the first ask
            return 200, {"ok": True, "orders": [], "closed": []}
        return 200, {"ok": True, "orders": [_order(state["ref"])], "closed": []}

    def steps(pg, seen):
        pg.check('input[name="payMethod"][value="zelle"]')
        pg.click("#cart-checkout")
        pg.wait_for_selector(".zelle-box", timeout=15000)
        assert "Order 12" in pg.inner_text("#zelle-done")
        assert pg.inner_text("#cart-msg") == ""
        assert [k["r"] for k in _keys(pg)] == [state["ref"]]
    _run({"/api/cart/checkout": checkout, "/api/cart/zelle-orders": lookup}, steps)


def test_only_closed_keys_are_forgotten():
    import time
    now = int(time.time() * 1000)
    keep_unpaid, closed, other_shopper = "a" * 32, "b" * 32, "c" * 32

    def lookup(body):
        assert set(body["refs"]) == {keep_unpaid, closed, other_shopper}
        return 200, {"ok": True, "orders": [_order(keep_unpaid)], "closed": [closed]}

    def steps(pg, seen):
        pg.wait_for_selector(".zelle-box")
        assert sorted(k["r"] for k in _keys(pg)) == [keep_unpaid, other_shopper]
    _run({"/api/cart/zelle-orders": lookup}, steps,
         storage=[{"r": keep_unpaid, "at": now}, {"r": closed, "at": now},
                  {"r": other_shopper, "at": now}])


def test_a_failed_lookup_forgets_nothing():
    keys = [{"r": "d" * 32, "at": 1}]

    def steps(pg, seen):
        pg.wait_for_timeout(500)
        assert _keys(pg) == keys
    _run({"/api/cart/zelle-orders": lambda b: (500, {"ok": False})}, steps, storage=keys)


def test_a_form_refused_on_the_page_saves_no_key():
    def steps(pg, seen):
        pg.check('input[name="payMethod"][value="zelle"]')
        pg.click("#ship-toggle")
        pg.click("#cart-checkout")
        pg.wait_for_timeout(300)
        assert _keys(pg) == []
        assert not [u for u, b in seen["posts"] if u == "/api/cart/checkout"]
    _run({"/api/cart/checkout": lambda b: (200, {"ok": True})}, steps)


def test_every_value_in_the_box_is_escaped():
    evil = '<img src=x onerror="window.__xss=1">'

    def checkout(body):
        o = _order(body["checkout_request_id"])
        o["order_id"] = evil
        o["pay_instructions"] = dict(ZELLE, to=evil, memo=evil, note=evil, pay_link='javascript:"><b>x</b>')
        return 200, {"ok": True, "method": "zelle", **o}

    def steps(pg, seen):
        pg.check('input[name="payMethod"][value="zelle"]')
        pg.click("#cart-checkout")
        pg.wait_for_selector(".zelle-box")
        assert pg.evaluate("window.__xss") is None
        assert pg.query_selector("#zelle-done img") is None
        assert pg.query_selector("#zelle-done b") is None
    _run({"/api/cart/checkout": checkout,
          "/api/cart/zelle-orders": lambda b: (200, {"ok": True, "orders": [], "closed": []})}, steps)


def test_a_server_error_after_the_order_saved_is_recovered_not_retried():
    state = {"ref": None}

    def checkout(body):
        state["ref"] = body["checkout_request_id"]
        return 500, {"ok": False, "error": "TypeError: boom"}

    def lookup(body):
        return 200, {"ok": True, "orders": [_order(state["ref"])], "closed": []}

    def steps(pg, seen):
        pg.check('input[name="payMethod"][value="zelle"]')
        pg.click("#cart-checkout")
        pg.wait_for_selector(".zelle-box", timeout=15000)
        assert "try again" not in pg.inner_text("#cart-msg").lower()
    _run({"/api/cart/checkout": checkout, "/api/cart/zelle-orders": lookup}, steps)
