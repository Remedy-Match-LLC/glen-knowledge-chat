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


# ── the pages ────────────────────────────────────────────────────────────────

def _script(page):
    src = (ROOT / "static" / f"console-{page}.html").read_text()
    # Drop // comments so a remark cannot satisfy or trip a check.
    return re.sub(r"(?m)^\s*//.*$", "", src)


@pytest.mark.parametrize("page", PAGES)
def test_no_page_waits_for_a_stored_key_before_loading(page):
    js = _script(page)
    assert not re.search(r"if\s*\(\s*key\(\)\s*\)\s*\{?\s*document\.getElementById\('gate'\)", js), page
    assert re.search(r"document\.getElementById\('gate'\)\.style\.display\s*=\s*'none'", js), page


@pytest.mark.parametrize("page", [p for p in PAGES if p != "client-orders"])
def test_a_401_still_brings_the_prompt_back(page):
    js = _script(page)
    assert re.search(r"status\s*===\s*401\s*\)\s*\{[^}]*getElementById\('gate'\)\.style\.display\s*=\s*'(?:flex|)'",
                     js), page


def test_client_orders_says_unauthorised_on_a_401():
    assert "status===401){ msg.textContent='Unauthorized" in _script("client-orders")
