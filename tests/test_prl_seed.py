import json, os
SEED = os.path.join(os.path.dirname(__file__), "..", "data", "prl_seed.json")

def test_seed_shape_and_integrity():
    d = json.load(open(SEED))
    assert set(d) == {"products", "focus_area_products", "focus_area_items"}
    assert len(d["products"]) >= 143
    names = {p["name"] for p in d["products"]}
    # every focus-area product references a known catalog product
    for fp in d["focus_area_products"]:
        assert fp["prl_product_name"] in names, fp["prl_product_name"]
    # item->FA map is populated (43 focus areas' infoceuticals)
    assert len(d["focus_area_items"]) > 100
    # relations are within the taxonomy or null
    for p in d["products"]:
        assert p["relation"] in (None, "substitute", "complement", "consider")


def test_a_trademark_mark_does_not_hide_the_crosswalk_row():
    """The crosswalk writes "Tranquinol"; the catalog writes "Tranquinol®". An exact
    lookup gave 9 products no suggestion until 2026-10-07."""
    import json, os
    seed = json.load(open(os.path.join(os.path.dirname(__file__), "..", "data", "prl_seed.json")))
    by = {p["name"]: p for p in seed["products"]}
    assert by["Tranquinol®"]["best_ff"] == "Stress Release"
    assert by["ThyroVen™"]["best_ff"] == "Thyroid Support"
