from pathlib import Path


HTML = (Path(__file__).resolve().parents[1] / "static" / "console-portal-links.html").read_text()


def test_portal_links_has_direct_open_action():
    assert "Open portal" in HTML
    assert "openPortal(this," in HTML


def test_open_action_fetches_tokenized_link_and_uses_new_tab():
    assert "window.open('about:blank', '_blank')" in HTML
    assert "'/api/console/portal-link?email='" in HTML
    assert "portalTab.location.replace(j.link)" in HTML


def test_open_action_handles_missing_portal_and_blocked_popup():
    assert "Pop-up blocked. Allow pop-ups and try again." in HTML
    assert "No portal for that email." in HTML
    assert "portalTab.close()" in HTML
