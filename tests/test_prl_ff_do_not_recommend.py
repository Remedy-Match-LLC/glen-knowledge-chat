"""The PRL and Fullscript cards suggest a Functional Formulation beside each channel
product. The suggestion comes from a vault crosswalk, which named Electrolyte Mineral
Manna and AllerFree (never recommended) and retired products. The card view must drop
any suggestion that is blocked or not sellable (2026-10-07)."""
import json
import os

os.environ.setdefault("OPENAI_API_KEY", "dummy")
os.environ.setdefault("PINECONE_API_KEY", "dummy")

import pytest

import app
from dashboard.related_products import DO_NOT_RECOMMEND

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _names_of_blocked_products():
    prods = json.load(open(os.path.join(ROOT, "data", "products.json")))["products"]
    return sorted({prods[s]["name"] for s in DO_NOT_RECOMMEND if s in prods})


@pytest.mark.parametrize("name", ["Electrolyte Mineral Manna", "AllerFree", "Fungifuge"])
def test_named_blocked_products_are_not_suggested(name):
    assert app._prl_ff_view(name, "equivalent") is None
    assert app._fullscript_ff_view(name, "equivalent") is None


@pytest.mark.parametrize("name", _names_of_blocked_products())
def test_every_blocked_catalog_name_is_not_suggested(name):
    assert app._prl_ff_view(name, "consider") is None


def test_a_name_that_is_no_product_is_not_suggested():
    assert app._prl_ff_view("NeurOmega", "consider") is None


def _first(pred):
    prods = json.load(open(os.path.join(ROOT, "data", "products.json")))["products"]
    return next(s for s, p in prods.items() if pred(s, p))


@pytest.mark.parametrize("make", [
    lambda: _first(lambda s, p: p.get("inactive") and app._get_product(s) is None),
    lambda: "no-such-product-slug",
    lambda: "electrolyte-mineral-manna",
    lambda: "allerfree-homeoenergetic-drops",
])
def test_whatever_the_resolver_returns_a_dead_or_blocked_slug_never_shows(monkeypatch, make):
    """The view must not trust the resolver: an inactive, unknown or blocked slug is dropped."""
    slug = make()
    monkeypatch.setattr(app, "_resolve_remedy_slug", lambda r: slug)
    assert app._prl_ff_view("Anything", "consider") is None
    assert app._fullscript_ff_view("Anything", "consider") is None


def test_a_lookup_error_drops_the_chip_and_does_not_raise(monkeypatch):
    monkeypatch.setattr(app, "_resolve_remedy_slug", lambda r: "clear-the-way")
    def boom(slug):
        raise RuntimeError("catalog down")
    monkeypatch.setattr(app, "_get_product", boom)
    assert app._prl_ff_view("Clear the Way", "consider") is None


def test_a_sellable_product_is_still_suggested():
    v = app._prl_ff_view("Clear the Way", "consider")
    assert v == {"name": "Clear the Way", "relation": "consider", "slug": "clear-the-way"}
    assert app._fullscript_ff_view("Clear the Way", None)["relation"] == "consider"


def test_no_suggestion_in_the_committed_prl_seed_is_blocked():
    seed = json.load(open(os.path.join(ROOT, "data", "prl_seed.json")))
    for p in seed["products"]:
        v = app._prl_ff_view(p.get("best_ff"), p.get("relation"))
        if v:
            assert v["slug"] not in DO_NOT_RECOMMEND, p["name"]


def test_the_chip_shows_the_name_of_the_product_it_links_to():
    v = app._prl_ff_view("Relax", "consider")
    assert v["slug"] == "stress-release"
    assert v["name"] == "Stress Release"


