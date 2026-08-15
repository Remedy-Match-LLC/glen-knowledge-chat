from pathlib import Path


HTML = (Path(__file__).resolve().parents[1] / "static" / "client-portal.html").read_text()


def test_portal_refreshes_stale_data_when_member_returns_to_open_tab():
    assert 'window.__portalLastLoadedAt = Date.now();' in HTML
    assert 'document.addEventListener("visibilitychange", refreshPortalWhenReturning);' in HTML
    assert 'window.addEventListener("focus", refreshPortalWhenReturning);' in HTML
    assert 'Date.now() - lastLoadedAt < 30000' in HTML


def test_return_refresh_protects_hidden_tabs_and_active_intake():
    assert 'if(document.visibilityState === "hidden") return;' in HTML
    assert 'load({preserveActiveIntake:true});' in HTML
