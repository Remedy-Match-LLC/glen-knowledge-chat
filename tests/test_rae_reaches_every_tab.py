"""Rae reaches every console tab on her login cookie, and the private-link pages carry the bar.

Glen, 2026-11-20: "several other tabs don't show the link. they ask for the secret key
still. She should have access to all the tabs. Once she connects to one, she also needs
the navigation header to all the tabs."

Three faults, each pinned here:
  1. _auth() read only the header and ?key=, so her cookie was refused on Home's todos
     and calendar, the dashboard cards and /api/me.
  2. Nine pages showed the key box when localStorage was empty, before asking the server.
  3. The local tools' pages, opened over the private link, carried no tab bar.
"""
import importlib.util
import re
import sqlite3
from pathlib import Path

import pytest

from dashboard import local_tool_pass as ltp

ROOT = Path(__file__).resolve().parent.parent


# ── 1. _auth() honours the login cookie ──────────────────────────────────────

@pytest.fixture
def client(monkeypatch, tmp_path):
    import app as appmod
    monkeypatch.setattr(appmod, "LOG_DB", str(tmp_path / "chat_log.db"))
    monkeypatch.setattr(appmod, "CONSOLE_SECRET", "test-secret")
    monkeypatch.setattr(appmod.dashboard, "CONSOLE_SECRET", "test-secret")
    appmod._init_auth_tables()
    appmod._init_workspace_schema()
    appmod._init_todos_table()
    appmod._init_calendar_table()
    appmod.app.config["TESTING"] = True
    _seed(appmod, "rae-owner-token", "rae")
    _seed(appmod, "shaira-va-token", "shaira")
    return appmod


def _seed(appmod, token, owner):
    with sqlite3.connect(appmod.LOG_DB) as cx:
        cx.execute("INSERT INTO workspace_users (name, display_name, scope) VALUES (?,?,?)",
                   (owner, owner.title(), f"workspace:{owner}"))
        uid = cx.execute("SELECT id FROM workspace_users WHERE name=?", (owner,)).fetchone()[0]
        cx.execute("INSERT INTO access_tokens (token, user_id, note) VALUES (?,?,?)",
                   (token, uid, "test"))
        cx.commit()


def _c(appmod, cookie=None):
    c = appmod.app.test_client()
    if cookie:
        c.set_cookie(appmod.CONSOLE_COOKIE, cookie)
    return c


def test_her_cookie_alone_makes_api_me_say_rae(client):
    j = _c(client, "rae-owner-token").get("/api/me").get_json()
    assert j["nav"] == "rae" and j["role"] == "owner" and j["name"] == "Rae"


@pytest.mark.parametrize("path", ["/api/todos?owner=glen&status=open",
                                  "/api/calendar?range=today&kind=events"])
def test_her_cookie_alone_opens_home_data(client, path):
    assert _c(client, "rae-owner-token").get(path).status_code == 200


@pytest.mark.parametrize("cookie", ["shaira-va-token", "not-a-key", None],
                         ids=["va-cookie", "bad-cookie", "nothing"])
def test_other_cookies_still_get_nothing(client, cookie):
    j = _c(client, cookie).get("/api/me").get_json()
    assert j["nav"] is None
    assert _c(client, cookie).get("/api/todos?owner=glen&status=open").status_code == 401


def test_a_master_cookie_is_glen(client):
    c = client.app.test_client()
    c.get("/console/pages?key=test-secret", headers={"Accept": "text/html"})
    assert c.get("/api/me").get_json()["nav"] == "glen"


def test_a_va_header_keeps_its_own_identity_over_an_owner_cookie(client):
    j = _c(client, "rae-owner-token").get(
        "/api/me", headers={"X-Console-Key": "shaira-va-token"}).get_json()
    assert j["nav"] == "va", "a valid key is never upgraded by a cookie"


def test_a_stale_header_yields_to_her_cookie(client):
    j = _c(client, "rae-owner-token").get(
        "/api/me", headers={"X-Console-Key": "old-rotated-key"}).get_json()
    assert j["nav"] == "rae"


# ── 2. pages ask the server before showing the key box ──────────────────────

def _js(name):
    src = (ROOT / "static" / name).read_text()
    return re.sub(r"(?m)^\s*//.*$", "", src)


BOOT_PAGES = ["reviews", "biofield-reveals", "biofield-portal", "pages", "remedy-meanings"]


