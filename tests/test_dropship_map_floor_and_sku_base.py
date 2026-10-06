"""Glen, 2026-10-06: practitioner prices for products that retail below the $67 MAP.

- A product retailing below MAP ($70 since #1921) is floored at its own retail, so a patient never pays
  more through a practitioner than in the store.
- A product with its own wholesale discount (the large-format books at $20) is based
  at that wholesale price in drop-ship and dispensary orders, not the $50 blended base.
- Glen, later the same day: the drop-ship rule ($50 base and fee) applies only to
  Functional Formulations, and "no discount unless otherwise specified". Anything else
  drop-ships at its own wholesale price if it has one, else retail, with no fee.
"""
from dashboard import dropship_checkout as dc
from dashboard import practitioner_pricing as pp


# ── map_floor_cents ───────────────────────────────────────────────────────────

def test_floor_is_retail_when_retail_is_below_map():
    assert pp.map_floor_cents(3000, 6700) == 3000


def test_floor_is_map_when_retail_is_above_map():
    assert pp.map_floor_cents(7000, 6700) == 6700


def test_floor_is_map_when_retail_is_missing():
    assert pp.map_floor_cents(None, 6700) == 6700
    assert pp.map_floor_cents(0, 6700) == 6700


# ── sku_base_cents ────────────────────────────────────────────────────────────

def test_sku_base_uses_the_products_own_wholesale_discount():
    assert pp.sku_base_cents(1, 0, 4000, 50) == 2000


def test_sku_base_without_a_discount_is_the_blended_curve_for_an_ff_only():
    assert pp.sku_base_cents(1, 0, 4000, None) == pp.drop_ship_base_cents(1, 0) == 5000
    assert pp.sku_base_cents(1, 0, 4000, None, is_ff=False) == 4000


# ── drop-ship quote (practitioner pays) ──────────────────────────────────────

def _stub_cart(monkeypatch, retail, pct, ff=("ff",)):
    monkeypatch.setattr(dc, "_retail_for", lambda slug: retail[slug])
    monkeypatch.setattr(dc, "_wholesale_pct_for", lambda slug: pct.get(slug))
    monkeypatch.setattr(dc, "_is_ff", lambda slug: slug in ff)
    monkeypatch.setattr(dc, "_practitioner_dropship_unit_cents", lambda pid: None)


def test_dropship_book_is_based_at_its_20_dollar_wholesale(monkeypatch):
    _stub_cart(monkeypatch, {"book": 4000}, {"book": 50})
    q = dc.quote_dropship_cart([{"slug": "book", "qty": 1}], {"id": "p1", "modules_completed": 0})
    line = q["lines"][0]
    # base $20 and no fee (not a Functional Formulation): the practitioner pays $20, not $50
    assert line["base_cents"] == 2000
    assert line["fee_cents"] == 0
    assert line["unit_cents"] == 2000


def test_dropship_non_ff_without_a_discount_is_retail_and_no_fee(monkeypatch):
    # A $40 infoceutical: $40, not the $50 base.
    _stub_cart(monkeypatch, {"info": 4000}, {})
    q = dc.quote_dropship_cart([{"slug": "info", "qty": 1}], {"id": "p1", "modules_completed": 0})
    line = q["lines"][0]
    assert (line["base_cents"], line["fee_cents"], line["unit_cents"]) == (4000, 0, 4000)


def test_dropship_formula_without_a_discount_is_unchanged(monkeypatch):
    _stub_cart(monkeypatch, {"ff": 7000}, {})
    q = dc.quote_dropship_cart([{"slug": "ff", "qty": 1}], {"id": "p1", "modules_completed": 0})
    assert q["lines"][0]["unit_cents"] == 5700


# ── practitioner selling price (patient pays) ────────────────────────────────

def _boom(*a, **k):
    raise RuntimeError("no settings db")


