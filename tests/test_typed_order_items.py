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
    r = _client().get("/api/products?all=1&typed=1", headers=HDR)
    row = next(p for p in r.get_json()["products"] if p["slug"] == it["slug"])
    assert row["typed"] is True and row["price_cents"] == 2500
    # Rewards and related products read the list without typed=1 and save a catalog slug.
    for url in ("/api/products?all=1", "/api/products"):
        rows = _client().get(url, headers=HDR).get_json()["products"]
        assert not any(p["slug"] == it["slug"] for p in rows), url
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
    # Many jars need a bigger box. Equal at 12 as well, and dearer than one jar, so the
    # typed item is really packed at its own package and count.
    typed12 = app_mod._price_inhouse_invoice([{"slug": it["slug"], "qty": 12}], **kw)
    catalog12 = app_mod._price_inhouse_invoice(
        [{"slug": "pterostilbene-pure-powder", "qty": 12}], **kw)
    assert typed12["shipping_cents"] == catalog12["shipping_cents"] > typed["shipping_cents"]


def test_a_failed_typed_lookup_raises_rather_than_dropping_the_line(monkeypatch):
    it = _new("Typed Outage")
    from dashboard import typed_items as ti

    def boom(*a, **k):
        raise RuntimeError("database down")
    monkeypatch.setattr(ti, "get", boom)
    with pytest.raises(RuntimeError):
        _priced({"slug": it["slug"], "qty": 1})


def test_a_repeat_of_the_same_item_returns_it():
    it = _new("Typed Retry")
    r = _add(it["name"], cents=2500, bt="30 g")
    assert r.status_code == 200 and r.get_json()["item"]["slug"] == it["slug"]


def test_two_adds_at_once_do_not_error(monkeypatch):
    from dashboard import typed_items as ti
    name = "Typed Race %s" % uuid.uuid4().hex[:8]
    assert _add(name).status_code == 200
    real_get, calls = ti.get, []

    def first_miss(cx, slug):   # the second request read before the first one wrote
        calls.append(slug)
        return None if len(calls) == 1 else real_get(cx, slug)
    monkeypatch.setattr(ti, "get", first_miss)
    r = _add(name)
    assert r.status_code == 200, r.data
    r = _add(name, cents=9900)
    assert r.status_code == 400


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
    rows = _client().get("/api/products?all=1&typed=1", headers=HDR).get_json()["products"]
    assert not any(p["slug"] == it["slug"] and p.get("typed") for p in rows)


def test_a_retired_catalog_record_does_not_drop_a_typed_line(monkeypatch):
    it = _new("Typed Then Retired")
    cat = dict(app_mod._PRODUCTS["products"])
    cat[it["slug"]] = {"name": it["name"], "price_cents": 5500, "inactive": True}
    monkeypatch.setitem(app_mod._PRODUCTS, "products", cat)
    recs = _recs(_priced({"slug": it["slug"], "qty": 2}))
    assert recs[it["slug"]]["unit_cents"] == 2500


def test_a_live_catalog_product_of_the_same_name_supersedes_it(monkeypatch):
    it = _new("Typed Named Later")
    cat = dict(app_mod._PRODUCTS["products"])
    cat["some-other-slug-" + it["slug"][-8:]] = {"name": it["name"], "price_cents": 5500}
    monkeypatch.setitem(app_mod._PRODUCTS, "products", cat)
    other = "some-other-slug-" + it["slug"][-8:]
    recs = _recs(_priced({"slug": it["slug"], "qty": 1}))
    # The line becomes the catalog product, so every later lookup by slug finds it.
    assert it["slug"] not in recs and recs[other]["unit_cents"] == 5500
    rows = _client().get("/api/products?all=1&typed=1", headers=HDR).get_json()["products"]
    assert not any(p["slug"] == it["slug"] for p in rows)