@pytest.mark.parametrize("page", BOOT_PAGES)
def test_boot_asks_the_server_before_the_gate(page):
    js = _js(f"console-{page}.html")
    assert "async function boot(){\n  if(!key() && !(await rmCookieSignedIn())){" in js
    assert not re.search(r"function boot\(\)\{\n  if\(!key\(\)\)\{", js)
    assert re.search(r"function rmCookieSignedIn\(\)\{\s*return fetch\('/api/console/auth-status', "
                     r"\{credentials:'same-origin'\}\)\s*\.then\(function\(r\)\{ return r\.ok; \}\)"
                     r"\.catch\(function\(\)\{ return false; \}\);\s*\}", js)


def test_membership_shows_the_form_on_her_cookie():
    js = _js("membership-admin.html")
    assert re.search(r"if \(KEY\) showForm\(\);\s*else fetch\('/api/console/auth-status'.*?"
                     r"if \(r\.ok\) showForm\(\);", js, re.S)


def test_projects_tries_the_server_with_no_stored_key():
    js = _js("console-projects.html")
    body = js[js.index("async function load()"):js.index("function setStatus")]
    assert "if (!key) { showKeyPrompt(); return; }" not in body
    assert ("if (r.status === 401) {\n      localStorage.removeItem(KEY_STORAGE);\n"
            "      showKeyPrompt();\n      return;\n    }") in body
    idea = js[js.index("async function addIdea()"):]
    assert "if (!key) { showKeyPrompt(); return; }" not in idea[:600]


def test_top_products_loads_on_open_without_a_typed_key():
    js = _js("console-top-products.html")
    assert re.search(r"loadTable\(\);\s*</script>\s*</body>", js)
    assert re.search(r"function getKey\(\) \{\s*return document\.getElementById\(\"consoleKey\"\)"
                     r"\.value\.trim\(\);\s*\}", js), "only a typed key; a stored one would go in the URL"
    assert "Enter your console key and click Load." not in js


def test_the_dashboard_loads_its_cards_on_her_cookie():
    js = _js("dashboard.html")
    assert "if(!consoleKey) showLockout();" not in js
    assert re.search(r"let signedIn = !!consoleKey;\s*if\(!consoleKey\)\{\s*fetch\(\"/api/console/auth-status\""
                     r".*?if\(r\.ok\)\{ signedIn = true; refreshAll\(\); \} else showLockout\(\);\ \}\)\s*"
                     r"\.catch\(\(\) => showLockout\(\)\);", js, re.S)
    assert "if(!signedIn){ showLockout(); return; }\n  cards.forEach" in js
    assert "if(!signedIn) return;" in js
    assert 'if(!signedIn){ showLockout(); return; }\n  const btn = document.getElementById("regenIntel");' in js
    assert "consoleKey = v;\n      signedIn = true;" in js


# ── 3. the tab bar on the private-link pages ─────────────────────────────────

def test_op_nav_base_mode_points_home_and_drops_the_key():
    js = _js("op-nav.js")
    assert re.search(r"if \(BASE && !/\^https:\\/\\/\[a-z0-9.-\]\+\$/i\.test\(BASE\)\) return;", js)
    i_base = js.index('var BASE = (script && script.dataset && script.dataset.base) || "";')
    # Round 2: no key is even READ in this mode, from the URL or from storage.
    assert 'var urlKey = BASE ? "" : (new URLSearchParams(location.search).get("key") || "");' in js
    assert 'if (!BASE) { try { storedKey = localStorage.getItem("console_key") || ""; } catch (e) {} }' in js
    assert i_base < js.index("var urlKey") < js.index("var storedKey")
    assert "t.href = BASE + t.href;" in js and "m.href = BASE + m.href;" in js
    assert "if (!BASE) bar += '<span class=\"op-nav-search-wrap\">'" in js
    assert 'if (!BASE) fetch("/api/me"' in js
    assert 'if (!BASE && (cached === "rae"' in js, "no cached profile can narrow her bar"
    assert "if (!effKey && !BASE) {" in js
    assert "if (!BASE && !document.querySelector('script[src=\"/static/ask-guide.js\"]'))" in js


SECRET = "p" * 40
MASTER = "test-master-console-secret"
TAILNET = "100.85.193.57"
TBASE = f"http://{TAILNET}:8011"


