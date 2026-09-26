"""Browser check: staff in a client's portal see a banner and are asked before acting.

The guarded route's view is swapped for a recorder, so the check counts what actually
reached it. Synthetic data only.
Spec: docs/superpowers/specs/2026-09-26-portal-staff-guard-design.md
"""
import sqlite3
import threading

import pytest

pytest.importorskip("playwright.sync_api")
from playwright.sync_api import sync_playwright  # noqa: E402

SECRET = "test-secret"
EMAIL = "guard-test@example.com"
CONTENT = {"greeting": "Aloha Test,"}


@pytest.fixture
def live(monkeypatch, tmp_path):
    import app as appmod
    import werkzeug.serving
    monkeypatch.setattr(appmod, "LOG_DB", str(tmp_path / "chat_log.db"))
    appmod._init_auth_tables()
    monkeypatch.setattr(appmod, "CONSOLE_SECRET", SECRET)
    monkeypatch.setattr(appmod, "_PORTAL_SHELL_ENABLED", True)
    monkeypatch.setattr(appmod, "_portal_tos_agreed", lambda email: True)
    from dashboard import client_portal as cp
    from dashboard import portal_identity as pi
    cx = sqlite3.connect(appmod.LOG_DB)
    cp.init_client_portal_table(cx)
    token, _ = cp.upsert_portal(cx, EMAIL, "Guard Test", CONTENT)
    pi._ensure_people_table(cx)
    cx.execute("INSERT OR IGNORE INTO people (email, name, roles, created_at, updated_at) "
               "VALUES (?,?,?,?,?)", (EMAIL, "Guard Test", '["client"]', "t", "t"))
    cx.commit()
    cx.close()
    calls = []
    endpoint, _ = appmod.app.url_map.bind("localhost").match(
        f"/api/portal/{token}/chat", method="POST")

    def _recorder(**kw):
        from flask import request
        calls.append(request.headers.get("X-Staff-Confirmed"))
        return appmod.jsonify({"ok": True}), 200

    monkeypatch.setitem(appmod.app.view_functions, endpoint, _recorder)
    appmod.app.config["TESTING"] = True
    srv = werkzeug.serving.make_server("127.0.0.1", 0, appmod.app, threaded=True)
    port = srv.socket.getsockname()[1]
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{port}", token, calls
    srv.shutdown()
    t.join()


SEND_JS = """(token) => {
  window.__result = null;
  fetch('/api/portal/' + token + '/chat', {method: 'POST',
        headers: {'Content-Type': 'application/json'}, body: '{"message":"hi"}'})
    .then(r => { window.__result = r.status; });
}"""


def _open(p, base, token, staff):
    browser = p.chromium.launch()
    ctx = browser.new_context(extra_http_headers={"X-Console-Key": SECRET} if staff else {})
    page = ctx.new_page()
    page.goto(f"{base}/portal/{token}")
    page.wait_for_function("() => typeof window._staffBanner === 'function'")
    page.wait_for_selector(".card")
    return browser, page


def test_staff_see_banner_and_cancel_sends_nothing_then_do_it_sends_once(live):
    base, token, calls = live
    with sync_playwright() as p:
        browser, page = _open(p, base, token, staff=True)
        page.wait_for_selector("#staffBanner")
        assert "Guard Test's portal as staff" in page.inner_text("#staffBanner")

        page.evaluate(SEND_JS, token)
        page.wait_for_selector(".staff-confirm")
        assert "Do this as Guard Test?" in page.inner_text(".staff-confirm")
        page.click(".staff-confirm-cancel")
        page.wait_for_function("() => window.__result !== null")
        assert page.evaluate("() => window.__result") == 409
        assert calls == []

        page.evaluate(SEND_JS, token)
        page.wait_for_selector(".staff-confirm")
        page.click(".staff-confirm-go")
        page.wait_for_function("() => window.__result !== null")
        assert page.evaluate("() => window.__result") == 200
        assert calls == ["1"]
        assert page.query_selector(".staff-confirm") is None
        browser.close()


def test_client_is_never_asked_and_sees_no_banner(live):
    base, token, calls = live
    with sync_playwright() as p:
        browser, page = _open(p, base, token, staff=False)
        page.evaluate(SEND_JS, token)
        page.wait_for_function("() => window.__result !== null")
        assert page.evaluate("() => window.__result") == 200
        assert calls == [None]
        assert page.query_selector("#staffBanner") is None
        assert page.query_selector(".staff-confirm") is None
        browser.close()