def test_selling_price_fallback_uses_retail_below_map(monkeypatch):
    # The settings-error fallback is max(retail, floor): $30 retail stays $30.
    monkeypatch.setattr(dc.db, "connect", _boom)
    assert dc._practitioner_price_cents("p1", "info", 3000) == 3000


def test_selling_price_fallback_keeps_map_above_it(monkeypatch):
    monkeypatch.setattr(dc.db, "connect", _boom)
    assert dc._practitioner_price_cents("p1", "ff", 6000) == 6000
    assert dc._practitioner_price_cents("p1", "ff", 7000) == 7000


def test_selling_price_with_settings_floors_at_retail(monkeypatch, tmp_path):
    import sqlite3
    from dashboard import practitioner_settings as ps
    db_file = tmp_path / "log.db"
    monkeypatch.setattr(dc, "_LOG_DB", str(db_file))
    monkeypatch.setattr(dc.db, "connect", lambda path: sqlite3.connect(path))
    monkeypatch.setattr(dc, "_dropship_ff", lambda slug: slug == "ff")
    monkeypatch.setattr(dc, "_wholesale_pct_for", lambda slug: None)
    cx = sqlite3.connect(str(db_file))
    ps.init_settings_table(cx)
    # Stored overrides the error fallback could never produce (it returns retail).
    ps.set_pricing(cx, "p1", {"default_markup_pct": 0,
                              "overrides": {"info": 2500, "info2": 3500, "ff": 6000}})
    cx.close()
    assert dc._practitioner_price_cents("p1", "info", 3000) == 3000   # below retail: floored at retail
    assert dc._practitioner_price_cents("p1", "info2", 3000) == 3500  # markup above retail kept
    assert dc._practitioner_price_cents("p1", "ff", 7500) == 7000     # above MAP: floored at MAP


# ── dispensary order (patient pays the practitioner's price) ─────────────────

def test_dispensary_book_margin_uses_its_wholesale_base(monkeypatch):
    monkeypatch.setattr(dc, "_retail_for", lambda slug: 4000)
    monkeypatch.setattr(dc, "_wholesale_pct_for", lambda slug: 50)
    monkeypatch.setattr(dc, "_is_ff", lambda slug: False)
    monkeypatch.setattr(dc, "practitioner_price_for", lambda pid, slug: 4000)
    import dashboard.tax as _tax
    monkeypatch.setattr(_tax, "compute_get_cents",
                        lambda s, *, channel, ship_to_state, resale_ok=False: 0)
    out = dc.build_client_order(
        [{"slug": "book", "qty": 1}], {"id": "p1", "modules_completed": 0},
        patient={"email": "pat@x.com", "ship": {"name": "Pat", "state": "CA", "country": "US"}},
        method="card")
    # patient pays $40; base $20, no fee, so the practitioner earns $20 (was $0)
    assert out["subtotal_cents"] == 4000
    assert out["margin_cents"] == 2000


# ── console override clamp ───────────────────────────────────────────────────

def test_console_override_floors_at_retail_below_map(monkeypatch, tmp_path):
    import app as appmod
    monkeypatch.setattr(appmod, "LOG_DB", tmp_path / "chat_log.db")
    monkeypatch.setattr(appmod, "_practitioner_session_pid", lambda: "p1")
    cat = {"info": {"price_cents": 3000}, "ff": {"price_cents": 7500, "qty_pricing": True}}
    monkeypatch.setattr(appmod, "_get_product", lambda slug: cat.get(slug))
    r = appmod.app.test_client().post("/api/practitioner/settings", json={"pricing": {
        "default_markup_pct": 0,
        "overrides": {"info": 2500, "ff": 6000, "unknown": 6000}}})
    data = r.get_json()
    assert r.status_code == 200 and data["ok"] is True
    got = {c["slug"]: c["clamped_to_cents"] for c in data["clamped"]}
    assert got == {"info": 3000, "ff": 7000, "unknown": 7000}


# ── round 1 review fixes ─────────────────────────────────────────────────────

