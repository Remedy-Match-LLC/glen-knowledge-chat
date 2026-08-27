import app


def test_system_prompt_routes_supplement_analysis_to_public_review_page():
    prompt = app.get_system_prompt("self-healing")

    assert "SUPPLEMENT REVIEW PAGE" in prompt
    assert "https://illtowell.com/product-review" in prompt
    assert "free supplement-review page" in prompt
