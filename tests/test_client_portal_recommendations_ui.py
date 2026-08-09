import app as app_module


def test_portal_page_has_recommendations_section_and_fetch():
    app_module.app.config["TESTING"] = True
    c = app_module.app.test_client()
    body = c.get("/portal/anytoken").get_data(as_text=True)  # page is static; token resolved client-side
    assert "/recommendations" in body                 # the page fetches the endpoint
    assert "My Recommendations" in body                # the section heading
    assert "renderRecommendations" in body             # the render function exists
    assert "/recommendations${dq}" in body             # selected scan date follows the fetch


def test_portal_page_wires_recommendation_writes():
    app_module.app.config["TESTING"] = True
    c = app_module.app.test_client()
    body = c.get("/portal/anytoken").get_data(as_text=True)
    assert "/recommendation/hide" in body
    assert "/recommendation/client-note" in body
    assert "/recommendation/section" in body


def test_recommendations_progressively_reveal_three_five_then_all():
    body = app_module.app.test_client().get("/portal/anytoken").get_data(as_text=True)
    assert '"See more (5)"' in body
    assert '`See all (${total})`' in body
    assert "visible < 5 ? Math.min(5, total) : total" in body
