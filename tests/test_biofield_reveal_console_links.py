from pathlib import Path


def test_reveal_console_distinguishes_client_portal_from_editor():
    html = (Path(__file__).parents[1] / "static" / "console-biofield-reveals.html").read_text()
    assert "View Client Portal" in html
    assert "/api/console/portal-link?email=" in html
    assert "Edit Portal →" in html
    assert "/console/biofield-portal?email=" in html
    assert "Open Portal →" not in html
