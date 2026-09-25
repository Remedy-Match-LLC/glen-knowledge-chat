"""A signed-in owner is never asked for the console key.

2026-09-24: Glen signed in on Rae's browser with the emailed link and opened People: CRM.
It showed the key prompt. Two faults, both fixed here:

  1. Eight console pages hid the prompt only when localStorage held a key. A magic-link
     sign-in leaves a login cookie and no stored key, and Rae's site data had been
     cleared that morning. The pages now try to load either way; a 401 brings the
     prompt back.
  2. /api/ghl/queue/* compared the presented key to the master secret only. The
     before_request bridge turns an owner's cookie into her OWNER token, which every
     other console route accepts; this one refused it. A VA token stays refused.
"""
import re
import sqlite3
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PAGES = ["approvals", "client-orders", "crm", "money", "orders", "pricing-settings",
         "products", "taskboard"]


@pytest.fixture
def client(monkeypatch, tmp_path):
    import app as appmod
    db = str(tmp_path / "chat_log.db")
    monkeypatch.setattr(appmod, "LOG_DB", db)
    monkeypatch.setattr(appmod, "CONSOLE_SECRET", "test-secret")
    monkeypatch.setenv("CONSOLE_SECRET", "test-secret")
    monkeypatch.setenv("WEBHOOK_SECRET", "hook-secret")
    monkeypatch.setattr(appmod, "_role_for_token", lambda t: {
        "rae-owner-token": appmod._bos_rbac.OWNER,
        "shaira-va-token": appmod._bos_rbac.VA}.get(t))
    from dashboard import ghl_queue
    with sqlite3.connect(db) as cx:
        ghl_queue.init_ghl_queue_table(cx)
    appmod.app.config["TESTING"] = True
    return appmod


def _get(appmod, cookie=None, headers=None):
    c = appmod.app.test_client()
    if cookie:
        c.set_cookie(appmod.CONSOLE_COOKIE, cookie)
    return c.get("/api/ghl/queue/pending", headers=headers or {}).status_code


# ── the queue route ──────────────────────────────────────────────────────────

def test_an_owner_signed_in_by_magic_link_reads_the_queue(client):
    assert _get(client, cookie="rae-owner-token") == 200, "Rae's exact shape"


def test_an_owner_token_header_reads_the_queue(client):
    assert _get(client, headers={"X-Console-Key": "rae-owner-token"}) == 200


def test_a_master_cookie_reads_the_queue(client):
    c = client.app.test_client()
    assert c.get("/console/pages?key=test-secret", headers={"Accept": "text/html"}).status_code == 302
    assert c.get("/api/ghl/queue/pending").status_code == 200


def test_the_mac_drain_keeps_its_webhook_secret(client):
    assert _get(client, headers={"X-Webhook-Secret": "hook-secret"}) == 200
    assert _get(client, headers={"X-Console-Key": "test-secret"}) == 200


@pytest.mark.parametrize("how", [
    {"cookie": "shaira-va-token"},
    {"headers": {"X-Console-Key": "shaira-va-token"}},
    {"headers": {"X-Console-Key": "not-a-key"}},
    {"cookie": "not-a-key"},
    {},
], ids=["va-cookie", "va-header", "bad-header", "bad-cookie", "nothing"])
def test_everyone_else_is_refused(client, how):
    assert _get(client, **how) == 401


def test_the_result_route_uses_the_same_check(client):
    c = client.app.test_client()
    c.set_cookie(client.CONSOLE_COOKIE, "shaira-va-token")
    assert c.post("/api/ghl/queue/result", json={}).status_code == 401
    c.set_cookie(client.CONSOLE_COOKIE, "rae-owner-token")
    assert c.post("/api/ghl/queue/result", json={}).status_code != 401


# ── the queue route: what it must still refuse ───────────────────────────────

@pytest.mark.parametrize("key", ["test-secret", "rae-owner-token"])
def test_a_key_in_the_url_is_refused(client, key):
    """Round 1: these routes never took ?key=, and a secret in a URL lands in logs."""
    c = client.app.test_client()
    assert c.get("/api/ghl/queue/pending?key=" + key).status_code == 401


def test_a_text_plain_result_post_changes_nothing(client):
    """Round 1: a cross-site form can send text/plain with no preflight."""
    from dashboard import ghl_queue
    with sqlite3.connect(client.LOG_DB) as cx:
        qid = ghl_queue.enqueue(cx, op="tag_add", email="a@x.com", payload={"tag": "t"})
        cx.commit()
    c = client.app.test_client()
    c.set_cookie(client.CONSOLE_COOKIE, "rae-owner-token")
    r = c.post("/api/ghl/queue/result", data='{"id": %d, "status": "done"}' % qid,
               headers={"Content-Type": "text/plain"})
    assert r.status_code == 400
    with sqlite3.connect(client.LOG_DB) as cx:
        assert [row[0] for row in cx.execute("SELECT id FROM ghl_write_queue WHERE status='pending'")] == [qid]
    r = c.post("/api/ghl/queue/result", json={"id": qid, "status": "done"})
    assert r.status_code == 200, "the same call as JSON still works"


# ── the pages ────────────────────────────────────────────────────────────────

def _script(page):
    src = (ROOT / "static" / f"console-{page}.html").read_text()
    # Drop // comments so a remark cannot satisfy or trip a check.
    return re.sub(r"(?m)^\s*//.*$", "", src)


GATE_OFF = r"document\.getElementById\('gate'\)\.style\.display\s*=\s*'none'"


FIRST_LOAD = {"approvals": "start", "crm": "loadQueue", "money": "reveal", "orders": "load",
              "pricing-settings": "load", "products": "loadAll", "taskboard": "load",
              "client-orders": None}   # client orders loads nothing until a search


