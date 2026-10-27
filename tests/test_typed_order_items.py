"""A new item typed into the New Order list.

Glen, 2026-10-26, relayed by production: "a way to type in a new item to the list".
Glen, 2026-10-27, platform tab, "yes": a typed item always invoices at the typed price,
and Pure Powder rules apply when its name says Pure Powder. It never reaches the store,
and a catalog product with the same slug supersedes it.
"""
import uuid

import pytest

from dashboard.typed_items import slugify

app_mod = pytest.importorskip("app")

HDR = {"X-Console-Key": app_mod.CONSOLE_SECRET}
EMAIL = "typed-item-test@example.com"
SHIP = {"name": "T", "street": "1 A St", "city": "Hilo", "state": "HI", "zip": "96720",
        "country": "US"}


def _client():
    return app_mod.app.test_client()


def _add(name, cents=2500, bt="30 g"):
    r = _client().post("/api/orders/typed-items", headers=HDR,
                       json={"name": name, "price_cents": cents, "bottle_type": bt})
    return r


def _new(stem, cents=2500, bt="30 g"):
    name = "%s %s" % (stem, uuid.uuid4().hex[:8])
    r = _add(name, cents, bt)
    assert r.status_code == 200, r.data
    return r.get_json()["item"]


# A member invoice: the Biofield line flips the remedies to member pricing, so an
# automatic discount would show if the typed item took one.
def _priced(*lines, **kw):
    lines = [{"slug": "biofield-analysis", "qty": 1},
             {"slug": "paracleanse", "qty": 6}] + list(lines)
    kw.setdefault("pickup", True)
    kw.setdefault("ship", None)
    out = app_mod._price_inhouse_invoice(lines, email=EMAIL, **kw)
    assert out is not None
    return out


def _recs(out):
    return {r["slug"]: r for r in out["items_rec"]}


def test_a_typed_item_invoices_at_the_typed_price_on_a_member_invoice():
    it = _new("Typed Tincture")
    recs = _recs(_priced({"slug": it["slug"], "qty": 4}))
    assert recs["paracleanse"]["unit_cents"] < 7000   # the member rate is live here
    assert recs[it["slug"]]["unit_cents"] == 2500
    assert recs[it["slug"]]["line_cents"] == 10000
    assert recs[it["slug"]]["name"] == it["name"]


def test_a_client_special_does_not_lower_a_typed_item(monkeypatch):
    it = _new("Typed Special")
    monkeypatch.setattr(app_mod, "_client_special_for", lambda *a, **k: 100)
    assert _recs(_priced({"slug": it["slug"], "qty": 1}))[it["slug"]]["unit_cents"] == 2500


def test_a_price_typed_on_the_line_still_wins():
    it = _new("Typed Edit")
    rec = _recs(_priced({"slug": it["slug"], "qty": 1, "unit_cents": 1999}))[it["slug"]]
    assert rec["unit_cents"] == 1999 and rec.get("override")


def test_a_typed_pure_powder_follows_the_pure_powder_rules():
    it = _new("Typed Pure Powder", cents=4000)
    p = app_mod._invoice_product(it["slug"])
    assert app_mod._is_pure_powder(p) and not app_mod._typed_fixed_price(p)
    eng = app_mod._engine_item(p, 1)
    assert eng["volume_eligible"] is False
    assert eng["product"]["sku_discount_floor_pct"] == 0.75
    # The same rule a catalog Pure Powder gets: list price on a member invoice.
    recs = _recs(_priced({"slug": it["slug"], "qty": 3},
                         {"slug": "pterostilbene-pure-powder", "qty": 3}))
    assert recs[it["slug"]]["unit_cents"] == 4000
    assert recs["pterostilbene-pure-powder"]["unit_cents"] == 4000