def _claim_by_name(monkeypatch, it, **extra):
    other = "claimed-" + it["slug"][-8:]
    cat = dict(app_mod._PRODUCTS["products"])
    cat[other] = {"name": it["name"], "price_cents": 4000, "bottle_type": "30 g", **extra}
    monkeypatch.setitem(app_mod._PRODUCTS, "products", cat)
    return other


def test_a_line_superseded_by_name_still_ships(monkeypatch):
    it = _new("Typed Claimed Ship", cents=4000)
    _claim_by_name(monkeypatch, it)
    kw = dict(email=EMAIL, pickup=False, ship=SHIP, strict_packaging=True)
    claimed = app_mod._price_inhouse_invoice([{"slug": it["slug"], "qty": 12}], **kw)
    catalog = app_mod._price_inhouse_invoice(
        [{"slug": "pterostilbene-pure-powder", "qty": 12}], **kw)
    assert claimed["shipping_cents"] == catalog["shipping_cents"] > 0
    assert app_mod._typed_products_in([{"slug": it["slug"]}])


def test_an_unknown_slug_is_not_mapped_by_name(monkeypatch):
    # Only a typed item is mapped to the catalog product that took its name.
    cat = dict(app_mod._PRODUCTS["products"])
    cat["claim-x"] = {"name": "Never Typed Thing %s" % uuid.uuid4().hex[:6],
                      "price_cents": 4000}
    monkeypatch.setitem(app_mod._PRODUCTS, "products", cat)
    from dashboard.typed_items import slugify
    assert app_mod._invoice_product(slugify(cat["claim-x"]["name"])) is None


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


def test_a_catalog_name_whose_slug_differs_is_refused():
    r = _add("Myelin Syntropy Powder")
    assert r.status_code == 409 and "already in the list" in r.get_json()["error"]
    r = _add("emotional stress release (MB5)")
    assert r.status_code == 409 and "retired" in r.get_json()["error"]


def test_a_true_or_fractional_price_is_refused():
    assert _add("Typed Bool %s" % uuid.uuid4().hex[:6], cents=True).status_code == 400
    assert _add("Typed Frac %s" % uuid.uuid4().hex[:6], cents=2599.9).status_code == 400


def test_a_customer_edit_keeps_the_typed_line_and_cannot_add_one(monkeypatch):
    it = _new("Typed On Invoice")
    other = _new("Typed Not On Invoice")
    order = {"id": 990001, "source": "in-house", "external_ref": "INH-TYPED",
             "email": EMAIL, "name": "T", "status": "proposed", "channel": "pickup",
             "items": [{"slug": "paracleanse", "qty": 1, "unit_cents": 7000},
                       {"slug": it["slug"], "qty": 1, "unit_cents": 2500}]}
    captured = {}
    monkeypatch.setattr(app_mod, "_invoice_order_for_token", lambda _t: order)

    def fake_reprice(_cx, _order, lines, **kwargs):
        captured["lines"] = lines
        return ({"items_rec": lines, "subtotal_cents": 0, "discount_cents": 0,
                 "adjustment_cents": 0, "shipping_cents": 0, "get_cents": 0,
                 "points_redeemed_cents": 0, "total_cents": 0}, False)
    monkeypatch.setattr(app_mod, "_reprice_and_persist_invoice", fake_reprice)
    monkeypatch.setattr(app_mod._bos_orders, "get_order", lambda _cx, _oid: order)
    monkeypatch.setattr(app_mod, "_invoice_summary", lambda _o: {"total_cents": 0})
    monkeypatch.setattr(app_mod._inbox, "send_email", lambda *a, **k: None)
    r = _client().post("/api/invoice/tok/update", json={"lines": [
        {"slug": "paracleanse", "qty": 2, "i": 0}, {"slug": it["slug"], "qty": 3, "i": 1},
        {"slug": other["slug"], "qty": 1}]})
    assert r.status_code == 200, r.data
    slugs = {l["slug"]: l["qty"] for l in captured["lines"]}
    assert slugs == {"paracleanse": 2, it["slug"]: 3}


