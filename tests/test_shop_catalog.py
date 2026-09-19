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
    # `competitor` on a product is the price comparison its own page shows, e.g. the Mithreal
    # cap against DefenderShield's. It marks our product, never a rival's.
    "mithreal-cap": {"slug": "mithreal-cap", "name": "Mithreal Cap", "price_cents": 5997,
                     "competitor": {"brand": "DefenderShield", "price_cents": 6499}},
    "electrolyte-mineral-manna": {"slug": "electrolyte-mineral-manna",
                                  "name": "Electrolyte Mineral Manna", "price_cents": 4997},
}


def get_product(slug):
    p = CATALOG.get(slug)
    return dict(p) if p else None


def test_listable_leaves_out_info_service_and_missing():
    assert sc.listable(get_product("terrain-restore"))
    for slug in ("info-page", "consult"):
        assert not sc.listable(get_product(slug))
    assert not sc.listable(None)


def test_card_links_the_new_product_page_and_keeps_the_image():
    c = sc.card(get_product("microbiome"))
    assert c == {"slug": "microbiome", "name": "Microbiome", "price_cents": 6997,
                 "image": "/static/product-photos/m.webp", "url": "/begin/product/microbiome"}


def test_card_falls_back_to_the_first_gallery_image_when_image_is_empty():
    product = {"slug": "nous-energy", "name": "Nous Energy", "price_cents": 5997,
               "images": [{"src": "/static/product-photos/nous-energy-1.webp", "alt": "Noni fruit"},
                          {"src": "/static/product-photos/nous-energy-2.webp", "alt": "Noni on lava rock"}]}
    c = sc.card(product)
    assert c["image"] == "/static/product-photos/nous-energy-1.webp"


def test_search_matches_name_description_and_ingredients():
    assert [c["slug"] for c in sc.search(CATALOG, get_product, "humic")] == ["terrain-restore"]
    assert [c["slug"] for c in sc.search(CATALOG, get_product, "FLORA")] == ["microbiome"]


def test_empty_search_lists_every_listable_product_by_name():
    slugs = [c["slug"] for c in sc.search(CATALOG, get_product, "")]
    assert slugs == ["electrolyte-mineral-manna", "microbiome", "mithreal-cap", "terrain-restore"]


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


def test_a_product_with_a_price_comparison_is_listed():
    assert sc.listable(get_product("mithreal-cap"))