def test_the_preview_shows_the_typed_price(monkeypatch):
    it = _new("Typed Preview")
    monkeypatch.setattr(app_mod, "_client_special_for", lambda *a, **k: 100)
    r = _client().post("/api/orders/price-preview", headers=HDR, json={
        "email": EMAIL, "lines": [{"slug": "biofield-analysis", "qty": 1},
                                  {"slug": it["slug"], "qty": 2}]})
    assert r.status_code == 200, r.data
    line = next(l for l in r.get_json()["lines"] if l["slug"] == it["slug"])
    assert line["effective_unit_cents"] == 2500 and line["line_cents"] == 5000


def test_the_order_list_offers_it_and_the_store_does_not():
    it = _new("Typed Listed")
    r = _client().get("/api/products?all=1", headers=HDR)
    row = next(p for p in r.get_json()["products"] if p["slug"] == it["slug"])
    assert row["typed"] is True and row["price_cents"] == 2500
    # Everything the store, cart and chat use reads the catalog only.
    assert app_mod._get_product(it["slug"]) is None
    out = app_mod._price_cart([{"slug": it["slug"], "qty": 1}], ship=SHIP)
    assert not out["items_rec"]


def test_a_typed_item_is_packed_like_a_catalog_item_of_its_package():
    it = _new("Typed Ship", cents=4000, bt="30 g")
    ship = {"name": "T", "street": "1 A St", "city": "Hilo", "state": "HI",
            "zip": "96720", "country": "US"}
    kw = dict(email=EMAIL, pickup=False, ship=ship, strict_packaging=True)
    typed = app_mod._price_inhouse_invoice([{"slug": it["slug"], "qty": 1}], **kw)
    catalog = app_mod._price_inhouse_invoice(
        [{"slug": "pterostilbene-pure-powder", "qty": 1}], **kw)
    assert typed["shipping_cents"] == catalog["shipping_cents"] > 0


def test_a_catalog_name_and_a_retired_name_are_refused():
    r = _add("Pterostilbene Pure Powder")
    assert r.status_code == 409 and "already in the list" in r.get_json()["error"]
    retired = next(s for s, p in app_mod._PRODUCTS["products"].items()
                   if p.get("inactive") and p.get("name")
                   and slugify(p["name"]) == s)
    r = _add(app_mod._PRODUCTS["products"][retired]["name"])
    assert r.status_code == 409 and "retired" in r.get_json()["error"]


def test_the_catalog_supersedes_a_typed_item(monkeypatch):
    it = _new("Typed Later Listed")
    cat = dict(app_mod._PRODUCTS["products"])
    cat[it["slug"]] = {"name": it["name"] + " (catalog)", "price_cents": 5500}
    monkeypatch.setitem(app_mod._PRODUCTS, "products", cat)
    assert app_mod._invoice_product(it["slug"])["price_cents"] == 5500
    rows = _client().get("/api/products?all=1", headers=HDR).get_json()["products"]
    assert not any(p["slug"] == it["slug"] and p.get("typed") for p in rows)


def test_retiring_that_catalog_record_does_not_bring_the_typed_item_back(monkeypatch):
    it = _new("Typed Then Retired")
    cat = dict(app_mod._PRODUCTS["products"])
    cat[it["slug"]] = {"name": it["name"], "price_cents": 5500, "inactive": True}
    monkeypatch.setitem(app_mod._PRODUCTS, "products", cat)
    assert app_mod._invoice_product(it["slug"]) is None


def test_a_repeat_name_and_bad_input_are_refused():
    it = _new("Typed Twice")
    r = _add(it["name"], cents=9900)
    assert r.status_code == 400 and "already in the list at $25.00" in r.get_json()["error"]
    assert _add("Typed No Package %s" % uuid.uuid4().hex[:6], bt="").status_code == 400
    assert _add("Typed Free %s" % uuid.uuid4().hex[:6], cents=0).status_code == 400
    assert _add("", cents=100).status_code == 400


def test_only_the_owner_can_type_an_item():
    r = _client().post("/api/orders/typed-items",
                       json={"name": "x", "price_cents": 100, "bottle_type": "30 g"})
    assert r.status_code == 401