def test_a_customer_edit_keeps_a_line_superseded_by_name(monkeypatch):
    it = _new("Typed Claimed Edit")
    _claim_by_name(monkeypatch, it)
    order = {"id": 990002, "source": "in-house", "external_ref": "INH-TYPED2",
             "email": EMAIL, "name": "T", "status": "proposed", "channel": "pickup",
             "items": [{"slug": it["slug"], "qty": 1, "unit_cents": 2500}]}
    captured = {}
    monkeypatch.setattr(app_mod, "_invoice_order_for_token", lambda _t: order)

    def fake_reprice(_cx, _order, lines, **kwargs):
        captured["lines"] = lines
        return ({"items_rec": lines, "subtotal_cents": 0, "discount_cents": 0,
                 "adjustment_cents": 0, "shipping_cents": 0, "get_cents": 0,
                 "points_redeemed_cents": 0, "total_cents": 0}, False)
    monkeypatch.setattr(app_mod, "_reprice_and_persist_invoice", fake_reprice)
    monkeypatch.setattr(app_mod._bos_orders, "get_order", lambda _cx, _oid: order)
    monkeypatch.setattr(app_mod, "_invoice_summary", lambda _o: {"total_cents": 0})
    monkeypatch.setattr(app_mod._inbox, "send_email", lambda *a, **k: None)
    r = _client().post("/api/invoice/tok/update",
                       json={"lines": [{"slug": it["slug"], "qty": 2, "i": 0}]})
    assert r.status_code == 200, r.data
    assert [(l["slug"], l["qty"]) for l in captured["lines"]] == [(it["slug"], 2)]


def test_combining_shipments_packs_the_typed_items(monkeypatch):
    it = _new("Typed Combined", cents=4000)
    members = [
        {"id": 1, "email": "a@example.com", "pay_status": "unpaid", "status": "proposed",
         "items": [{"slug": it["slug"], "qty": 2}], "shipping_cents": 0, "total_cents": 8000},
        {"id": 2, "email": "b@example.com", "pay_status": "unpaid", "status": "proposed",
         "items": [{"slug": "paracleanse", "qty": 1}], "shipping_cents": 0,
         "total_cents": 7000},
    ]
    monkeypatch.setattr(app_mod, "_family_plan_enabled", lambda: False)
    monkeypatch.setattr(app_mod._bos_orders, "orders_in_group", lambda _cx, _sid: members)
    monkeypatch.setattr(app_mod._bos_combined_shipments, "get_shipment",
                        lambda _cx, _sid: {"ship_to": SHIP})
    monkeypatch.setattr(app_mod._bos_orders, "set_order_shipping", lambda *a, **k: None)
    monkeypatch.setattr(app_mod._bos_orders, "set_order_overpay_credit", lambda *a, **k: None)
    seen = []
    real = app_mod._price_cart

    def spy(cart, **kw):
        out = real(cart, **kw)
        seen.append(({c["slug"] for c in cart}, out["shipping_cents"]))
        return out
    monkeypatch.setattr(app_mod, "_price_cart", spy)
    app_mod._recompute_combined_shipping(None, 1)
    alone = [ship for slugs, ship in seen if slugs == {it["slug"]}]
    assert alone and alone[0] > 0


def test_package_must_be_a_known_container():
    assert _add("Typed Odd %s" % uuid.uuid4().hex[:6], bt="Weird Box").status_code == 400
    assert _add("Typed Own %s" % uuid.uuid4().hex[:6], bt="own-box").status_code == 400
    types = _client().get("/api/orders/typed-items", headers=HDR).get_json()["bottle_types"]
    assert "30 g" in types and "own-box" not in types


def test_name_and_price_limits():
    assert _add("x" * 121, cents=100).status_code == 400
    assert _add("!!!", cents=100).status_code == 400
    assert _add("Typed Dear %s" % uuid.uuid4().hex[:6], cents=1_000_001).status_code == 400
    assert _add("Typed Top %s" % uuid.uuid4().hex[:6], cents=1_000_000).status_code == 200


