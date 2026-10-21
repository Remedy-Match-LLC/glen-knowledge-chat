"""Review round 3 for the report links and #chat sign-in (2026-10-20), adopted as
written by the reviewer: each test names the mutation it kills or the finding it pins
(plans/reviews/links/round3.md in the vault)."""
import json
import os
import re
import sqlite3
import subprocess
import textwrap
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

from tests.test_portal_login_next import _providers_on, client  # noqa: F401

# In tests/ this is the repo root; run from elsewhere, set DEPLOY_CHAT_ROOT.
ROOT = Path(os.environ.get("DEPLOY_CHAT_ROOT") or Path(__file__).resolve().parent.parent)


# ── Kills M12: a provider start must store only a clean key ──────────────────
# The finish path cleans again, so a raw value is not exploitable today. This pins
# the first check, so a later reader of extra["next"] cannot be handed a URL.

@pytest.mark.parametrize("provider", ["google", "apple"])
@pytest.mark.parametrize("nxt,want", [("chat", "chat"), ("https://evil.example", ""),
                                      ("//evil.example", ""), ("CHAT", "chat")])
def test_a_provider_start_stores_only_a_known_route_key(client, monkeypatch, provider, nxt, want):
    c, appmod = client
    _providers_on(appmod, monkeypatch)
    start = c.get(f"/portal/auth/{provider}/start", query_string={"next": nxt})
    state = parse_qs(urlparse(start.headers["Location"]).query)["state"][0]
    from dashboard import portal_providers as providers
    cx = sqlite3.connect(appmod.LOG_DB)
    row = cx.execute("SELECT extra FROM auth_tokens WHERE token_hash=?",
                     (providers._hash(state),)).fetchone()
    cx.close()
    assert json.loads(row[0])["next"] == want


# ── Kills M27 by behaviour (existing test only greps the source) ────────────
# test_the_login_page_sends_next_with_every_sign_in greps the source, so a filter
# moved to a dead branch still passes. This runs the page's own portalNext IIFE.

