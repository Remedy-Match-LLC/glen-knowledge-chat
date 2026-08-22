from dashboard import page_sharing as ps


def _product(slug):
    if slug == "mint-smart":
        return {"slug": slug, "name": "Mint Smart"}
    if slug == "old-name":
        return {"slug": "new-name", "name": "New Name"}
    return None


def test_static_public_pages_are_explicitly_shareable():
    page = ps.resolve_shareable_page("/begin/explore")
    assert page == {
        "page_key": "education:explore",
        "title": "Explore Remedy Match",
        "kind": "education",
        "canonical_path": "/begin/explore",
    }


def test_live_product_is_shareable_and_supersession_is_canonicalized():
    page = ps.resolve_shareable_page("/begin/product/old-name", product_lookup=_product)
    assert page["page_key"] == "product:new-name"
    assert page["canonical_path"] == "/begin/product/new-name"


def test_private_unknown_and_parameterized_paths_fail_closed():
    denied = (
        "/portal/private-token",
        "/console",
        "/begin/product/missing",
        "/begin/explore?next=/portal/private-token",
        "https://evil.example/begin/explore",
        "//evil.example/begin/explore",
        "/begin/product/../portal",
    )
    for path in denied:
        assert ps.resolve_shareable_page(path, product_lookup=_product) is None


def test_share_url_uses_only_canonical_server_values():
    page = ps.resolve_shareable_page("/begin/product/mint-smart", product_lookup=_product)
    assert ps.canonical_share_url("https://illtowell.com/", page, "jane-doe") == (
        "https://illtowell.com/begin/product/mint-smart?ref=jane-doe&src=share"
    )

