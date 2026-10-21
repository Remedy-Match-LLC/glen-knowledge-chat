"""The concierge card's price comes from the store, never from a vector (2026-10-21).

Glen's ruling: data/products.json price_cents is the only price source. The
specific-formulations vectors carry copied prices, and 154 of 155 were stale
($69.97 against a store $70.00). `_resolve_complement` used to put that copied
price on the "Add to my order" button.

  a. A resolved slug shows the catalog's price_cents, formatted as the store does.
     It is the base store price, as the product page shows it and the add charges.
  b. The price is read on every call, so a cached card follows a price change.
  c. An unresolved name, or a product with no price_cents, shows no price.
     A zero price is a price and shows as $0.00.
  d. A product that cannot be ordered (info_only) gets no add card.
  e. /begin/concierge/add refuses a product with no price_cents of its own.
  f. Scraped "Price: $X." anchors are dropped from prompt text: the concierge and
     match context (build_context) and generated product copy (_page_text).
  g. /api/qbo/price-coverage reports active catalog prices and never queries the index.
"""
import json
import sqlite3

import pytest

import app
from dashboard import orders as O
from dashboard import product_content as PC

SLUG = "concierge-price-probe"
TITLE = "Concierge Price Probe Title"
NAME = "Concierge Price Probe"
STALE = "$69.97"


class _Idx:
    def __init__(self, title, score=0.99):
        self.title, self.score, self.calls = title, score, 0

    def query(self, **k):
        self.calls += 1
        m = type("M", (), {"score": self.score,
                           "metadata": {"title": self.title, "price": STALE,
                                        "price_your": STALE, "price_retail": STALE,
                                        "url": "https://x.test/p"}})()
        return type("R", (), {"matches": [m]})()


@pytest.fixture
def probe(monkeypatch):
    products = app._PRODUCTS.setdefault("products", {})
    monkeypatch.setitem(products, SLUG, {"name": NAME, "price_cents": 7000})
    monkeypatch.setitem(app._TITLE_TO_SLUG, TITLE, SLUG)
    monkeypatch.setattr(app, "embed", lambda _n: [0.0])
    app._COMPLEMENT_CACHE.clear()
    yield products
    app._COMPLEMENT_CACHE.clear()


def _set(monkeypatch, products, **rec):
    monkeypatch.setitem(products, SLUG, {"name": NAME, **rec})


def _resolve(monkeypatch, idx, name=NAME):
    monkeypatch.setattr(app, "_idx", idx)
    app._COMPLEMENT_CACHE.clear()
    return app._resolve_complement(name)


# ── a. The store price ────────────────────────────────────────────────────────

def test_the_card_price_is_the_store_price_cents(monkeypatch, probe):
    c = _resolve(monkeypatch, _Idx(TITLE))
    assert c["slug"] == SLUG and c["in_catalog"]
    assert c["price"] == "$70.00"


def test_a_large_price_has_no_thousands_separator(monkeypatch, probe):
    _set(monkeypatch, probe, price_cents=169700)
    assert _resolve(monkeypatch, _Idx(TITLE))["price"] == "$1697.00"


def test_the_card_shows_the_base_price_not_the_member_price(monkeypatch, probe):
    _set(monkeypatch, probe, price_cents=30000, member_price_cents=20000)
    assert _resolve(monkeypatch, _Idx(TITLE))["price"] == "$300.00"


def test_a_stale_metadata_price_never_reaches_the_card(monkeypatch, probe):
    c = _resolve(monkeypatch, _Idx(TITLE))
    assert STALE not in str(c)


# ── b. The cache ──────────────────────────────────────────────────────────────

def test_a_cached_card_follows_a_store_price_change(monkeypatch, probe):
    monkeypatch.setattr(app, "_idx", _Idx(TITLE))
    app._COMPLEMENT_CACHE.clear()
    assert app._resolve_complement(NAME)["price"] == "$70.00"
    _set(monkeypatch, probe, price_cents=4000)
    assert app._resolve_complement(NAME)["price"] == "$40.00"
    _set(monkeypatch, probe)
    assert app._resolve_complement(NAME)["price"] is None


# ── c. No price ───────────────────────────────────────────────────────────────

def test_an_unresolved_name_shows_no_price(monkeypatch, probe):
    c = _resolve(monkeypatch, _Idx("A Title Not In The Catalog"))
    assert c["slug"] is None and not c["in_catalog"]
    assert c["price"] is None


def test_a_low_score_match_shows_no_price(monkeypatch, probe):
    c = _resolve(monkeypatch, _Idx(TITLE, score=0.5))
    assert c["slug"] is None and c["price"] is None


def test_a_product_without_price_cents_shows_no_price(monkeypatch, probe):
    _set(monkeypatch, probe)
    c = _resolve(monkeypatch, _Idx(TITLE))
    assert c["slug"] == SLUG and c["in_catalog"]
    assert c["price"] is None


def test_a_zero_price_is_shown(monkeypatch, probe):
    _set(monkeypatch, probe, price_cents=0)
    assert _resolve(monkeypatch, _Idx(TITLE))["price"] == "$0.00"


# ── d. Not orderable ──────────────────────────────────────────────────────────