def test_flat_dropship_price_never_raises_a_line(monkeypatch):
    _stub_cart(monkeypatch, {"book": 4000, "ff": 6900}, {"book": 50})
    monkeypatch.setattr(dc, "_practitioner_dropship_unit_cents", lambda pid: 4000)
    monkeypatch.setattr(dc, "_flat_ceiling_cents", lambda: 7000)
    q = dc.quote_dropship_cart([{"slug": "book", "qty": 1}, {"slug": "ff", "qty": 1}],
                               {"id": "p1", "modules_completed": 0})
    units = {l["slug"]: l["unit_cents"] for l in q["lines"]}
    assert units == {"book": 2000, "ff": 4000}


def test_discounted_products_stay_out_of_the_volume_count(monkeypatch):
    # 1 formula + 11 books: the formula is priced as 1 bottle ($50 base), as in a stocking order.
    _stub_cart(monkeypatch, {"book": 4000, "ff": 7000}, {"book": 50})
    q = dc.quote_dropship_cart([{"slug": "ff", "qty": 1}, {"slug": "book", "qty": 11}],
                               {"id": "p1", "modules_completed": 0})
    ff = next(l for l in q["lines"] if l["slug"] == "ff")
    assert ff["base_cents"] == 5000


def test_a_discount_never_takes_a_line_below_its_own_margin(monkeypatch):
    import app as appmod
    import dashboard.tax as _tax
    prices = {"ff": 7000, "cheap": 3500}
    monkeypatch.setattr(dc, "_retail_for", lambda slug: prices[slug])
    monkeypatch.setattr(dc, "_wholesale_pct_for", lambda slug: None)
    monkeypatch.setattr(dc, "_is_ff", lambda slug: slug == "ff")
    monkeypatch.setattr(dc, "practitioner_price_for", lambda pid, slug: prices[slug])
    monkeypatch.setattr(_tax, "compute_get_cents",
                        lambda s, *, channel, ship_to_state, resale_ok=False: 0)
    monkeypatch.setattr(appmod, "_qty_eligible", lambda p: True)
    monkeypatch.setattr(appmod, "_get_product", lambda slug: {})
    monkeypatch.setattr(dc._pricing, "open_total_pct", lambda q, e: 0.0)
    monkeypatch.setattr(dc._pricing, "program_total_pct", lambda q, e, m: 0.0)
    monkeypatch.setattr(dc._pricing, "same_sku_pct", lambda q, e: 0.10)
    monkeypatch.setattr(dc._pricing, "unit_floor_cents", lambda *a: 0)
    monkeypatch.setattr(dc._pricing, "apply_discount", lambda s, pct, f: int(s * (1 - pct)))
    out = dc.build_client_order(
        [{"slug": "ff", "qty": 1}, {"slug": "cheap", "qty": 1}],
        {"id": "p1", "modules_completed": 0},
        patient={"email": "pat@x.com", "ship": {"name": "Pat", "state": "CA", "country": "US"}},
        method="card", effective_settings={"on": True})
    ff = pp.quote_line(selling_cents=7000, qty=1, modules=0, settings=dc._settings())  # only FFs count
    # The $35 line has no margin (base above price), so it gets no discount and
    # takes nothing from the formula line's credit.
    assert out["subtotal_cents"] == 6300 + 3500
    assert out["margin_cents"] == ff["margin_cents"] - 700


def test_wholomega_120_keeps_formulation_dropship_pricing():
    """Glen, 2026-10-06: "formulation pricing" for the larger WholOmega bottle, $97 for one."""
    import app as appmod
    assert appmod._get_product("wholomega-120-gelcaps").get("ff_larger_size") is True
    assert dc._dropship_ff("wholomega-120-gelcaps") is True
    assert dc._dropship_ff("coq10") is False
    line = dc.dropship_line_cents(retail_cents=19000, qty=1, modules=0, settings=dc._settings(),
                                  is_ff=dc._dropship_ff("wholomega-120-gelcaps"))
    assert line["unit_cents"] == 9700


