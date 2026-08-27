import sqlite3

from tests.test_begin_routes import _load_app


def _client(monkeypatch, tmp_path):
    app_module = _load_app()
    db_path = str(tmp_path / "suggestions.db")
    monkeypatch.setattr(app_module, "LOG_DB", db_path)
    monkeypatch.setattr(app_module, "_send_inquiry_email", lambda *args, **kwargs: True)
    with sqlite3.connect(db_path) as cx:
        app_module.init_inquiry_tables(cx)
    return app_module.app.test_client(), app_module, db_path


def test_suggestion_records_optional_details(monkeypatch, tmp_path):
    client, _app_module, db_path = _client(monkeypatch, tmp_path)
    response = client.post("/api/practitioner-finder/suggest", json={
        "practitioner_name": "Dr. Kai Example",
        "profession": "Functional optometrist",
        "website": "https://example.com",
        "recommender_email": "client@example.com",
        "recommendation": "Listened carefully and helped me understand my options.",
    })
    assert response.status_code == 201
    assert response.get_json()["ok"] is True
    with sqlite3.connect(db_path) as cx:
        row = cx.execute(
            "SELECT practitioner_name, profession, website, recommender_email, "
            "recommendation, notification_sent FROM practitioner_suggestions"
        ).fetchone()
    assert row == (
        "Dr. Kai Example", "Functional optometrist", "https://example.com",
        "client@example.com", "Listened carefully and helped me understand my options.", 1,
    )


def test_suggestion_requires_practitioner_name(monkeypatch, tmp_path):
    client, _app_module, _ = _client(monkeypatch, tmp_path)
    response = client.post("/api/practitioner-finder/suggest", json={
        "recommendation": "A wonderful practitioner",
    })
    assert response.status_code == 400
    assert "Practitioner name" in response.get_json()["error"]


def test_suggestion_rejects_bad_optional_email(monkeypatch, tmp_path):
    client, _app_module, _ = _client(monkeypatch, tmp_path)
    response = client.post("/api/practitioner-finder/suggest", json={
        "practitioner_name": "Dr. Kai Example",
        "recommender_email": "not-an-email",
    })
    assert response.status_code == 400


def test_finder_has_suggestion_button_and_form():
    html = open("static/practitioner-finder.html", encoding="utf-8").read()
    assert 'id="suggest-practitioner-btn"' in html
    assert "Suggest a Practitioner" in html
    assert 'id="suggestion-form"' in html
    assert "/api/practitioner-finder/suggest" in html
    assert 'name="additional_details"' in html
