"""The hosted console hands an owner token a one-time pass to the local tools.

Glen, 2026-11-19: "Give Rae's computer a private, locked link to your Mac." The
launcher mints a pass and sends the browser over the private link. The master key
never appears in the redirect. With no pass secret the #2298 plain page stays.
"""
import importlib
from urllib.parse import parse_qs, urlsplit

import pytest

from dashboard import local_tool_pass as ltp

ROUTES = [("/console/biofield-intake", "Biofield Intake", "biofield-intake", "/"),
          ("/console/clinical-tags", "Clinical Tags", "clinical-tags", "/clinical-tags")]
HTML = {"Accept": "text/html,application/xhtml+xml"}
MASTER = "master-test-secret-value"
SECRET = "s" * 40


@pytest.fixture()
def app_mod(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    try:
        import app
        importlib.reload(app)
    except Exception as exc:
        # A broken import must fail these gate tests, never skip them.
        pytest.fail(f"app import failed: {exc}")
    app.app.config["TESTING"] = True
    import dashboard
    monkeypatch.setattr(dashboard, "CONSOLE_SECRET", MASTER)
    monkeypatch.setattr(app, "_present_console_key", lambda: "owner-token-a")
    monkeypatch.setattr(app, "_owner_token_ok", lambda k: k == "owner-token-a")
    monkeypatch.setattr(app, "_owner_name_for_token",
                        lambda k: "owner-a" if k == "owner-token-a" else "")
    monkeypatch.delenv("LOCAL_TOOL_TAILNET_URL", raising=False)
    return app


@pytest.mark.parametrize("path,tool,slug,tpath", ROUTES)
def test_the_page_offers_the_private_link_beside_the_plain_message(
        app_mod, monkeypatch, path, tool, slug, tpath):
    monkeypatch.setenv(ltp.SECRET_ENV, SECRET)
    with app_mod.app.test_client() as c:
        r = c.get(path, headers=HTML)
    body = r.get_data(as_text=True)
    assert r.status_code == 200 and "Location" not in r.headers
    assert f"{tool} runs on Glen's Mac" in body
    assert f"href='{path}?open=1" in body and "asleep" in body
    assert MASTER not in body and "pass=" not in body
    assert "no-store" in r.headers.get("Cache-Control", "")


@pytest.mark.parametrize("path,tool,slug,tpath", ROUTES)
def test_open_mints_a_pass_for_the_owner_and_this_tool(app_mod, monkeypatch, path, tool,
                                                       slug, tpath):
    monkeypatch.setenv(ltp.SECRET_ENV, SECRET)
    with app_mod.app.test_client() as c:
        r = c.get(path + "?open=1", headers=HTML)
    assert r.status_code == 302
    loc = r.headers["Location"]
    u = urlsplit(loc)
    assert (u.scheme, u.netloc, u.path) == ("http", "100.85.193.57:8011", tpath)
    tok = parse_qs(u.query)["pass"][0]
    assert ltp.redeem_pass(tok, slug, ltp.ReplayGuard()) == "owner-a"
    assert r.headers.get("Referrer-Policy") == "no-referrer"
    assert "no-store" in r.headers.get("Cache-Control", "")


@pytest.mark.parametrize("path,tool,slug,tpath", ROUTES)
def test_the_master_key_never_appears_in_the_redirect(app_mod, monkeypatch, path, tool,
                                                      slug, tpath):
    import base64
    monkeypatch.setenv(ltp.SECRET_ENV, SECRET)
    with app_mod.app.test_client() as c:
        r = c.get(path + "?open=1", headers=HTML)
    loc = r.headers["Location"]
    assert MASTER not in loc and "key=" not in loc and SECRET not in loc
    tok = parse_qs(urlsplit(loc).query)["pass"][0]
    body = tok.partition(".")[0]
    decoded = base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)).decode()
    assert MASTER not in decoded and "owner-token-a" not in decoded


@pytest.mark.parametrize("path,tool,slug,tpath", ROUTES)
def test_no_pass_secret_keeps_the_plain_page_and_mints_nothing(app_mod, monkeypatch, path,
                                                               tool, slug, tpath):
    monkeypatch.delenv(ltp.SECRET_ENV, raising=False)
    with app_mod.app.test_client() as c:
        for q in ("", "?open=1"):
            r = c.get(path + q, headers=HTML)
            body = r.get_data(as_text=True)
            assert r.status_code == 200 and "Location" not in r.headers
            assert f"{tool} runs on Glen's Mac" in body and "open=1" not in body
            assert "only works from that machine" in body


@pytest.mark.parametrize("path,tool,slug,tpath", ROUTES)
def test_a_lapsed_sign_in_gets_no_pass(app_mod, monkeypatch, path, tool, slug, tpath):
    monkeypatch.setenv(ltp.SECRET_ENV, SECRET)
    monkeypatch.setattr(app_mod, "_present_console_key", lambda: "")
    with app_mod.app.test_client() as c:
        r = c.get(path + "?open=1", headers=HTML)
        body = r.get_data(as_text=True)
        assert r.status_code == 200 and "Location" not in r.headers and "open=1" not in body
        r = c.get(path + "?open=1", headers={"Accept": "application/json"})
        assert r.status_code == 401


@pytest.mark.parametrize("path,tool,slug,tpath", ROUTES)
def test_the_master_key_path_is_unchanged(app_mod, monkeypatch, path, tool, slug, tpath):
    monkeypatch.setenv(ltp.SECRET_ENV, SECRET)
    monkeypatch.setattr(app_mod, "_present_console_key", lambda: MASTER)
    with app_mod.app.test_client() as c:
        r = c.get(path + "?open=1", headers=HTML)
    loc = r.headers["Location"]
    assert r.status_code == 302 and loc.startswith("http://127.0.0.1:8011")
    assert "pass=" not in loc


def test_the_back_path_rides_through_the_launcher(app_mod, monkeypatch):
    monkeypatch.setenv(ltp.SECRET_ENV, SECRET)
    with app_mod.app.test_client() as c:
        r = c.get("/console/biofield-intake", headers={**HTML,
                  "Referer": "http://localhost/console/sell"})
        assert "back=%2Fconsole%2Fsell" in r.get_data(as_text=True)
        r = c.get("/console/biofield-intake?open=1&back=%2Fconsole%2Fsell", headers=HTML)
        assert parse_qs(urlsplit(r.headers["Location"]).query)["back"] == ["/console/sell"]
        r = c.get("/console/biofield-intake?open=1&back=%2F%2Fevil.example", headers=HTML)
        assert "back" not in parse_qs(urlsplit(r.headers["Location"]).query)


def test_the_owner_token_never_rides_in_the_back_path(app_mod, monkeypatch):
    """op-nav.js puts ?key=<owner token> on every console link, so the Referer
    carries it. Neither the hosted launcher page nor the tailnet redirect may."""
    monkeypatch.setenv(ltp.SECRET_ENV, SECRET)
    with app_mod.app.test_client() as c:
        r = c.get("/console/biofield-intake", headers={**HTML,
                  "Referer": "http://localhost/console/sell?key=OWNERTOKEN123"})
        body = r.get_data(as_text=True)
        assert "back=%2Fconsole%2Fsell" in body
        assert "OWNERTOKEN123" not in body
        r = c.get("/console/biofield-intake?open=1&back=%2Fconsole%2Fsell%3Fkey%3DOWNERTOKEN123",
                  headers=HTML)
        loc = r.headers["Location"]
        assert "OWNERTOKEN123" not in loc
        assert parse_qs(urlsplit(loc).query)["back"] == ["/console/sell"]