def _card_db(tmp_path, best_ff, mirror):
    import sqlite3
    from dashboard import prl_supplement as prl
    db = str(tmp_path / "c.db")
    cx = sqlite3.connect(db)
    cx.row_factory = sqlite3.Row
    prl.init_tables(cx)
    prl.sync_from_seed(cx, {
        "products": [{"name": "pH Minerals", "url": "u", "best_ff": best_ff,
                      "relation": "equivalent", "focus_tags": [], "ff_alts": [],
                      "external_id": "1", "product_type": "supplement"}],
        "focus_area_products": [{"focus_area_id": 9, "focus_area_name": "Minerals",
                                 "prl_product_name": "pH Minerals", "rank": 0}],
        "focus_area_items": [{"focus_area_id": 9, "item_code": "ED4"}],
    })
    cx.execute("CREATE TABLE IF NOT EXISTS scan_recommendations (email TEXT, scan_id TEXT,"
               " scan_date TEXT, item_code TEXT, priority_rank INTEGER, label TEXT)")
    cx.execute("INSERT INTO scan_recommendations VALUES ('a@b.com','s1','2026-07-01','ED4',1,'ED4')")
    if mirror:
        cx.execute("INSERT INTO prl_scan_mirror VALUES ('s1', ?, '2026-07-13')", (json.dumps(
            {"patterns": [{"Name": "Minerals", "PatternItems": [],
                           "PRLProducts": [{"Name": "pH Minerals"}]}]}),))
    cx.commit()
    cx.close()
    return db


@pytest.mark.parametrize("mirror", [False, True], ids=["derived", "mirror"])
def test_the_card_keeps_the_prl_product_and_drops_a_blocked_chip(monkeypatch, tmp_path, mirror):
    """Drives the real card builder on both paths, so a path that bypassed the view fails."""
    for best_ff, want in [("Electrolyte Mineral Manna", None), ("Clear the Way", "clear-the-way")]:
        here = tmp_path / best_ff.replace(" ", "")
        here.mkdir()
        monkeypatch.setattr(app, "LOG_DB", _card_db(here, best_ff, mirror))
        monkeypatch.setenv("PRL_SUPPLEMENT_ENABLED", "1")
        out = app._prl_supplement_for("a@b.com", "2026-07-01")
        assert out["source"] == ("mirror" if mirror else "derived")
        row = out["focus_areas"][0]["products"][0]
        assert row["name"] == "pH Minerals"
        assert (row["ff"] or {}).get("slug") == want


def _fs_db(tmp_path, best_ff):
    import sqlite3
    from dashboard import fullscript as fs
    db = str(tmp_path / "f.db")
    cx = sqlite3.connect(db)
    cx.row_factory = sqlite3.Row
    fs.init_tables(cx)
    fs.sync_from_seed(cx, {
        "products": [{"name": "Mag Taurate", "brand": "Jarrow", "product_slug": "mag-taurate",
                      "external_id": "P1", "best_ff": best_ff, "relation": "substitute",
                      "focus_tags": [], "ff_alts": [], "product_type": "supplement",
                      "url": None, "source": "seed", "active": 1}],
        "focus_area_products": [{"focus_area_id": 9, "focus_area_name": "Minerals",
                                 "fs_product_name": "Mag Taurate", "rank": 0}],
        "focus_area_items": [{"focus_area_id": 9, "item_code": "ED4"}],
    })
    cx.execute("CREATE TABLE IF NOT EXISTS scan_recommendations (email TEXT, scan_id TEXT,"
               " scan_date TEXT, item_code TEXT, priority_rank INTEGER, label TEXT)")
    cx.execute("INSERT INTO scan_recommendations VALUES ('a@b.com','s1','2026-07-01','ED4',1,'ED4')")
    cx.commit()
    cx.close()
    return db


@pytest.mark.parametrize("best_ff,want", [("Electrolyte Mineral Manna", None),
                                           ("Clear the Way", "clear-the-way")])
def test_the_fullscript_card_drops_a_blocked_chip(monkeypatch, tmp_path, best_ff, want):
    """Drives the real Fullscript card builder, so a call site that bypassed the view fails."""
    monkeypatch.setattr(app, "LOG_DB", _fs_db(tmp_path, best_ff))
    monkeypatch.setattr(app, "_fullscript_enabled", lambda: True)
    out = app._fullscript_for("a@b.com", "2026-07-01")
    row = out["groups"][0]["products"][0]
    assert row["name"] == "Mag Taurate"
    assert (row["ff"] or {}).get("slug") == want
