"""Browser check of the merge page: preview, apply with the in-page confirmation, undo.
Synthetic data only. Spec: docs/superpowers/specs/2026-09-26-merge-two-people-design.md"""
import sqlite3
import threading

import pytest

pytest.importorskip("playwright.sync_api")
from playwright.sync_api import sync_playwright  # noqa: E402

SECRET = "test-secret"
AOL, GMAIL = "mel@aol.com", "mel@gmail.com"


@pytest.fixture
def live(monkeypatch, tmp_path):
    import app as appmod
    import werkzeug.serving
    monkeypatch.setattr(appmod, "LOG_DB", str(tmp_path / "chat_log.db"))
    appmod._init_auth_tables()
    appmod._init_people_table()
    appmod._init_person_merge_tables()
    monkeypatch.setattr(appmod, "CONSOLE_SECRET", SECRET)
    from dashboard import person_merge_discover as pd
    monkeypatch.setattr(pd, "MOVE_TABLES", pd.MOVE_TABLES | {"orders_demo"})   # a stand-in table
    from dashboard import person_merge_evidence as ev
    monkeypatch.setattr(ev, "gmail_last_reply",
                        lambda e, service=None: "2026-09-20T00:00:00+00:00" if e == GMAIL else None)
    monkeypatch.setattr(appmod, "ghl_mark_merged", lambda e, s, stop: ("c1", None))
    monkeypatch.setattr(appmod, "ghl_unmark_merged", lambda e, s: ("c1", None))
    cx = sqlite3.connect(appmod.LOG_DB)
    cx.execute("INSERT INTO people (id, email, name, tags, created_at, updated_at) "
               "VALUES (1, ?, 'Mel Palmer', '[]', 't', 't')", (AOL,))
    cx.execute("INSERT INTO people (id, email, name, tags, created_at, updated_at) "
               "VALUES (2, ?, 'Mel Palmer', '[]', 't', 't')", (GMAIL,))
    cx.execute("CREATE TABLE orders_demo (id INTEGER PRIMARY KEY, email TEXT)")
    cx.execute("INSERT INTO orders_demo VALUES (1, ?)", (AOL,))
    cx.commit()
    cx.close()
    appmod.app.config["TESTING"] = True
    srv = werkzeug.serving.make_server("127.0.0.1", 0, appmod.app, threaded=True)
    port = srv.socket.getsockname()[1]
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{port}", appmod
    srv.shutdown()
    t.join()


def _people(appmod):
    with sqlite3.connect(appmod.LOG_DB) as cx:
        return [r[0] for r in cx.execute("SELECT id FROM people ORDER BY id")]


@pytest.mark.parametrize("size", [(390, 844), (1280, 900)], ids=["phone", "desktop"])
def test_preview_apply_and_undo(live, size):
    base, appmod = live
    with sync_playwright() as p:
        b = p.chromium.launch()
        ctx = b.new_context(extra_http_headers={"X-Console-Key": SECRET},
                            viewport={"width": size[0], "height": size[1]})
        page = ctx.new_page()
        page.goto(f"{base}/console/merge?survivor=2&merged=1")
        page.wait_for_selector("#apply-btn")
        text = page.inner_text("#preview")
        assert "Suggested: gmail" in text
        assert "orders demo" in text
        assert page.evaluate("() => document.documentElement.scrollWidth <= window.innerWidth")
        page.click("#apply-btn")
        page.wait_for_selector(".dialog")
        page.click("#dlg-no")                       # Cancel first: nothing happens
        assert _people(appmod) == [1, 2]
        page.click("#apply-btn")
        page.click("#dlg-go")
        page.wait_for_selector("#undo-btn")
        assert "Merged." in page.inner_text("#status")
        assert _people(appmod) == [2]
        page.click("#undo-btn")
        page.click("#dlg-go")
        page.wait_for_function("() => document.getElementById('status').innerText.includes('Undone')")
        assert _people(appmod) == [1, 2]
        b.close()


def test_the_applied_survivor_is_always_the_shown_one(live, monkeypatch):
    """Review round 2: the preview showed the suggested survivor selected but applied the
    other. The preview now always runs with the selected survivor."""
    base, appmod = live
    from dashboard import person_merge_evidence as ev
    monkeypatch.setattr(ev, "gmail_last_reply",
                        lambda e, service=None: "2026-09-25T00:00:00+00:00" if e == AOL else None)
    with sync_playwright() as p:
        b = p.chromium.launch()
        page = b.new_context(extra_http_headers={"X-Console-Key": SECRET}).new_page()
        page.goto(f"{base}/console/merge?survivor=2&merged=1")
        page.wait_for_function("() => document.querySelector('#preview h2') && "
                               "document.getElementById('preview').innerText.includes('Suggested: aol')")
        page.wait_for_selector("#apply-btn")
        page.click("#apply-btn")
        assert "into mel@aol.com" in page.inner_text(".dialog")
        page.click("#dlg-no")
        page.check(f'input[name="stay"][value="2"]')
        page.wait_for_function("() => document.querySelector('input[name=\"stay\"]:checked') && "
                               "document.querySelector('input[name=\"stay\"]:checked').value === '2'")
        page.click("#apply-btn")
        assert "into mel@gmail.com" in page.inner_text(".dialog")
        page.click("#dlg-no")
        b.close()


def test_a_blocked_merge_cannot_be_applied(live):
    base, appmod = live
    with sqlite3.connect(appmod.LOG_DB) as cx:
        cx.execute("CREATE TABLE coach_subscriptions (member_email TEXT PRIMARY KEY, status TEXT)")
        cx.execute("INSERT INTO coach_subscriptions VALUES (?, 'active')", (AOL,))
        cx.execute("INSERT INTO coach_subscriptions VALUES (?, 'active')", (GMAIL,))
    with sync_playwright() as p:
        b = p.chromium.launch()
        page = b.new_context(extra_http_headers={"X-Console-Key": SECRET}).new_page()
        page.goto(f"{base}/console/merge?survivor=2&merged=1")
        page.wait_for_selector("#apply-btn")
        assert "coach subscriptions" in page.inner_text("#preview")
        assert page.is_disabled("#apply-btn")
        b.close()
