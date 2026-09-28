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
        # The page's own content fits the phone. (The shared console nav bar overflows on
        # every console page at phone width; that is its own issue, not this page's.)
        assert page.evaluate("() => { const w = document.querySelector('.wrap'); "
                             "return w.scrollWidth <= window.innerWidth && "
                             "w.getBoundingClientRect().right <= window.innerWidth + 1; }")
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


def test_the_owner_chooses_for_a_clash_with_no_rule(live):
    base, appmod = live
    with sqlite3.connect(appmod.LOG_DB) as cx:
        cx.execute("DROP TABLE IF EXISTS affiliate_signups")
        cx.execute("CREATE TABLE affiliate_signups (id INTEGER PRIMARY KEY, email TEXT UNIQUE, slug TEXT)")
        cx.execute("INSERT INTO affiliate_signups VALUES (1, ?, 'mel-aol')", (AOL,))
        cx.execute("INSERT INTO affiliate_signups VALUES (2, ?, 'mel-gm')", (GMAIL,))
    with sync_playwright() as p:
        b = p.chromium.launch()
        page = b.new_context(extra_http_headers={"X-Console-Key": SECRET}).new_page()
        page.goto(f"{base}/console/merge?survivor=2&merged=1")
        page.wait_for_selector("#apply-btn")
        assert page.is_disabled("#apply-btn")
        page.check('input[name="choice-affiliate_signups"][value="survivor"]')
        page.wait_for_function("() => { const b = document.getElementById('apply-btn'); "
                               "return b && !b.disabled; }")
        # Changing who stays clears the choice: "survivor" would now mean the other record.
        page.check('input[name="stay"][value="1"]')
        page.wait_for_function("() => { const b = document.getElementById('apply-btn'); "
                               "return b && b.disabled && document.querySelector("
                               "'input[name=\"stay\"]:checked').value === '1'; }")
        page.check('input[name="stay"][value="2"]')
        page.wait_for_function("() => document.querySelector('input[name=\"stay\"]:checked') && "
                               "document.querySelector('input[name=\"stay\"]:checked').value === '2'")
        page.check('input[name="choice-affiliate_signups"][value="survivor"]')
        page.wait_for_function("() => { const b = document.getElementById('apply-btn'); "
                               "return b && !b.disabled; }")
        page.click("#apply-btn")
        page.click("#dlg-go")
        page.wait_for_selector("#undo-btn")
        with sqlite3.connect(appmod.LOG_DB) as cx:
            assert cx.execute("SELECT slug FROM affiliate_signups").fetchall() == [("mel-gm",)]
        b.close()


# ── 2026-09-27: Glen pressed Apply three times and saw nothing ───────────────
# Each attempt was refused (409), and the page wrote the reason into the status line at the
# top, out of view. The button also never said the merge was done.
def _open(p, base):
    b = p.chromium.launch()
    page = b.new_context(extra_http_headers={"X-Console-Key": SECRET},
                         viewport={"width": 390, "height": 700}).new_page()
    page.goto(f"{base}/console/merge?survivor=2&merged=1")
    page.wait_for_selector("#apply-btn")
    return b, page


def test_a_refusal_is_shown_beside_the_button(live, monkeypatch):
    base, appmod = live
    from dashboard import person_merge as pm

    def refuse(*a, **k):
        raise pm.MergeBlocked([{"table": "carts", "column": "email"}])
    monkeypatch.setattr(pm, "apply", refuse)
    with sync_playwright() as p:
        b, page = _open(p, base)
        page.click("#apply-btn")
        page.click("#dlg-go")
        page.wait_for_function("() => document.getElementById('apply-msg') && "
                               "document.getElementById('apply-msg').innerText.includes('carts')")
        assert not page.is_disabled("#apply-btn")
        assert page.inner_text("#apply-btn") == "Apply merge"
        assert _people(appmod) == [1, 2]
        b.close()


def test_a_server_error_that_is_not_json_is_shown(live):
    base, appmod = live
    with sync_playwright() as p:
        b, page = _open(p, base)
        page.route("**/api/console/people/merge", lambda r: r.fulfill(
            status=502, body="<html>Bad gateway</html>", content_type="text/html"))
        page.click("#apply-btn")
        page.click("#dlg-go")
        page.wait_for_function("() => document.getElementById('apply-msg') && "
                               "document.getElementById('apply-msg').innerText.includes('502')")
        # The merge may have run before the gateway failed: no second press until a reload
        # (review round 1).
        assert page.is_disabled("#apply-btn")
        assert "reload" in page.inner_text("#apply-msg").lower()
        b.close()


def test_a_reply_without_a_merge_is_not_called_merged(live):
    """Round 2: a 200 holding {} announced "Merged. undefined records changed"."""
    base, appmod = live
    with sync_playwright() as p:
        b, page = _open(p, base)
        page.route("**/api/console/people/merge", lambda r: r.fulfill(
            status=200, body="{}", content_type="application/json"))
        page.click("#apply-btn")
        page.click("#dlg-go")
        page.wait_for_function("() => document.getElementById('apply-msg') && "
                               "document.getElementById('apply-msg').innerText.toLowerCase().includes('reload')")
        assert page.inner_text("#apply-btn") != "Merged"
        b.close()


def test_the_preview_is_locked_while_a_merge_runs(live):
    """Round 2: changing who stays mid-merge re-ran the preview and showed "Merged" beside
    a different pair, with its Apply enabled."""
    base, appmod = live
    with sync_playwright() as p:
        b, page = _open(p, base)
        held = []
        page.route("**/api/console/people/merge", lambda r: held.append(r))
        page.click("#apply-btn")
        page.click("#dlg-go")
        page.wait_for_function("() => document.getElementById('apply-btn').innerText.startsWith('Merging')")
        assert page.is_disabled('input[name="stay"][value="1"]')
        held[0].continue_()
        page.wait_for_selector("#undo-btn")
        b.close()


def test_the_button_says_merged_when_done(live):
    base, appmod = live
    with sync_playwright() as p:
        b, page = _open(p, base)
        page.click("#apply-btn")
        page.click("#dlg-go")
        page.wait_for_selector("#undo-btn")
        assert page.inner_text("#apply-btn") == "Merged"
        assert page.is_disabled("#apply-btn")
        assert "Merged." in page.inner_text("#apply-msg")
        assert _people(appmod) == [2]
        b.close()


def test_a_switched_off_button_says_why(live):
    base, appmod = live
    with sqlite3.connect(appmod.LOG_DB) as cx:
        cx.execute("DROP TABLE IF EXISTS affiliate_signups")
        cx.execute("CREATE TABLE affiliate_signups (id INTEGER PRIMARY KEY, email TEXT UNIQUE, slug TEXT)")
        cx.execute("INSERT INTO affiliate_signups VALUES (1, ?, 'mel-aol')", (AOL,))
        cx.execute("INSERT INTO affiliate_signups VALUES (2, ?, 'mel-gm')", (GMAIL,))
    with sync_playwright() as p:
        b, page = _open(p, base)
        assert page.is_disabled("#apply-btn")
        assert "Choose whose record stays" in page.inner_text("#apply-msg")
        b.close()
