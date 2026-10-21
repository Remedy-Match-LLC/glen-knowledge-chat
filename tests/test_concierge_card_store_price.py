"""The concierge card's price comes from the store, never from a vector (2026-10-21).

Glen's ruling: data/products.json price_cents is the only price source. The
specific-formulations vectors carry copied prices, and 154 of 155 were stale
($69.97 against a store $70.00). `_resolve_complement` used to put that copied
price on the "Add to my order" button.

  a. A resolved slug shows the catalog's price_cents, formatted as the store does.
  b. An unresolved name shows no price at all.
  c. A resolved product with no price_cents of its own shows no price.
  d. /api/qbo/price-coverage reports catalog prices and never queries the index.
"""
import pytest

import app

SLUG = "concierge-price-probe"
TITLE = "Concierge Price Probe Title"
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
    monkeypatch.setitem(products, SLUG, {"name": "Concierge Price Probe",
                                         "price_cents": 7000})
    monkeypatch.setitem(app._TITLE_TO_SLUG, TITLE, SLUG)
    monkeypatch.setattr(app, "embed", lambda _n: [0.0])
    app._COMPLEMENT_CACHE.clear()
    yield products
    app._COMPLEMENT_CACHE.clear()


def _resolve(monkeypatch, idx, name="Concierge Price Probe"):
    monkeypatch.setattr(app, "_idx", idx)
    app._COMPLEMENT_CACHE.clear()
    return app._resolve_complement(name)


def test_the_card_price_is_the_store_price_cents(monkeypatch, probe):
    c = _resolve(monkeypatch, _Idx(TITLE))
    assert c["slug"] == SLUG and c["in_catalog"]
    assert c["price"] == "$70.00"


def test_the_card_follows_a_store_price_change(monkeypatch, probe):
    monkeypatch.setitem(probe, SLUG, {"name": "Concierge Price Probe", "price_cents": 4000})
    c = _resolve(monkeypatch, _Idx(TITLE))
    assert c["price"] == "$40.00"


def test_a_stale_metadata_price_never_reaches_the_card(monkeypatch, probe):
    c = _resolve(monkeypatch, _Idx(TITLE))
    assert STALE not in str(c)


def test_an_unresolved_name_shows_no_price(monkeypatch, probe):
    c = _resolve(monkeypatch, _Idx("A Title Not In The Catalog"))
    assert c["slug"] is None and not c["in_catalog"]
    assert c["price"] is None


def test_a_low_score_match_shows_no_price(monkeypatch, probe):
    c = _resolve(monkeypatch, _Idx(TITLE, score=0.5))
    assert c["slug"] is None and c["price"] is None


def test_a_product_without_price_cents_shows_no_price(monkeypatch, probe):
    monkeypatch.setitem(probe, SLUG, {"name": "Concierge Price Probe"})
    c = _resolve(monkeypatch, _Idx(TITLE))
    assert c["slug"] == SLUG and c["in_catalog"]
    assert c["price"] is None


def test_price_coverage_reads_the_catalog_not_the_index(monkeypatch, probe):
    idx = _Idx(TITLE)
    monkeypatch.setattr(app, "_idx", idx)
    monkeypatch.setattr(app, "_qbo_auth_ok", lambda: True)
    d = app.app.test_client().get("/api/qbo/price-coverage").get_json()
    rows = {r["slug"]: r for r in d["products"]}
    assert rows[SLUG]["found"] and rows[SLUG]["price"] == "$70.00"
    assert STALE not in str(d)
    assert idx.calls == 0
    assert d["total"] == len(probe)
