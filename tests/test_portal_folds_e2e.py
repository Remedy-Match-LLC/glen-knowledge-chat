"""Browser check: a real rendered portal, phone and desktop, every door.

The 2026-09-16 fold feature passed its unit tests and showed no toggles live, because it
measured cards inside hidden doors. This check renders the page and counts. Synthetic
data only. Spec: docs/superpowers/specs/2026-09-25-portal-folding-design.md
"""
import sqlite3
import threading

import pytest

pytest.importorskip("playwright.sync_api")
from playwright.sync_api import sync_playwright  # noqa: E402

SECRET = "test-secret"
EMAIL = "fold-test@example.com"

CONTENT = {
    "greeting": "Aloha Test,",
    "video": {"url": "https://example.com/v", "label": "Watch"},
    "layers": [{"n": 1, "title": "Calm", "meaning": "Calm layer.", "remedy": "Serenity",
                "dosing": "1 capsule daily"},
               {"n": 2, "title": "Liver", "meaning": "Liver layer.", "remedy": "Liver Support",
                "dosing": "1 capsule daily"}],
    "reorder_items": [{"slug": "nous-energy", "qty": 1}],
}


@pytest.fixture
def live(monkeypatch, tmp_path):
    import app as appmod
    import werkzeug.serving
    monkeypatch.setattr(appmod, "LOG_DB", str(tmp_path / "chat_log.db"))
    appmod._init_auth_tables()
    monkeypatch.setattr(appmod, "CONSOLE_SECRET", SECRET)
    monkeypatch.setattr(appmod, "_PORTAL_SHELL_ENABLED", True)
    monkeypatch.setenv("PORTAL_FOLDS_V2", "1")
    # A returning client has agreed to the Terms; the gate would hide every door.
    monkeypatch.setattr(appmod, "_portal_tos_agreed", lambda email: True)
    from dashboard import client_portal as cp
    cx = sqlite3.connect(appmod.LOG_DB)
    cp.init_client_portal_table(cx)
    token, _ = cp.upsert_portal(cx, EMAIL, "Fold Test", CONTENT)
    # /api/portal/<token>/view needs the person row, as in test_client_portal_routes.
    from dashboard import portal_identity as pi
    pi._ensure_people_table(cx)
    cx.execute("INSERT OR IGNORE INTO people (email, name, roles, created_at, updated_at) "
               "VALUES (?,?,?,?,?)", (EMAIL, "Fold Test", '["client"]', "t", "t"))
    cx.commit()
    cx.close()
    appmod.app.config["TESTING"] = True
    srv = werkzeug.serving.make_server("127.0.0.1", 0, appmod.app, threaded=True)
    port = srv.socket.getsockname()[1]
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{port}", token, appmod
    srv.shutdown()
    t.join()


COUNT_JS = """(door) => {
  const secs = [...document.querySelectorAll('section[data-door="' + door + '"]')].filter(s => !s.hidden);
  const cards = secs.flatMap(s => [...s.querySelectorAll('.card')]).filter(c =>
      (c.dataset.foldId || c.id) && !c.dataset.foldSkip && c.querySelector('h2,h3'));
  const toggles = cards.filter(c => [...c.children].some(x => x.classList.contains('card-fold')));
  return {cards: cards.length, toggles: toggles.length};
}"""

IDS_JS = """() => {
  const ids = [...document.querySelectorAll('.card')].map(c => c.dataset.foldId || c.id).filter(Boolean);
  const doubled = [...document.querySelectorAll('.card')].filter(c =>
      [...c.querySelectorAll('.card-fold')].length > 1).length;
  return {ids, doubled};
}"""


def _open(page, url):
    page.goto(url)
    page.wait_for_selector(".card")
    page.wait_for_function("() => typeof _foldsV2 === 'object' && _foldsV2 !== null")


def _doors(page):
    return page.evaluate("() => window.PortalShell.DOORS.map(d => d.key)")


@pytest.mark.parametrize("size", [(390, 844), (1280, 900)], ids=["phone", "desktop"])
def test_every_door_gets_a_toggle_on_every_card(live, size):
    base, token, _ = live
    with sync_playwright() as p:
        b = p.chromium.launch()
        page = b.new_page(viewport={"width": size[0], "height": size[1]})
        _open(page, f"{base}/portal/{token}")
        total = 0
        report = {}
        for door in _doors(page):
            page.evaluate("(d) => showDoor(d)", door)
            page.evaluate("() => wirePortalFolds()")
            got = page.evaluate(COUNT_JS, door)
            report[door] = got
            assert got["toggles"] == got["cards"], (door, got)
            total += got["toggles"]
        assert total >= 7, report
        ids = page.evaluate(IDS_JS)
        dupes = sorted({i for i in ids["ids"] if ids["ids"].count(i) > 1})
        assert not dupes, dupes
        assert ids["doubled"] == 0
        b.close()


