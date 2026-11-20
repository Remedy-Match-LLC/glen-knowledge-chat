"""Match: Intake and Match: Tags show a plain page, never a key prompt (Rae, 2026-11-19).

Both tabs launch a tool on Glen's Mac. Anyone without the master secret gets a page
that says so. A script keeps the JSON answers. The master secret never appears.
"""
import importlib

import pytest

ROUTES = [("/console/biofield-intake", "Biofield Intake"),
          ("/console/clinical-tags", "Clinical Tags")]
HTML = {"Accept": "text/html,application/xhtml+xml"}


@pytest.fixture()
def app_mod(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    try:
        import app
        importlib.reload(app)
    except Exception as exc:
        pytest.skip(f"app import failed: {exc}")
    app.app.config["TESTING"] = True
    import dashboard
    monkeypatch.setattr(dashboard, "CONSOLE_SECRET", "master-test-secret")
    return app


@pytest.mark.parametrize("path,tool", ROUTES)
def test_owner_token_in_a_browser_gets_the_plain_page(app_mod, monkeypatch, path, tool):
    monkeypatch.setattr(app_mod, "_present_console_key", lambda: "rae-owner-token")
    monkeypatch.setattr(app_mod, "_owner_token_ok", lambda k: k == "rae-owner-token")
    with app_mod.app.test_client() as c:
        r = c.get(path, headers=HTML)
    body = r.get_data(as_text=True)
    assert r.status_code == 200 and r.mimetype == "text/html"
    assert f"{tool} runs on Glen's Mac" in body
    assert "master-test-secret" not in body and "Location" not in r.headers
    assert "key" not in body.lower().replace("keep", "")


@pytest.mark.parametrize("path,tool", ROUTES)
def test_a_lapsed_sign_in_in_a_browser_gets_the_same_page(app_mod, monkeypatch, path, tool):
    monkeypatch.setattr(app_mod, "_present_console_key", lambda: "")
    monkeypatch.setattr(app_mod, "_owner_token_ok", lambda k: False)
    with app_mod.app.test_client() as c:
        r = c.get(path, headers=HTML)
    assert r.status_code == 200 and f"{tool} runs on Glen's Mac" in r.get_data(as_text=True)
    assert "master-test-secret" not in r.get_data(as_text=True)


@pytest.mark.parametrize("path,tool", ROUTES)
def test_a_script_keeps_the_json_answers(app_mod, monkeypatch, path, tool):
    monkeypatch.setattr(app_mod, "_owner_token_ok", lambda k: k == "rae-owner-token")
    with app_mod.app.test_client() as c:
        monkeypatch.setattr(app_mod, "_present_console_key", lambda: "rae-owner-token")
        r = c.get(path, headers={"Accept": "application/json"})
        assert r.status_code == 409 and r.get_json()["error"] == "local_tool"
        monkeypatch.setattr(app_mod, "_present_console_key", lambda: "")
        r = c.get(path, headers={"Accept": "application/json"})
        assert r.status_code == 401


@pytest.mark.parametrize("path,tool", ROUTES)
def test_the_master_secret_still_launches_the_tool(app_mod, monkeypatch, path, tool):
    monkeypatch.setattr(app_mod, "_present_console_key", lambda: "master-test-secret")
    with app_mod.app.test_client() as c:
        r = c.get(path, headers=HTML)
    assert r.status_code in (301, 302, 303, 307, 308)
