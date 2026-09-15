"""What the public store lists, how search matches, and how concern groups form."""
from dashboard import shop_catalog as sc

CATALOG = {
    "terrain-restore": {"slug": "terrain-restore", "name": "Terrain Restore",
                        "price_cents": 6997, "description": "gut terrain",
                        "ingredients": [{"name": "Humic acid"}]},
    "microbiome": {"slug": "microbiome", "name": "Microbiome", "price_cents": 6997,
                   "description": "flora", "image": "/static/product-photos/m.webp"},
    "info-page": {"slug": "info-page", "name": "Info Page", "info_only": True},
    "consult": {"slug": "consult", "name": "Consult", "service": True},
    "rival-cap": {"slug": "rival-cap", "name": "Rival Cap", "competitor": {"brand": "X"}},
    "electrolyte-mineral-manna": {"slug": "electrolyte-mineral-manna",
                                  "name": "Electrolyte Mineral Manna", "price_cents": 4997},
}


def get_product(slug):
    p = CATALOG.get(slug)
    return dict(p) if p else None


def test_listable_leaves_out_info_service_competitor_and_missing():
    assert sc.listable(get_product("terrain-restore"))
    for slug in ("info-page", "consult", "rival-cap"):
        assert not sc.listable(get_product(slug))
    assert not sc.listable(None)


def test_card_links_the_new_product_page_and_keeps_the_image():
    c = sc.card(get_product("microbiome"))
    assert c == {"slug": "microbiome", "name": "Microbiome", "price_cents": 6997,
                 "image": "/static/product-photos/m.webp", "url": "/begin/product/microbiome"}


def test_search_matches_name_description_and_ingredients():
    assert [c["slug"] for c in sc.search(CATALOG, get_product, "humic")] == ["terrain-restore"]
    assert [c["slug"] for c in sc.search(CATALOG, get_product, "FLORA")] == ["microbiome"]


def test_empty_search_lists_every_listable_product_by_name():
    slugs = [c["slug"] for c in sc.search(CATALOG, get_product, "")]
    assert slugs == ["electrolyte-mineral-manna", "microbiome", "terrain-restore"]


def test_search_respects_the_limit():
    assert len(sc.search(CATALOG, get_product, "", limit=2)) == 2


def test_concern_groups_skip_unlistable_and_do_not_recommend_products():
    programs = [
        {"condition_key": "symptom-digestion", "label": "Digestive Discomfort / Bloating",
         "items": [{"slug": "microbiome"}, {"slug": "info-page"},
                   {"slug": "electrolyte-mineral-manna"}, {"slug": "terrain-restore"}]},
        {"condition_key": "empty", "label": "Nothing sellable", "items": [{"slug": "consult"}]},
    ]
    groups = sc.concern_groups(programs, get_product)
    assert groups == [{"key": "symptom-digestion", "label": "Digestive Discomfort / Bloating",
                       "slugs": ["microbiome", "terrain-restore"]}]
