"""Glen, 2026-10-06: practitioner prices for products that retail below the $67 MAP.

- A product retailing below MAP is floored at its own retail, so a patient never pays
  more through a practitioner than in the store.
- A product with its own wholesale discount (the large-format books at $20) is based
  at that wholesale price in drop-ship and dispensary orders, not the $50 blended base.
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


def test_sku_base_without_a_discount_is_the_blended_curve():
    assert pp.sku_base_cents(1, 0, 4000, None) == pp.drop_ship_base_cents(1, 0) == 5000


# ── drop-ship quote (practitioner pays) ──────────────────────────────────────

def _stub_cart(monkeypatch, retail, pct):
    monkeypatch.setattr(dc, "_retail_for", lambda slug: retail[slug])
    monkeypatch.setattr(dc, "_wholesale_pct_for", lambda slug: pct.get(slug))
    monkeypatch.setattr(dc, "_practitioner_dropship_unit_cents", lambda pid: None)


def test_dropship_book_is_based_at_its_20_dollar_wholesale(monkeypatch):
    _stub_cart(monkeypatch, {"book": 4000}, {"book": 50})
    q = dc.quote_dropship_cart([{"slug": "book", "qty": 1}], {"id": "p1", "modules_completed": 0})
    line = q["lines"][0]
    # base $20; fee 33% of ($40 - $20) = $6.60; the practitioner pays $26.60, not $50
    assert line["base_cents"] == 2000
    assert line["fee_cents"] == 660
    assert line["unit_cents"] == 2660


def test_dropship_formula_without_a_discount_is_unchanged(monkeypatch):
    _stub_cart(monkeypatch, {"ff": 7000}, {})
    q = dc.quote_dropship_cart([{"slug": "ff", "qty": 1}], {"id": "p1", "modules_completed": 0})
    assert q["lines"][0]["unit_cents"] == 5660


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
    db_file = tmp_path / "log.db"
    monkeypatch.setattr(dc, "_LOG_DB", str(db_file))
    monkeypatch.setattr(dc.db, "connect", lambda path: sqlite3.connect(path))
    # No stored settings: default markup 0, so the price is retail, floored at retail.
    assert dc._practitioner_price_cents("p1", "info", 3000) == 3000
    # Above MAP is untouched.
    assert dc._practitioner_price_cents("p1", "ff", 7000) == 7000


# ── dispensary order (patient pays the practitioner's price) ─────────────────

def test_dispensary_book_margin_uses_its_wholesale_base(monkeypatch):
    monkeypatch.setattr(dc, "_retail_for", lambda slug: 4000)
    monkeypatch.setattr(dc, "_wholesale_pct_for", lambda slug: 50)
    monkeypatch.setattr(dc, "practitioner_price_for", lambda pid, slug: 4000)
    import dashboard.tax as _tax
    monkeypatch.setattr(_tax, "compute_get_cents",
                        lambda s, *, channel, ship_to_state, resale_ok=False: 0)
    out = dc.build_client_order(
        [{"slug": "book", "qty": 1}], {"id": "p1", "modules_completed": 0},
        patient={"email": "pat@x.com", "ship": {"name": "Pat", "state": "CA", "country": "US"}},
        method="card")
    # patient pays $40; base $20, fee $6.60, so the practitioner earns $13.40 (was $0)
    assert out["subtotal_cents"] == 4000
    assert out["margin_cents"] == 1340


# ── console override clamp ───────────────────────────────────────────────────

def test_console_override_floors_at_retail_below_map(monkeypatch, tmp_path):
    import app as appmod
    monkeypatch.setattr(appmod, "LOG_DB", tmp_path / "chat_log.db")
    monkeypatch.setattr(appmod, "_practitioner_session_pid", lambda: "p1")
    prices = {"info": 3000, "ff": 7000}
    monkeypatch.setattr(appmod, "_get_product",
                        lambda slug: {"price_cents": prices[slug]} if slug in prices else None)
    r = appmod.app.test_client().post("/api/practitioner/settings", json={"pricing": {
        "default_markup_pct": 0,
        "overrides": {"info": 2500, "ff": 6000, "unknown": 6000}}})
    data = r.get_json()
    assert r.status_code == 200 and data["ok"] is True
    got = {c["slug"]: c["clamped_to_cents"] for c in data["clamped"]}
    assert got == {"info": 3000, "ff": 6700, "unknown": 6700}