def test_a_repeat_with_a_different_package_or_spelling_is_refused():
    it = _new("Typed Package")
    assert _add(it["name"], cents=2500, bt="30 Caps").status_code == 400
    assert _add(it["name"].upper(), cents=2500, bt="30 g").status_code == 400


def test_a_non_owner_cannot_type_an_item(monkeypatch):
    from dashboard import rbac
    monkeypatch.setattr(app_mod, "_bos_actor", lambda: rbac.Actor(role="va"))
    r = _client().post("/api/orders/typed-items",
                       json={"name": "Typed VA", "price_cents": 100, "bottle_type": "30 g"})
    assert r.status_code == 401


def test_an_edited_typed_price_is_not_capped_by_a_client_special(monkeypatch):
    it = _new("Typed Edit Special")
    monkeypatch.setattr(app_mod, "_client_special_for", lambda *a, **k: 100)
    rec = _recs(_priced({"slug": it["slug"], "qty": 1, "unit_cents": 3000}))[it["slug"]]
    assert rec["unit_cents"] == 3000
    r = _client().post("/api/orders/price-preview", headers=HDR, json={
        "email": EMAIL, "lines": [{"slug": it["slug"], "qty": 1, "unit_cents": 3000}]})
    line = next(l for l in r.get_json()["lines"] if l["slug"] == it["slug"])
    assert line["effective_unit_cents"] == 3000


def test_the_shipping_preview_packs_a_typed_item():
    it = _new("Typed Ship Preview", cents=4000)

    def quote(slug):
        r = _client().post("/api/orders/shipping-preview", headers=HDR, json={
            "lines": [{"slug": slug, "qty": 12}], "address": SHIP, "name": "T"})
        assert r.status_code == 200, r.data
        return r.get_json()["shipping_cents"]
    assert quote(it["slug"]) == quote("pterostilbene-pure-powder") > 0


# ── A typed service (money, 2026-11-16: the "Remedy Match" line) ───────────────

def test_service_is_offered_first_and_accepted():
    types = _client().get("/api/orders/typed-items", headers=HDR).get_json()["bottle_types"]
    assert types[0] == "service" and types.count("service") == 1
    it = _new("Typed Service", cents=10000, bt="service")
    assert it["bottle_type"] == "service"


def test_a_typed_service_ships_nothing():
    """Alone it costs no postage; beside a bottle it adds none."""
    svc = _new("Typed Svc Ship", cents=10000, bt="service")
    kw = dict(email=EMAIL, pickup=False, ship=SHIP, strict_packaging=True)
    alone = app_mod._price_inhouse_invoice([{"slug": svc["slug"], "qty": 1}], **kw)
    assert alone["shipping_cents"] == 0
    jar = [{"slug": "pterostilbene-pure-powder", "qty": 1}]
    with_svc = app_mod._price_inhouse_invoice(jar + [{"slug": svc["slug"], "qty": 1}], **kw)
    without = app_mod._price_inhouse_invoice(jar, **kw)
    assert with_svc["shipping_cents"] == without["shipping_cents"] > 0
    assert _recs(with_svc)[svc["slug"]].get("service") is True


def test_a_typed_service_at_zero_keeps_a_zero_total():
    """At $0 on the line, a service order totals $0, so No charge can settle it."""
    svc = _new("Typed Svc Zero", cents=10000, bt="service")
    out = app_mod._price_inhouse_invoice([{"slug": svc["slug"], "qty": 1, "unit_cents": 0}],
                                         email=EMAIL, pickup=False, ship=SHIP,
                                         strict_packaging=True)
    assert out["shipping_cents"] == 0
    assert sum(int(r.get("line_cents") or 0) for r in out["items_rec"]) == 0
    # The amount payable, and the two flags that stop the $0 auto-pay (round 2).
    assert out["total_cents"] == 0
    assert out["shipping_fell_back"] is False and out["total_floored"] is False