def test_a_fold_survives_reload_and_a_second_device(live):
    base, token, _ = live
    with sync_playwright() as p:
        b = p.chromium.launch()
        page = b.new_page()
        _open(page, f"{base}/portal/{token}")
        page.evaluate("() => showDoor('scans')")
        page.evaluate("() => wirePortalFolds()")
        first = page.evaluate("""() => {
          const c = [...document.querySelectorAll('section[data-door="scans"]:not([hidden]) .card')]
            .find(c => [...c.children].some(x => x.classList.contains('card-fold')));
          return {id: c.dataset.foldId || c.id, folded: c.classList.contains('is-folded')};
        }""")
        with page.expect_response(lambda r: r.url.endswith("/folds") and r.request.method == "PUT"):
            page.click(f'[data-fold-id="{first["id"]}"] > .card-fold, #{first["id"]} > .card-fold')
        want = not first["folded"]

        def folded_on(pg):
            _open(pg, f"{base}/portal/{token}")
            pg.evaluate("() => showDoor('scans')")
            pg.evaluate("() => wirePortalFolds()")
            return pg.evaluate("(id) => document.querySelector('[data-fold-id=\"' + id + '\"], #' + id)"
                               ".classList.contains('is-folded')", first["id"])

        assert folded_on(page) is want
        other = b.new_context().new_page()          # no shared storage: a second device
        assert folded_on(other) is want
        b.close()


def test_staff_folds_never_change_the_clients_view(live):
    base, token, _ = live
    with sync_playwright() as p:
        b = p.chromium.launch()
        client = b.new_page()
        staff = b.new_context(extra_http_headers={"X-Console-Key": SECRET}).new_page()
        for pg in (client, staff):
            _open(pg, f"{base}/portal/{token}")
            pg.evaluate("() => showDoor('scans')")
            pg.evaluate("() => wirePortalFolds()")
        state = lambda pg: pg.evaluate(
            "() => [...document.querySelectorAll('.card')].filter(c => c.dataset.foldId)"
            ".map(c => [c.dataset.foldId, c.classList.contains('is-folded')])")
        client_before = state(client)
        with staff.expect_response(lambda r: r.url.endswith("/folds") and r.request.method == "PUT"):
            staff.click(".fold-bar-all")
        _open(client, f"{base}/portal/{token}")
        client.evaluate("() => showDoor('scans')")
        client.evaluate("() => wirePortalFolds()")
        assert state(client) == client_before
        assert staff.locator(".fold-bar-match").count() >= 1
        assert client.locator(".fold-bar-match").count() == 0
        with staff.expect_response(lambda r: "of=client" in r.url):
            staff.locator(".fold-bar-match").first.click()
        staff.wait_for_function("(want) => JSON.stringify([...document.querySelectorAll('.card')]"
                                ".filter(c => c.dataset.foldId).map(c => [c.dataset.foldId, "
                                "c.classList.contains('is-folded')])) === want",
                                arg=__import__("json").dumps(state(client), separators=(",", ":")))
        assert state(staff) == state(client)
        b.close()



HEADINGS_JS = """() => [...document.querySelectorAll('.card.is-folded')]
  .filter(c => c.offsetParent !== null)
  .map(c => ({id: c.dataset.foldId || c.id,
              visible: [...c.children].some(x => /^H[23]$/.test(x.tagName) && x.offsetHeight > 0)}))
  .filter(x => !x.visible).map(x => x.id)"""


def test_no_folded_card_loses_its_heading(live):
    """Final review, 2026-09-26: the intake card's heading is nested, so folding it left an
    empty box. Fold everything on every door, then check each folded card still shows a
    heading."""
    base, token, _ = live
    with sync_playwright() as p:
        b = p.chromium.launch()
        page = b.new_page()
        _open(page, f"{base}/portal/{token}")
        for door in _doors(page):
            page.evaluate("(d) => showDoor(d)", door)
            page.evaluate("() => wirePortalFolds()")
            if page.locator(f'section[data-door="{door}"]:not([hidden]) .fold-bar-all').count():
                page.evaluate("(d) => _foldAllOrRestore(d)", door)
            blank = page.evaluate(HEADINGS_JS)
            assert not blank, (door, blank)
        page.evaluate("() => showTab('intake')")
        assert not page.evaluate(HEADINGS_JS)
        b.close()