def test_non_ff_products_stay_out_of_the_volume_count(monkeypatch):
    # 1 formula + 11 infoceuticals (no wholesale price): the formula is still 1 bottle on the curve.
    _stub_cart(monkeypatch, {"info": 4000, "ff": 7000}, {})
    q = dc.quote_dropship_cart([{"slug": "ff", "qty": 1}, {"slug": "info", "qty": 11}],
                               {"id": "p1", "modules_completed": 0})
    assert next(l for l in q["lines"] if l["slug"] == "ff")["base_cents"] == 5000



# ── round 2 (final): a product with no discount never sells below retail ─────

def test_selling_floor_is_retail_for_a_product_with_no_discount():
    assert pp.selling_floor_cents(10000, 7000, discountable=False) == 10000
    assert pp.selling_floor_cents(10000, 7000, discountable=True) == 7000
    assert pp.selling_floor_cents(4000, 7000, discountable=False) == 4000
    assert pp.selling_floor_cents(None, 7000, discountable=False) == 7000


def test_dispensary_price_floor_for_a_plain_100_dollar_product(monkeypatch):
    monkeypatch.setattr(dc.db, "connect", _boom)
    monkeypatch.setattr(dc, "_is_ff", lambda slug: False)
    monkeypatch.setattr(dc, "_wholesale_pct_for", lambda slug: None)
    monkeypatch.setattr(dc, "_dropship_ff", lambda slug: False)
    assert dc._practitioner_price_cents("p1", "device", 10000) == 10000


def test_console_override_cannot_take_a_plain_product_below_retail(monkeypatch, tmp_path):
    import app as appmod
    monkeypatch.setattr(appmod, "LOG_DB", tmp_path / "chat_log.db")
    monkeypatch.setattr(appmod, "_practitioner_session_pid", lambda: "p1")
    cat = {"device": {"price_cents": 10000}, "ff": {"price_cents": 7500, "qty_pricing": True}}
    monkeypatch.setattr(appmod, "_get_product", lambda slug: cat.get(slug))
    r = appmod.app.test_client().post("/api/practitioner/settings", json={"pricing": {
        "default_markup_pct": 0, "overrides": {"device": 7000, "ff": 7000}}})
    got = {c["slug"]: c["clamped_to_cents"] for c in r.get_json()["clamped"]}
    assert got == {"device": 10000}   # the FF at $70 is allowed; the device is held at retail



def test_dispensary_volume_count_is_formulations_only(monkeypatch):
    """Round 3: 1 formula + 11 books in a dispensary cart prices the formula as 1 bottle."""
    import dashboard.tax as _tax
    prices = {"ff": 7000, "book": 4000}
    monkeypatch.setattr(dc, "_retail_for", lambda slug: prices[slug])
    monkeypatch.setattr(dc, "_wholesale_pct_for", lambda slug: 50 if slug == "book" else None)
    monkeypatch.setattr(dc, "_is_ff", lambda slug: slug == "ff")
    monkeypatch.setattr(dc, "practitioner_price_for", lambda pid, slug: prices[slug])
    monkeypatch.setattr(_tax, "compute_get_cents",
                        lambda s, *, channel, ship_to_state, resale_ok=False: 0)
    out = dc.build_client_order(
        [{"slug": "ff", "qty": 1}, {"slug": "book", "qty": 11}], {"id": "p1", "modules_completed": 0},
        patient={"email": "pat@x.com", "ship": {"name": "Pat", "state": "CA", "country": "US"}},
        method="card")
    ff = pp.quote_line(selling_cents=7000, qty=1, modules=0, settings=dc._settings())
    assert out["margin_cents"] == ff["margin_cents"] + 11 * 2000


def test_own_wholesale_base_is_whole_dollars():
    assert pp.sku_base_cents(1, 0, 3500, 50, is_ff=False) == 1800   # $17.50 rounds up
