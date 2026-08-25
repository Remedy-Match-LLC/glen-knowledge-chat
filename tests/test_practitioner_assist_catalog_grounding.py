import app as appmod


CATALOG = {
    "perfect-skin": {"name": "Perfect Skin"},
    "bone-builder": {"name": "Bone Builder"},
}


def test_skin_support_is_canonicalized_to_perfect_skin(monkeypatch):
    monkeypatch.setattr(appmod, "_PRODUCTS", {"products": CATALOG})
    monkeypatch.setattr(
        appmod._pp, "name_to_slug",
        lambda name, _catalog: "perfect-skin" if name == "Perfect Skin" else None)

    items = [{"name": "Skin Support", "why": "skin barrier support"}]
    products = appmod._assist_resolve_products(items)
    answer = appmod._assist_ground_answer(
        "Use Skin Support as the primary formulation.", items, products)

    assert products == [{
        "name": "Perfect Skin", "why": "skin barrier support",
        "slug": "perfect-skin", "source_name": "Skin Support"}]
    assert answer == "Use Perfect Skin as the primary formulation."


def test_unresolved_invented_product_is_removed_from_answer(monkeypatch):
    monkeypatch.setattr(appmod, "_PRODUCTS", {"products": CATALOG})
    monkeypatch.setattr(appmod._pp, "name_to_slug", lambda _name, _catalog: None)
    monkeypatch.setattr(appmod, "embed", lambda _name: (_ for _ in ()).throw(RuntimeError()))

    items = [{"name": "Imaginary Glow Formula", "why": "made up"}]
    products = appmod._assist_resolve_products(items)
    answer = appmod._assist_ground_answer(
        "Add Imaginary Glow Formula to the order.", items, products)

    assert products == []
    assert "Imaginary Glow Formula" not in answer
    assert answer == "Add a catalog-confirmed formulation to the order."


def test_catalog_prompt_contains_only_active_orderable_products(monkeypatch):
    monkeypatch.setattr(appmod, "_PRODUCTS", {"products": {
        "perfect-skin": {"name": "Perfect Skin"},
        "retired": {"name": "Retired Product", "inactive": True},
        "external": {"name": "External Product", "info_only": True},
    }})

    prompt = appmod._assist_orderable_catalog_text()

    assert "perfect-skin: Perfect Skin" in prompt
    assert "Retired Product" not in prompt
    assert "External Product" not in prompt
