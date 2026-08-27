import app
import begin_funnel


def test_shopping_card_stays_in_funnel_and_explains_the_handoff():
    card = begin_funnel.CARD_CATALOG["product"]
    assert card["base_url"] == "/begin/match"
    assert card["internal"] is True
    assert "exact remedy page" in card["sub"]


def test_generic_shop_language_surfaces_the_shopping_card_not_voice_tools():
    cards = begin_funnel.surface_for_chat({}, ["where can I browse and shop for remedies?"], "")
    assert [c["key"] for c in cards][0] == "product"
    assert "voice_distinctions" not in [c["key"] for c in cards]
    assert cards[0]["href"] == "/begin/match"


def test_remedy_need_surfaces_guided_match_before_product_browse():
    cards = begin_funnel.surface_for_chat({}, ["what remedy for dry eyes?"], "")
    assert [c["key"] for c in cards][0] == "remedy_match"
    assert "product" in [c["key"] for c in cards]
    assert cards[0]["href"] == "/begin/match"


def test_matcher_resolves_catalog_products_to_stable_in_funnel_pages(monkeypatch):
    monkeypatch.setattr(app, "PUBLIC_BASE_URL", "https://illtowell.com")
    url, source = app._resolve_remedy_url("Therapeutic Nightlight")
    assert source == "catalog"
    assert url == "https://illtowell.com/begin/product/therapeutic-nightlight"
    assert "remedymatch.com" not in url


def test_main_bot_knows_each_shopping_door_and_product_classes():
    prompt = app.get_system_prompt("self-healing")
    assert "SHOPPING ROUTES" in prompt
    for url in ("https://illtowell.com/begin/match",
                "https://illtowell.com/reorder",
                "https://illtowell.com/product-review",
                "https://illtowell.com/begin/tools"):
        assert url in prompt
    assert "Infoceuticals are energetic-frequency, bioinformational remedies" in prompt
    assert "storefront search" in prompt


def test_every_internal_catalog_card_resolves_to_a_live_application_route():
    client = app.app.test_client()
    for key, card in begin_funnel.CARD_CATALOG.items():
        href = card["base_url"]
        if not href.startswith("/"):
            continue
        response = client.get(href)
        assert response.status_code in (200, 301, 302), (key, href, response.status_code)


def test_named_product_row_carries_class_and_purpose_for_guidance():
    directive = app.build_product_directive(
        query_text="What is the Therapeutic Nightlight for?")
    row = next(line for line in directive.splitlines()
               if line.strip().startswith("• Therapeutic Nightlight "))
    assert "class: device/tool" in row
    assert "purpose:" in row
    assert "660 nm" in row


def test_migrated_infoceutical_is_not_misclassified_as_nutritional():
    product = app._PRODUCTS["products"]["bfa-big-field-aligner-infoceutical"]
    assert not product.get("url"), "fixture must exercise the migrated no-URL record"
    assert "class: Infoceutical" in app._product_guidance_hint(
        "bfa-big-field-aligner-infoceutical")