def test_an_info_only_product_gets_no_add_card(monkeypatch, probe):
    _set(monkeypatch, probe, price_cents=30000, info_only=True)
    c = _resolve(monkeypatch, _Idx(TITLE))
    assert c["slug"] is None and not c["in_catalog"]
    assert c["price"] is None and c["url"] is None


# ── e. The add route ──────────────────────────────────────────────────────────

def _order(monkeypatch, tmp_path):
    db = str(tmp_path / "log.db")
    monkeypatch.setattr(app, "LOG_DB", db)
    monkeypatch.setattr(app, "is_member", lambda sid, email: True)
    cx = sqlite3.connect(db)
    O.init_orders_table(cx)
    O.upsert_order(cx, source="funnel", external_ref="tok-cp", email="e@b.com",
                   total_cents=8000)
    O.set_order_qbo_lines(cx, "tok-cp", {"lines": [{"name": "Base", "amount": 80.0,
                                                    "qty": 1}],
                                         "discount_cents": 0, "tax_cents": 0})
    cx.commit()
    cx.close()
    return db


def _total(db):
    cx = sqlite3.connect(db)
    cx.row_factory = sqlite3.Row
    row = O.find_order_by_external_ref(cx, "tok-cp")
    cx.close()
    return row["total_cents"], len(json.loads(row["qbo_lines_json"])["lines"])


def test_the_add_refuses_a_product_with_no_store_price(monkeypatch, tmp_path, probe):
    _set(monkeypatch, probe)
    db = _order(monkeypatch, tmp_path)
    r = app.app.test_client().post("/begin/concierge/add",
                                   json={"slug": SLUG, "invoice_id": "tok-cp"})
    assert r.status_code == 400 and r.get_json()["ok"] is False
    assert _total(db) == (8000, 1)


def test_the_add_charges_the_price_the_card_showed(monkeypatch, tmp_path, probe):
    db = _order(monkeypatch, tmp_path)
    r = app.app.test_client().post("/begin/concierge/add",
                                   json={"slug": SLUG, "invoice_id": "tok-cp"})
    assert r.status_code == 200, r.get_data(as_text=True)
    assert _total(db) == (8000 + 7000, 2)


# ── f. Prompt text ────────────────────────────────────────────────────────────

def _match(ns, text, mid):
    return type("M", (), {"id": mid, "score": 0.9,
                          "metadata": {"_source_ns": ns, "text": text, "name": mid}})()


def test_the_concierge_context_drops_the_scraped_price():
    chunk = f"Probe Formula. Price: {STALE}. Supports calm focus. A $10 lab test pairs well."
    ctx, _ = app.build_context([_match("specific-formulations", chunk, "sf-1"),
                                _match("specific-formulations",
                                       "Other Formula. Price: 39.97. Body text.", "sf-2")])
    assert STALE not in ctx and "39.97" not in ctx
    assert "Price:" not in ctx
    assert "Probe Formula." in ctx and "Supports calm focus." in ctx
    assert "A $10 lab test" in ctx  # only the scraped anchor goes, not the body's figures


def test_other_namespaces_keep_their_figures():
    ctx, _ = app.build_context([_match("ingredients", "A cup costs $5.00 at market.", "i-1")])
    assert "$5.00" in ctx


class _PCIdx:
    def query(self, **k):
        ms = [type("M", (), {"metadata": {"text": f"Probe Formula. Price: {STALE}. Part one.",
                                          "chunk_index": 0, "price": STALE,
                                          "url": "https://x.test/p"}})(),
              type("M", (), {"metadata": {"text": "Price: $39.97. Part two.",
                                          "chunk_index": 1}})()]
        return type("R", (), {"matches": ms})()


def test_generated_product_copy_never_sees_the_scraped_price(monkeypatch):
    monkeypatch.setattr(PC, "_clients", lambda: (_PCIdx(), None, lambda _t: [0.0]))
    page = PC._page_text({"name": NAME})
    assert STALE not in str(page) and "39.97" not in str(page)
    assert "Part one." in page["text"] and "Part two." in page["text"]
    assert page["price"] == ""


# ── g. Coverage ───────────────────────────────────────────────────────────────

def test_price_coverage_reads_the_catalog_not_the_index(monkeypatch, probe):
    idx = _Idx(TITLE)
    monkeypatch.setattr(app, "_idx", idx)
    monkeypatch.setattr(app, "_qbo_auth_ok", lambda: True)
    monkeypatch.setitem(probe, "cp-no-price", {"name": "No Price"})
    monkeypatch.setitem(probe, "cp-zero", {"name": "Zero", "price_cents": 0})
    monkeypatch.setitem(probe, "cp-gone", {"name": "Gone", "price_cents": 5000,
                                           "inactive": True})
    d = app.app.test_client().get("/api/qbo/price-coverage").get_json()
    rows = {r["slug"]: r for r in d["products"]}
    assert rows[SLUG]["found"] and rows[SLUG]["price"] == "$70.00"
    assert rows["cp-no-price"]["found"] is False and "price" not in rows["cp-no-price"]
    assert rows["cp-zero"]["found"] is True and rows["cp-zero"]["price"] == "$0.00"
    assert "cp-gone" not in rows
    active = [s for s, p in probe.items() if not p.get("inactive")]
    assert d["total"] == len(active)
    assert d["covered"] == sum(1 for s in active if probe[s].get("price_cents") is not None)
    assert STALE not in str(d)
    assert idx.calls == 0