def _login_next(search, hash_):
    page = (ROOT / "static" / "client-login.html").read_text()
    m = re.search(r"const portalNext = \(function\(\)\{[\s\S]*?\}\)\(\);", page)
    assert m, "portalNext not found"
    js = textwrap.dedent(f"""
        const location = {{search: {json.dumps(search)}, hash: {json.dumps(hash_)}}};
        {m.group(0)}
        process.stdout.write(JSON.stringify(portalNext));
    """)
    out = subprocess.run(["node", "-e", js], capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


@pytest.mark.parametrize("search,hash_,want", [
    ("?next=chat", "", "chat"), ("", "#chat", "chat"), ("?next=CHAT", "", "chat"),
    ("?next=https://evil.example", "", ""), ("?next=%2F%2Fevil", "", ""),
    ("", "#%E0%A4%A", ""), ("?next=" + "a" * 41, "", ""), ("", "", "")])
def test_the_login_page_lets_only_a_route_shape_leave(search, hash_, want):
    assert _login_next(search, hash_) == want


# ── F2 (fixed): the Terms gate must keep a deep link for every route ─────────
# A client who has not agreed the Terms follows the weekly invitation's
# /portal/<token>#calendar. The gate shows; applyPortalHash falls through for
# #calendar (only #intake and #chat return early), and the new clearing step
# removes the hash. After "I agree", load() renders again with no hash, and the
# client lands on the hub, not the calendar. Before the branch, the hash survived.

def _gate_run(hash_):
    page = (ROOT / "static" / "client-portal.html").read_text()
    start = page.index("function portalShellLive(){")
    end = page.index('window.addEventListener("hashchange", applyPortalHash);')
    src = page[start:end]
    js = textwrap.dedent("""
        const vm = require('vm');
        const reg = {'portal-tos-card': {setAttribute(){}, scrollIntoView(){}, focus(){}}};
        const location = {hash: %s, pathname: '/portal/t0k', search: '', href: 'https://x/portal/t0k'};
        const ctx = {
          document: {getElementById: id => reg[id] || null, querySelector: () => null},
          window: {setTimeout(fn){ fn(); }, __portalView: {}},
          location,
          history: {replaceState(s, t, url){ const i = url.indexOf('#');
                                             location.hash = i < 0 ? '' : url.slice(i); }},
          showTab(){}, _foldOpenCard(){}, URL, decodeURIComponent, encodeURIComponent };
        vm.createContext(ctx);
        vm.runInContext(%s + '\\nthis.applyPortalHash = applyPortalHash;', ctx);
        ctx.applyPortalHash();
        process.stdout.write(JSON.stringify(location.hash));
    """) % (json.dumps(hash_), json.dumps(src))
    out = subprocess.run(["node", "-e", js], capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


@pytest.mark.parametrize("route", ["#calendar", "#biofield-latest", "#cart", "#recs",
                                   "#intake", "#chat"])
def test_the_terms_gate_keeps_any_routes_hash_until_i_agree(route):
    assert _gate_run(route) == route


# ── Kills M23: a state from one provider tells the other nothing ─────────────

def test_a_google_state_posted_to_apple_returns_no_route(client, monkeypatch):
    c, appmod = client
    _providers_on(appmod, monkeypatch)
    start = c.get("/portal/auth/google/start?next=chat")
    state = parse_qs(urlparse(start.headers["Location"]).query)["state"][0]
    done = c.post("/portal/auth/apple/callback", data={"state": state, "code": "x"})
    assert done.headers["Location"] == "/portal/login?error=provider"


# ── Kills M45: an approved address is escaped inside href ────────────────────
# Clinical's file is the only source, and it is https-checked, but a quote in a
# URL must not end the attribute.

def test_an_address_with_a_quote_cannot_leave_the_href():
    from dashboard import biofield_plan_report as pr
    out = pr.sentence_html("Use the portal chat.",
                           {"portal chat": 'https://x.example/"onmouseover="alert(1)'})
    assert 'href="https://x.example/&quot;onmouseover=&quot;alert(1)"' in out
    assert 'onmouseover="' not in out


# ── Kills M32: a re-render never takes the cursor, even if the hash survives ──
# Today the hash clear hides the guard: with no hash, a re-render finds no route.
# Where replaceState throws (it is wrapped in try), the hash stays, and only
# `followed && route.focus` keeps every poll from pulling the cursor to the chat.

def test_a_rerender_with_the_hash_still_set_does_not_take_the_cursor():
    page = (ROOT / "static" / "client-portal.html").read_text()
    src = page[page.index("function portalShellLive(){"):
               page.index('window.addEventListener("hashchange", applyPortalHash);')]
    js = textwrap.dedent("""
        const vm = require('vm');
        let focused = [];
        const el = id => ({setAttribute(){}, scrollIntoView(){}, focus(){ focused.push(id); }});
        const reg = {chatCard: el('chatCard'), chatInput: el('chatInput')};
        const location = {hash: '#chat', pathname: '/portal/me', search: '', href: 'https://x/portal/me#chat'};
        const ctx = {
          document: {getElementById: id => reg[id] || null,
                     querySelector: s => /"(hub|ask)"/.test(s) ? {} : null},
          window: {setTimeout(fn){ fn(); }, __portalView: {}}, location,
          history: {replaceState(){ throw new Error('SecurityError'); }},
          showTab(){}, _foldOpenCard(){}, URL, decodeURIComponent, encodeURIComponent };
        vm.createContext(ctx);
        vm.runInContext(%s + '\\nthis.applyPortalHash = applyPortalHash;', ctx);
        ctx.applyPortalHash();           // followed: may focus
        ctx.window._portalHashOpened = 'chat';
        focused = [];
        ctx.applyPortalHash();           // a render re-applying the same hash
        process.stdout.write(JSON.stringify(focused));
    """) % json.dumps(src)
    out = subprocess.run(["node", "-e", js], capture_output=True, text=True, check=True)
    assert json.loads(out.stdout) == []
