from pathlib import Path

import app as appmod


ROOT = Path(__file__).parents[1]


def test_all_practitioner_surfaces_have_consistent_cross_navigation():
    expected = (
        "/practitioner/portal",
        "/practitioner/dropship",
        "/practitioner/client-account",
    )
    for filename in (
        "practitioner-portal.html",
        "practitioner-dropship.html",
        "practitioner-settings.html",
    ):
        page = (ROOT / "static" / filename).read_text()
        for href in expected:
            assert href in page, f"{filename} missing {href}"


def test_practitioner_page_captures_valid_url_session_in_cookie(monkeypatch):
    monkeypatch.setattr(
        appmod._pp, "practitioner_id_from_session",
        lambda token: "p1" if token == "SESSION" else None)

    response = appmod.app.test_client().get(
        "/practitioner/portal?token=SESSION", follow_redirects=False)

    assert response.status_code == 200
    assert "rm_practitioner_session=SESSION" in response.headers.get("Set-Cookie", "")
    assert "HttpOnly" in response.headers.get("Set-Cookie", "")


def test_practitioner_api_accepts_captured_session_cookie(monkeypatch):
    monkeypatch.setattr(
        appmod._pp, "practitioner_id_from_session",
        lambda token: "p1" if token == "SESSION" else None)
    monkeypatch.setattr(
        appmod._pp, "portal_data",
        lambda pid, **kwargs: {"email": "doc@example.com", "name": "Doc"})
    client = appmod.app.test_client()
    client.set_cookie("rm_practitioner_session", "SESSION")

    response = client.get("/api/practitioner/portal-data")

    assert response.status_code == 200
    assert response.get_json()["email"] == "doc@example.com"


def test_client_bridge_targets_the_public_practitioner_host():
    source = (ROOT / "app.py").read_text()
    assert "PUBLIC_BASE_URL.rstrip('/')}/practitioner/portal?token=" in source