def test_a_bottle_package_is_still_not_a_service():
    it = _new("Typed Jar", cents=4000, bt="30 g")
    p = app_mod._typed_item_product(it["slug"])
    assert not p.get("service") and p["bottle_type"] == "30 g"


def test_a_typed_service_counts_as_nothing_to_pack():
    """The order form, card and combine total must not show a service as a bottle
    (review round 1). Raw form lines carry no service flag, so the lookup must."""
    svc = _new("Typed Svc Count", cents=10000, bt="service")
    jar = _new("Typed Svc Jar", cents=4000, bt="30 g")
    raw = [{"slug": svc["slug"], "qty": 2}]
    assert app_mod._order_physical_units({"items": raw}) == 0
    assert app_mod._order_pack_breakdown({"items": raw})["bottle_units"] == 0
    mixed = raw + [{"slug": jar["slug"], "qty": 1}]
    assert app_mod._order_physical_units({"items": mixed}) == 1
    assert app_mod._order_pack_breakdown({"items": mixed})["bottle_units"] == 1
    # A stored line marked service also counts nothing, with no catalog entry at all.
    from dashboard import orders as _O
    assert _O.physical_units([{"slug": "not-in-any-catalog", "qty": 1, "service": True}], {}) == 0
    assert _O.pack_breakdown([{"slug": "not-in-any-catalog", "qty": 1, "service": True}],
                             {})["bottle_units"] == 0


# ── Retire (Glen, 2026-11-20, platform tab, "ok to build the retire button") ──

def _retire(slug, headers=HDR):
    return _client().post("/api/orders/typed-items/%s/retire" % slug, headers=headers)


def _picker_slugs():
    rows = _client().get("/api/products?all=1&typed=1", headers=HDR).get_json()["products"]
    return {p["slug"] for p in rows}


def test_a_retired_item_leaves_the_order_list():
    it = _new("Typed Retire")
    assert it["slug"] in _picker_slugs()
    r = _retire(it["slug"])
    assert r.status_code == 200 and r.get_json()["item"]["retired"] is True
    assert it["slug"] not in _picker_slugs()
    listed = _client().get("/api/orders/typed-items", headers=HDR).get_json()["items"]
    assert not any(i["slug"] == it["slug"] for i in listed)


def test_a_retired_item_still_prices_an_order_that_has_it():
    it = _new("Typed Retire Price", cents=4000)
    before = _recs(_priced({"slug": it["slug"], "qty": 2}))[it["slug"]]
    r = _retire(it["slug"])
    assert r.status_code == 200 and r.get_json()["item"]["retired"] is True
    assert it["slug"] not in _picker_slugs()
    after = _recs(_priced({"slug": it["slug"], "qty": 2}))[it["slug"]]
    assert before["unit_cents"] == after["unit_cents"] == 4000
    assert after["qty"] == 2 and after["line_cents"] == 8000


def test_a_retired_name_cannot_be_typed_back_in():
    it = _new("Typed Retire Back")
    _retire(it["slug"])
    r = _add(it["name"])
    assert r.status_code == 400 and "retired" in r.get_json()["error"]


def test_retiring_twice_is_safe_and_an_unknown_slug_is_404():
    it = _new("Typed Retire Twice")
    assert _retire(it["slug"]).status_code == 200
    assert _retire(it["slug"]).status_code == 200
    assert _retire("no-such-typed-item-%s" % uuid.uuid4().hex[:8]).status_code == 404


def test_only_the_owner_can_retire(monkeypatch):
    it = _new("Typed Retire Owner")
    assert _retire(it["slug"], headers={}).status_code == 401
    from dashboard import rbac
    monkeypatch.setattr(app_mod, "_bos_actor", lambda: rbac.Actor(role="va"))
    assert _retire(it["slug"]).status_code == 401
    monkeypatch.undo()
    assert it["slug"] in _picker_slugs()