def _mod():
    spec = importlib.util.spec_from_file_location("biofield_local_app_tabs",
                                                  ROOT / "biofield_local_app.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


bl = _mod()


@pytest.fixture()
def local(tmp_path, monkeypatch):
    monkeypatch.setenv("CONSOLE_SECRET", MASTER)
    monkeypatch.setenv(ltp.SECRET_ENV, SECRET)
    monkeypatch.delenv("PUBLIC_BASE_URL", raising=False)
    app = bl.create_app(db_path=str(tmp_path / "t.db"), e4l_db=str(tmp_path / "e4l.db"))
    app.config.update(TESTING=True)
    return app


def _tail(c, path):
    return c.get(path, base_url=TBASE, environ_overrides={
        bl._TAILNET_ENVIRON: True, bl._TAILNET_HOST_ENVIRON: TAILNET})


def _signed_in(c, tool, path):
    r = _tail(c, f"{path}?pass={ltp.mint_pass('owner-a', tool, secret=SECRET)}")
    assert r.status_code == 200
    assert "op-nav.js" not in r.get_data(as_text=True), "the opening page stays bare"


@pytest.mark.parametrize("tool,path,sub", [("clinical-tags", "/clinical-tags", "tags"),
                                           ("biofield-intake", "/", "intake")])
def test_a_private_link_page_carries_the_bar(local, tool, path, sub):
    with local.test_client() as c:
        _signed_in(c, tool, path)
        r = _tail(c, path)
        body = r.get_data(as_text=True)
        assert r.status_code == 200
        tag = ('<script src="/static/op-nav.js" data-base="https://illtowell.com" '
               f'data-active="clinical" data-sub="{sub}"></script>')
        assert body.count(tag) == 1
        assert body.index(tag) > body.lower().index("<body")
        assert MASTER not in body and "key=" not in tag
        js = _tail(c, "/static/op-nav.js")
        assert js.status_code == 200 and b"BASE + t.href" in js.data


def test_the_bar_is_not_added_without_a_session(local):
    with local.test_client() as c:
        r = _tail(c, "/clinical-tags")
        assert r.status_code == 401 and "op-nav.js" not in r.get_data(as_text=True)


def test_glens_loopback_pages_are_unchanged(local):
    with local.test_client() as c:
        c.get(f"/clinical-tags?key={MASTER}")
        r = c.get("/clinical-tags")
        assert r.status_code == 200 and "op-nav.js" not in r.get_data(as_text=True)


def test_the_tags_page_reaches_her_without_the_master_key(local):
    """The Match strip writes ?key=<master> on Glen's pages. Over the private link it
    must be gone, and the links must still work as plain links."""
    with local.test_client() as c:
        _signed_in(c, "clinical-tags", "/clinical-tags")
        body = _tail(c, "/clinical-tags").get_data(as_text=True)
        assert MASTER not in body
        assert 'href="https://illtowell.com/console/biofield-portal"' in body
        assert 'href="/formulation-map"' in body


def test_glens_own_tags_page_keeps_its_signed_in_links(local):
    with local.test_client() as c:
        c.get(f"/clinical-tags?key={MASTER}")
        assert f"biofield-portal?key={MASTER}" in c.get("/clinical-tags").get_data(as_text=True)


def test_a_page_holding_the_key_any_other_way_is_refused(local):
    local.add_url_rule("/leaky", "leaky", lambda: "<html><body>secret: %s</body></html>" % MASTER)
    local.add_url_rule("/leaky.json", "leaky_json",
                       lambda: bl.jsonify({"k": MASTER}))
    with local.test_client() as c:
        _signed_in(c, "clinical-tags", "/clinical-tags")
        for path in ("/leaky", "/leaky.json"):
            r = _tail(c, path)
            assert r.status_code == 500 and MASTER not in r.get_data(as_text=True), path
    with local.test_client() as c:
        c.get(f"/clinical-tags?key={MASTER}")
        assert MASTER in c.get("/leaky").get_data(as_text=True), \
            "the loopback listener is not filtered"


def test_a_redirect_carrying_the_key_is_cleaned_or_refused(local):
    from flask import redirect as _redir
    local.add_url_rule("/leaky-redirect", "leaky_redirect",
                       lambda: _redir("/clinical-tags?key=%s" % MASTER))
    local.add_url_rule("/leaky-header", "leaky_header",
                       lambda: ("ok", 200, {"X-Note": "k=" + MASTER}))
    with local.test_client() as c:
        _signed_in(c, "clinical-tags", "/clinical-tags")
        r = _tail(c, "/leaky-redirect")
        assert MASTER not in str(r.headers) and MASTER not in r.get_data(as_text=True)
        assert r.headers.get("Location", "").endswith("/clinical-tags")
        r = _tail(c, "/leaky-header")
        assert r.status_code == 500 and MASTER not in str(r.headers)


def test_a_text_file_sent_straight_through_is_checked(local, tmp_path):
    from flask import send_file
    leak = tmp_path / "leak.txt"
    leak.write_text("secret " + MASTER)
    local.add_url_rule("/leaky-file", "leaky_file", lambda: send_file(str(leak)))
    with local.test_client() as c:
        _signed_in(c, "clinical-tags", "/clinical-tags")
        r = _tail(c, "/leaky-file")
        assert r.status_code == 500 and MASTER not in r.get_data(as_text=True)


def test_a_padded_va_key_keeps_its_identity_over_an_owner_cookie(client):
    j = _c(client, "rae-owner-token").get(
        "/api/me", headers={"X-Console-Key": "shaira-va-token "}).get_json()
    assert j["nav"] == "va"


@pytest.mark.parametrize("path", ["/static/dashboard.html", "/static/console.html"])
def test_console_pages_are_not_served_over_the_private_link(local, path):
    with local.test_client() as c:
        _signed_in(c, "clinical-tags", "/clinical-tags")
        assert _tail(c, path).status_code == 404
        assert _tail(c, "/static/op-nav.js").status_code == 200


def test_byte_ranges_cannot_split_the_key_past_the_scrub(local, tmp_path):
    from flask import send_file
    leak = tmp_path / "leak.txt"
    leak.write_text("secret " + MASTER)
    local.add_url_rule("/leaky-range", "leaky_range", lambda: send_file(str(leak)))
    half = 7 + len(MASTER) // 2
    with local.test_client() as c:
        _signed_in(c, "clinical-tags", "/clinical-tags")
        for rng in (f"bytes=0-{half}", f"bytes={half + 1}-"):
            r = c.get("/leaky-range", base_url=TBASE, headers={"Range": rng}, environ_overrides={
                bl._TAILNET_ENVIRON: True, bl._TAILNET_HOST_ENVIRON: TAILNET})
            assert r.status_code == 500, rng


ODD = "a/b+c d&e"     # escapes differently under quote and quote_plus (round 3)


@pytest.fixture()
def odd_local(tmp_path, monkeypatch):
    monkeypatch.setenv("CONSOLE_SECRET", ODD)
    monkeypatch.setenv(ltp.SECRET_ENV, SECRET)
    app = bl.create_app(db_path=str(tmp_path / "t.db"), e4l_db=str(tmp_path / "e4l.db"))
    app.config.update(TESTING=True)
    return app


@pytest.mark.parametrize("form", ["raw", "quote", "quote_plus"])
def test_every_encoding_of_the_key_is_refused(odd_local, form):
    from urllib.parse import quote, quote_plus
    v = {"raw": ODD, "quote": quote(ODD, safe=""), "quote_plus": quote_plus(ODD)}[form]
    odd_local.add_url_rule("/enc", "enc", lambda: "<html><body>%s</body></html>" % v)
    with odd_local.test_client() as c:
        _signed_in(c, "clinical-tags", "/clinical-tags")
        r = _tail(c, "/enc")
        assert r.status_code == 500 and v not in r.get_data(as_text=True)


def test_a_double_escaped_key_link_is_cleaned_not_refused(local):
    local.add_url_rule("/dbl", "dbl",
                       lambda: '<html><body><a href="/x?a=1&amp;amp;key=%s">x</a></body></html>' % MASTER)
    with local.test_client() as c:
        _signed_in(c, "clinical-tags", "/clinical-tags")
        r = _tail(c, "/dbl")
        assert r.status_code == 200 and MASTER not in r.get_data(as_text=True)


def test_a_page_already_carrying_the_bar_gets_no_second_one(local):
    local.add_url_rule("/has-nav", "has_nav", lambda: (
        '<html><body><script src="/static/op-nav.js" data-active="x"></script></body></html>'))
    with local.test_client() as c:
        _signed_in(c, "clinical-tags", "/clinical-tags")
        body = _tail(c, "/has-nav").get_data(as_text=True)
        assert body.count("/static/op-nav.js") == 1 and "data-base" not in body


def test_a_post_reply_gets_no_bar(local):
    local.add_url_rule("/form", "form", lambda: "<html><body>ok</body></html>", methods=["POST"])
    with local.test_client() as c:
        _signed_in(c, "clinical-tags", "/clinical-tags")
        r = c.post("/form", base_url=TBASE, environ_overrides={
            bl._TAILNET_ENVIRON: True, bl._TAILNET_HOST_ENVIRON: TAILNET})
        assert r.status_code == 200 and "data-base" not in r.get_data(as_text=True)