@pytest.mark.parametrize("page", PAGES)
def test_the_page_hides_the_prompt_at_load_without_a_condition(page):
    """A top-level statement, not one inside unlock() and not behind any test of a
    stored key, and it starts the page's first load (round 2: deleting the load call
    passed the earlier check)."""
    js = _script(page)
    call = FIRST_LOAD[page]
    # Round 3: the call must FOLLOW DIRECTLY. "...='none'; if (key()) loadQueue();" and a
    # trailing "// loadQueue();" both passed a looser pattern, and the first is Rae's bug.
    app_on = r"document\.getElementById\('app'\)\.style\.display\s*=\s*''\s*;\s*"
    tail = (r"\s*;\s*" + (app_on if page == "approvals" else "") + call + r"\(\)\s*;") if call \
        else r"\s*;\s*$"
    assert re.search(r"(?m)^\s*" + GATE_OFF + tail, js), page


@pytest.mark.parametrize("page", PAGES)
def test_no_stored_key_test_guards_the_prompt(page):
    js = _script(page)
    assert not re.search(r"if\s*\([^)]*(?:key\(\)|console_key)[^)]*\)\s*\)?\s*\{?\s*" + GATE_OFF, js), page


@pytest.mark.parametrize("page", PAGES)
def test_a_401_brings_the_prompt_back(page):
    js = _script(page)
    assert re.search(r"status\s*===\s*401\s*\)\s*\{[^}]*getElementById\('gate'\)\.style\.display\s*=\s*'(?:flex|)'",
                     js), page


def test_approvals_asks_auth_status_not_the_queues():
    """Round 2: counting queue 401s missed a logged-out user whose queues failed for
    another reason. One call to /api/console/auth-status decides the prompt."""
    js = _script("approvals")
    start = re.search(r"function start\(\)\{(.*?)\n  \}", js, re.S)
    assert start and "/api/console/auth-status" in start.group(1)
    assert re.search(r"status\s*===\s*401\s*\)\s*\{[^}]*getElementById\('gate'\)", start.group(1))
    load_all = re.search(r"function loadAll\(\)\{(.*?)\n  \}", js, re.S).group(1)
    assert "gate" not in load_all, "a queue's 401 must not show the prompt"


def test_client_orders_reruns_the_refused_search_after_unlock():
    js = _script("client-orders")
    unlock = re.search(r"function unlock\(\)\{(.*?)\}\s*$", js, re.S | re.M)
    assert unlock and "search()" in unlock.group(1)


def test_money_reloads_its_tabs_after_the_key_is_entered():
    js = _script("money")
    unlock = re.search(r"function unlock\(\)\{(.*?)reveal\(\);\s*\}", js, re.S)
    assert unlock and "_moneyLoaded[t]=false" in unlock.group(1)


def _function_body(js, name):
    m = re.search(r"(?:async\s+)?function " + name + r"\s*\([^)]*\)\s*\{", js)
    assert m, name
    depth, start = 0, m.end() - 1
    for i in range(start, len(js)):
        depth += {"{": 1, "}": -1}.get(js[i], 0)
        if depth == 0:
            return js[start:i]
    raise AssertionError(name)


# The function whose 401 a first load actually hits. Money's first load is inside a
# module (MoneyPayments), so it keeps the file-wide check above.
FIRST_401 = {"crm": "loadQueue", "orders": "load", "pricing-settings": "load",
             "taskboard": "load", "products": "loadCatalog", "client-orders": "search"}


@pytest.mark.parametrize("page", sorted(FIRST_401))
def test_the_first_load_itself_brings_the_prompt_back(page):
    """Round 3: CRM has two 401 handlers; removing the one in loadQueue passed."""
    body = _function_body(_script(page), FIRST_401[page])
    assert re.search(r"status\s*===\s*401\s*\)\s*\{[^}]*getElementById\('gate'\)\.style\.display\s*=\s*'(?:flex|)'",
                     body), page


def test_approvals_loads_when_signed_in():
    """Round 3: deleting loadAll() from start()'s success path passed."""
    start = _function_body(_script("approvals"), "start")
    assert re.search(r"return;\s*\}\s*loadAll\(\)\s*;", start)
    assert re.search(r"\.catch\(function\(\)\{\s*loadAll\(\)\s*;\s*\}\)", start)


def test_money_unlock_really_resets_the_tabs():
    """Round 3: a comment or a no-op loop holding the literal passed a substring check."""
    unlock = _function_body(_script("money"), "unlock")
    assert re.search(r"(?m)^\s*Object\.keys\(_moneyLoaded\)\.forEach\(function\(t\)\{\s*"
                     r"if\(t!=='lookup'\)\s*_moneyLoaded\[t\]=false;\s*\}\);", unlock)


def test_client_orders_unlock_really_reruns_the_search():
    unlock = _function_body(_script("client-orders"), "unlock")
    assert re.search(r"(?m)^\s*if\(document\.getElementById\('q'\)\.value\.trim\(\)\.length>=2\)\s*search\(\);", unlock)


def test_an_empty_webhook_secret_opens_nothing(client, monkeypatch):
    """Round 3: with WEBHOOK_SECRET unset, "" == "" must not let anyone in."""
    monkeypatch.setenv("WEBHOOK_SECRET", "")
    assert _get(client) == 401
    assert _get(client, headers={"X-Webhook-Secret": ""}) == 401


def test_the_check_uses_the_module_console_secret(client, monkeypatch):
    """Round 3: the module value, which _present_console_key also uses, decides."""
    monkeypatch.setenv("CONSOLE_SECRET", "some-other-value")
    assert _get(client, headers={"X-Console-Key": "test-secret"}) == 200
    assert _get(client, headers={"X-Console-Key": "some-other-value"}) == 401