def test_the_retired_column_is_added_to_an_old_table():
    import sqlite3
    from dashboard import typed_items as ti
    cx = sqlite3.connect(":memory:")
    cx.execute("CREATE TABLE typed_order_items (slug TEXT PRIMARY KEY, name TEXT NOT NULL, "
               "price_cents INTEGER NOT NULL, bottle_type TEXT, created_by TEXT, "
               "created_at TEXT NOT NULL)")
    cx.execute("INSERT INTO typed_order_items VALUES ('old-one','Old One',500,'30 g',NULL,'x')")
    cx.commit()
    ti.init_table(cx)
    ti.init_table(cx)   # a second start is a no-op
    assert [i["slug"] for i in ti.list_all(cx)] == ["old-one"]
    assert ti.retire(cx, "old-one")["retired"] is True
    assert ti.list_all(cx) == [] and ti.get(cx, "old-one")["price_cents"] == 500


def test_the_new_order_page_offers_retire_only_for_a_new_item():
    html = open("static/order-new.html").read()
    assert 'id="retire-item-btn"' in html
    assert '(p&&p.typed)?"":"none"' in html
    assert "/retire\"" in html or "/retire\"," in html


def test_retiring_one_item_leaves_the_others_on_the_list():
    a, b = _new("Typed Retire A"), _new("Typed Retire B")
    assert _retire(a["slug"]).status_code == 200
    slugs = _picker_slugs()
    assert a["slug"] not in slugs and b["slug"] in slugs


def _old_style_cx():
    import sqlite3
    from dashboard import typed_items as ti
    cx = sqlite3.connect(":memory:")
    ti.init_table(cx)
    ti.add(cx, "Stamp One", 500, bottle_type="30 g")
    return cx, ti


def test_a_second_retire_keeps_the_first_retirement_time():
    cx, ti = _old_style_cx()
    ti.retire(cx, "stamp-one")
    first = cx.execute("SELECT retired_at FROM typed_order_items").fetchone()[0]
    cx.execute("UPDATE typed_order_items SET retired_at='2000-01-01'")
    cx.commit()
    ti.retire(cx, "stamp-one")
    assert first and cx.execute("SELECT retired_at FROM typed_order_items").fetchone()[0] \
        == "2000-01-01"


def test_a_retired_name_is_refused_when_two_adds_race(monkeypatch):
    cx, ti = _old_style_cx()
    ti.retire(cx, "stamp-one")
    real_get, calls = ti.get, []

    def first_miss(c, slug):   # the request read before the row existed
        calls.append(slug)
        return None if len(calls) == 1 else real_get(c, slug)
    monkeypatch.setattr(ti, "get", first_miss)
    with pytest.raises(ValueError, match="retired"):
        ti.add(cx, "Stamp One", 500, bottle_type="30 g")


def test_a_failed_column_add_is_not_swallowed():
    import sqlite3
    from dashboard import typed_items as ti
    real = sqlite3.connect(":memory:")

    class NoAlter:
        def execute(self, sql, *a):
            if sql.lstrip().upper().startswith("ALTER"):
                raise sqlite3.OperationalError("locked")
            return real.execute(sql, *a)

        def commit(self):
            real.commit()

        def rollback(self):
            real.rollback()
    real.execute("CREATE TABLE typed_order_items (slug TEXT PRIMARY KEY, name TEXT NOT NULL, "
                 "price_cents INTEGER NOT NULL, bottle_type TEXT, created_by TEXT, "
                 "created_at TEXT NOT NULL)")
    with pytest.raises(sqlite3.OperationalError):
        ti.init_table(NoAlter())


def test_the_page_retires_the_picked_typed_item_only():
    html = open("static/order-new.html").read()
    fn = html[html.index("async function retireTypedItem"):]
    fn = fn[:fn.index("\n}\n")]
    assert 'CATALOG.find(x=>x.slug===$("prod-pick").value)' in fn
    assert "if(!p||!p.typed) return;" in fn
    assert 'encodeURIComponent(p.slug)+"/retire"' in fn
