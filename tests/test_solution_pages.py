"""Solution category pages: data loading, rule checks, view and HTML."""
import json

import pytest

from dashboard import solution_pages as sp

PRODUCTS = {
    "kloud-pemf-mini": {"name": "Kloud PEMF Mat (Mini)", "price_cents": 99900},
    "old-mat": {"name": "Old Mat", "inactive": True},
    "info-page": {"name": "Info", "info_only": True},
    "fungifuge": {"name": "Fungifuge"},
    "prill-bottle": {"name": "Living Water Bottle (prill beads)"},
    "water-ionizer-5plate": {"name": "5-Plate Water Ionizer (Living Water)"},
}


def cat(**kw):
    base = {"slug": "pemf", "title": "PEMF", "principle": "p", "choose_by_use": [],
            "products": ["kloud-pemf-mini"], "learn": [], "table_names": []}
    base.update(kw)
    return base


def test_load_reads_the_categories_list(tmp_path):
    f = tmp_path / "c.json"
    f.write_text(json.dumps({"categories": [cat()]}))
    assert [c["slug"] for c in sp.load(f)] == ["pemf"]


def test_load_raises_on_a_missing_file(tmp_path):
    with pytest.raises(OSError):
        sp.load(tmp_path / "nope.json")


def test_excluded_covers_do_not_recommend_and_the_prill_bottle():
    assert sp.is_excluded("fungifuge", PRODUCTS["fungifuge"])
    assert sp.is_excluded("prill-bottle", PRODUCTS["prill-bottle"])
    # The plate ionizers are the primary recommendation. The shared brand must not hide them.
    assert not sp.is_excluded("water-ionizer-5plate", PRODUCTS["water-ionizer-5plate"])


def test_problems_is_empty_for_good_data():
    assert sp.problems([cat(table_names=["PEMF"])], PRODUCTS, ["PEMF"]) == []


@pytest.mark.parametrize("bad, fragment", [
    (cat(products=["nope"]), "not in the catalogue"),
    (cat(products=["old-mat"]), "not active"),
    (cat(products=["info-page"]), "not active"),
    (cat(products=["fungifuge"]), "do-not-recommend"),
    (cat(products=[], learn=[]), "no products and no learn page"),
    (cat(slug="Bad Slug"), "slug"),
])
def test_problems_names_each_broken_rule(bad, fragment):
    out = sp.problems([bad], PRODUCTS, [])
    assert any(fragment in p for p in out), out


def test_problems_requires_each_table_name_exactly_once():
    two = [cat(table_names=["172 Hz"]), cat(slug="other", table_names=["172 Hz"])]
    assert any("172 Hz" in p and "2 categories" in p for p in sp.problems(two, PRODUCTS, ["172 Hz"]))
    assert any("172 Hz" in p and "no category" in p for p in sp.problems([cat()], PRODUCTS, ["172 Hz"]))


def test_problems_flags_a_duplicate_category_slug():
    assert any("twice" in p for p in sp.problems([cat(), cat()], PRODUCTS, []))


def _get(products):
    def get_product(slug):
        p = products.get(slug)
        if p and p.get("superseded_by"):
            return get_product(p["superseded_by"])
        return dict(p, slug=slug) if p else None
    return get_product


VIEW_PRODUCTS = {
    "pouch": {"name": "EMF Shielding Pouch 7” x 4” Cell Phone", "price_cents": 3500},
    "old-pouch": {"name": "Old Pouch", "inactive": True, "superseded_by": "pouch"},
    "gone": {"name": "Gone", "inactive": True},
    "fungifuge": {"name": "Fungifuge", "price_cents": 4000},
}


def test_view_drops_inactive_and_excluded_and_dedupes_a_superseded_twin():
    c = cat(products=["pouch", "old-pouch", "gone", "fungifuge"], learn=["emf-sensitivity"],
            principle="First.\n\nSecond.")
    v = sp.category_view(c, _get(VIEW_PRODUCTS), learn_name=lambda s: "EMF Sensitivity")
    assert [p["slug"] for p in v["products"]] == ["pouch"]
    assert v["products"][0]["url"] == "/begin/product/pouch"
    assert v["principle"] == ["First.", "Second."]
    assert v["learn"] == [{"slug": "emf-sensitivity", "name": "EMF Sensitivity"}]


def test_view_drops_a_learn_page_that_is_not_public():
    v = sp.category_view(cat(learn=["fasting"]), _get(VIEW_PRODUCTS), learn_name=lambda s: None)
    assert v["learn"] == []


def test_a_view_with_nothing_to_show_is_not_visible():
    v = sp.category_view(cat(products=["gone"], learn=["fasting"]), _get(VIEW_PRODUCTS),
                         learn_name=lambda s: None)
    assert not sp.is_visible(v)


def test_category_html_escapes_names_and_links_each_card():
    c = cat(title="Water & Hydrogen", products=["pouch"], choose_by_use=["Travel <light>."])
    html = sp.render_category_html(sp.category_view(c, _get(VIEW_PRODUCTS)))
    assert "Water &amp; Hydrogen" in html
    assert "Travel &lt;light&gt;." in html
    assert 'href="/begin/product/pouch"' in html
    assert "7” x 4”" in html and "$35.00" in html
    assert "<script" not in html


def test_hub_html_links_every_visible_category():
    views = [sp.category_view(cat(slug="pemf", title="PEMF", principle="Pulse."), _get(PRODUCTS))]
    html = sp.render_hub_html(views)
    assert 'href="/solutions/pemf"' in html and "Pulse." in html
