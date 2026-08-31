# Shared Unsubscribe Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give every promotional email we send through the GHL conversations API a working unsubscribe link, and honor it everywhere.

**Architecture:** A new `dashboard/unsubscribe.py` mints HMAC-signed unsubscribe URLs and renders the footer in text and HTML. A `/email/unsubscribe` route confirms on GET and records on POST. Opt-outs land in the existing `email_suppression` table with `bounce_type='optout'`, so every sender that already calls `is_suppressed` honors them with no further change. `send_bulk` gains an opt-in `unsubscribe_scope` parameter; `weekly_live_invitation.py` composes the footer directly because it bypasses `send_bulk` and posts to the API itself.

**Tech Stack:** Python 3, Flask, `hmac`/`hashlib` (stdlib), pytest. Postgres in production via `dashboard/db.py`, SQLite locally.

**Spec:** `docs/superpowers/specs/2026-08-30-sequence-engine-design.md` (slice 1 of 5)

## Global Constraints

- **Never send real email from a test.** `send_via_ghl` returns early on `PYTEST_CURRENT_TEST`. Any new send path gets the same guard at its own entry point. A bare full-suite run has sent live email before.
- **Transactional mail gets no footer.** The footer is opt-in per call site, never automatic. Invoices, magic links and portal-ready notices must not gain an unsubscribe link.
- **Never put an email address in a query string** where a signed token would do. The signature is required; the address alone must not be enough to opt someone out.
- **Copy rules:** no em dashes, no ALL CAPS, "client" not "patient".
- **Secret source:** `CONSOLE_SECRET`, falling back to `WEBHOOK_SECRET`, read from the environment. Doppler is the source of truth; never write secrets to Render.
- **DB path:** routes and scripts use `app.LOG_DB`; `dashboard/inbox.py` uses its own `_db_path()`. Do not change either.
- **Base URL:** `PUBLIC_BASE_URL` (defaults to `https://illtowell.com`).

---

### Task 1: Signed unsubscribe tokens

**Files:**
- Create: `dashboard/unsubscribe.py`
- Test: `tests/test_unsubscribe_token.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `sign(email: str, scope: str) -> str`, `verify(email: str, scope: str, sig: str) -> bool`, `unsubscribe_url(email: str, scope: str = "global") -> str`. `scope` is `"global"` or a sequence slug. Later tasks depend on all three.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_unsubscribe_token.py
import os
import pytest
from dashboard import unsubscribe


@pytest.fixture(autouse=True)
def _secret(monkeypatch):
    monkeypatch.setenv("CONSOLE_SECRET", "test-secret-abc")
    monkeypatch.setattr(unsubscribe, "_SECRET", "test-secret-abc", raising=False)


def test_sign_is_stable_and_case_insensitive():
    assert unsubscribe.sign("A@B.com", "global") == unsubscribe.sign("a@b.com  ", "global")


def test_verify_accepts_its_own_signature():
    sig = unsubscribe.sign("a@b.com", "global")
    assert unsubscribe.verify("a@b.com", "global", sig) is True


def test_verify_rejects_a_signature_from_another_scope():
    sig = unsubscribe.sign("a@b.com", "nurture")
    assert unsubscribe.verify("a@b.com", "global", sig) is False


def test_verify_rejects_a_signature_for_another_address():
    sig = unsubscribe.sign("a@b.com", "global")
    assert unsubscribe.verify("c@d.com", "global", sig) is False


def test_verify_rejects_empty_signature():
    assert unsubscribe.verify("a@b.com", "global", "") is False


def test_url_carries_scope_and_signature_but_not_a_bare_address():
    url = unsubscribe.unsubscribe_url("a@b.com", "global")
    assert "/email/unsubscribe?" in url
    assert "s=" in url and "scope=global" in url
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /tmp/wt-deploy-chat-06bdc124 && python -m pytest tests/test_unsubscribe_token.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'dashboard.unsubscribe'`

- [ ] **Step 3: Write minimal implementation**

```python
# dashboard/unsubscribe.py
"""Unsubscribe links for promotional email.

GHL appends its own unsubscribe footer to `source: "workflow"` mail only. Anything
we send through the conversations API (`source: "app"`) arrives with none, so we
mint our own. Verified 2026-08-30 by reading delivered bodies.

An opt-out is recorded in `email_suppression` with `bounce_type='optout'` so that
every sender already calling `is_suppressed` honors it without further change.

The footer is opt-in per call site. Transactional mail (invoices, magic links,
portal-ready notices) must NOT carry one.
"""
from __future__ import annotations

import hashlib
import hmac
import os
from urllib.parse import quote

_SECRET = os.environ.get("CONSOLE_SECRET") or os.environ.get("WEBHOOK_SECRET", "")

GLOBAL = "global"


def _norm(email: str) -> str:
    return (email or "").strip().lower()


def _base() -> str:
    return os.environ.get("PUBLIC_BASE_URL", "https://illtowell.com").rstrip("/")


def sign(email: str, scope: str) -> str:
    """HMAC over address+scope. Mirrors _portal_claim_sign in app.py."""
    msg = f"unsub:{scope}:{_norm(email)}".encode()
    return hmac.new(_SECRET.encode(), msg, hashlib.sha256).hexdigest()[:40]


def verify(email: str, scope: str, sig: str) -> bool:
    if not sig:
        return False
    return hmac.compare_digest(sign(email, scope), sig)


def unsubscribe_url(email: str, scope: str = GLOBAL) -> str:
    e = _norm(email)
    return (f"{_base()}/email/unsubscribe?e={quote(e)}"
            f"&scope={quote(scope)}&s={sign(e, scope)}")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /tmp/wt-deploy-chat-06bdc124 && python -m pytest tests/test_unsubscribe_token.py -v`
Expected: 6 passed

- [ ] **Step 5: Mutation-test the signature check**

Temporarily change `verify` to `return True`. Run the tests. Expected: the three rejection tests go RED. Restore `verify`, re-run, expect green. A guard that cannot fail is not a guard.

- [ ] **Step 6: Commit**

```bash
cd /tmp/wt-deploy-chat-06bdc124
git add dashboard/unsubscribe.py tests/test_unsubscribe_token.py
git commit -m "Add signed unsubscribe tokens

API-sent email carries no GHL unsubscribe footer, so we mint our own.
HMAC mirrors the portal-claim pattern already in app.py."
```

---

### Task 2: Footer rendering

**Files:**
- Modify: `dashboard/unsubscribe.py`
- Test: `tests/test_unsubscribe_footer.py`

**Interfaces:**
- Consumes: `unsubscribe_url` from Task 1.
- Produces: `footer_text(email: str, scope: str = "global") -> str` and `footer_html(email: str, scope: str = "global") -> str`. Task 5 and Task 6 append these.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_unsubscribe_footer.py
import pytest
from dashboard import unsubscribe


@pytest.fixture(autouse=True)
def _secret(monkeypatch):
    monkeypatch.setattr(unsubscribe, "_SECRET", "test-secret-abc", raising=False)


def test_text_footer_contains_a_working_url():
    out = unsubscribe.footer_text("a@b.com")
    assert unsubscribe.unsubscribe_url("a@b.com") in out


def test_html_footer_is_an_anchor():
    out = unsubscribe.footer_html("a@b.com")
    assert '<a href="' in out and "Unsubscribe" in out


def test_html_footer_escapes_the_address():
    out = unsubscribe.footer_html('x"><script>@b.com')
    assert "<script>" not in out


def test_footer_identifies_the_sender():
    # CAN-SPAM requires a physical postal address in commercial mail.
    assert "Hawaii" in unsubscribe.footer_text("a@b.com")


def test_scope_flows_into_the_link():
    out = unsubscribe.footer_text("a@b.com", "nurture")
    assert "scope=nurture" in out
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /tmp/wt-deploy-chat-06bdc124 && python -m pytest tests/test_unsubscribe_footer.py -v`
Expected: FAIL with `AttributeError: module 'dashboard.unsubscribe' has no attribute 'footer_text'`

- [ ] **Step 3: Write minimal implementation**

Append to `dashboard/unsubscribe.py`:

```python
import html as _html

POSTAL = "Remedy Match LLC, Hilo, Hawaii"


def footer_text(email: str, scope: str = GLOBAL) -> str:
    return ("\n\n---\nYou are receiving this because you signed up at Remedy Match.\n"
            f"Unsubscribe: {unsubscribe_url(email, scope)}\n{POSTAL}")


def footer_html(email: str, scope: str = GLOBAL) -> str:
    url = _html.escape(unsubscribe_url(email, scope), quote=True)
    return ('<div style="margin-top:28px;padding-top:14px;border-top:1px solid #ddd;'
            'font-family:Arial,sans-serif;font-size:12px;line-height:1.5;color:#777">'
            "You are receiving this because you signed up at Remedy Match.<br>"
            f'<a href="{url}" style="color:#777">Unsubscribe</a><br>'
            f"{_html.escape(POSTAL)}</div>")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /tmp/wt-deploy-chat-06bdc124 && python -m pytest tests/test_unsubscribe_footer.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
cd /tmp/wt-deploy-chat-06bdc124
git add dashboard/unsubscribe.py tests/test_unsubscribe_footer.py
git commit -m "Render unsubscribe footer in text and HTML"
```

---

### Task 3: Record an opt-out in email_suppression

**Files:**
- Modify: `dashboard/email_suppression.py` (docstring, and a helper)
- Test: `tests/test_unsubscribe_optout_record.py`

**Interfaces:**
- Consumes: `email_suppression.add`, `email_suppression.is_suppressed` (existing).
- Produces: `email_suppression.add_optout(cx, email, source) -> None`. Task 4 calls it.

Reusing `email_suppression` rather than a new table is deliberate: every sender in the codebase already calls `is_suppressed`, so an opt-out is honored everywhere the moment it is written. The `bounce_type` column distinguishes it from a real bounce.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_unsubscribe_optout_record.py
from dashboard import db, email_suppression as es


def _cx():
    cx = db.connect(":memory:")
    es.init_table(cx)
    return cx


def test_optout_is_suppressed_afterwards():
    cx = _cx()
    assert es.is_suppressed(cx, "a@b.com") is False
    es.add_optout(cx, "a@b.com", "unsubscribe-link")
    assert es.is_suppressed(cx, "a@b.com") is True


def test_optout_is_recorded_as_optout_not_a_bounce():
    cx = _cx()
    es.add_optout(cx, "a@b.com", "unsubscribe-link")
    row = cx.execute("SELECT bounce_type FROM email_suppression "
                     "WHERE email='a@b.com'").fetchone()
    assert row[0] == "optout"


def test_optout_is_idempotent():
    cx = _cx()
    es.add_optout(cx, "A@B.com", "unsubscribe-link")
    es.add_optout(cx, "a@b.com", "unsubscribe-link")
    n = cx.execute("SELECT COUNT(*) FROM email_suppression").fetchone()[0]
    assert n == 1


def test_optout_does_not_overwrite_a_hard_bounce():
    cx = _cx()
    es.add(cx, "a@b.com", "hard", "550 no such user", "bounce-scanner")
    es.add_optout(cx, "a@b.com", "unsubscribe-link")
    row = cx.execute("SELECT bounce_type FROM email_suppression "
                     "WHERE email='a@b.com'").fetchone()
    assert row[0] == "hard"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /tmp/wt-deploy-chat-06bdc124 && python -m pytest tests/test_unsubscribe_optout_record.py -v`
Expected: FAIL with `AttributeError: module 'dashboard.email_suppression' has no attribute 'add_optout'`

- [ ] **Step 3: Write minimal implementation**

Append to `dashboard/email_suppression.py`:

```python
def add_optout(cx, email, source):
    """Record a recipient-initiated opt-out. Distinct from a bounce: the address
    is valid, the person asked to stop. Stored here so every sender that already
    calls is_suppressed honors it. Never downgrades an existing hard bounce."""
    if not email:
        return
    cx.execute("""INSERT INTO email_suppression(email,bounce_type,reason,source)
        VALUES(lower(?),'optout','recipient unsubscribed',?)
        ON CONFLICT(email) DO UPDATE SET source=excluded.source""",
        (email.strip().lower(), source))
    cx.commit()
```

Also update the module docstring's first line to read: `"""Email suppression list: addresses we must stop emailing — permanent delivery failures (hard bounces) and recipient opt-outs, distinguished by bounce_type."""`

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /tmp/wt-deploy-chat-06bdc124 && python -m pytest tests/test_unsubscribe_optout_record.py tests/test_email_suppression.py -v`
Expected: all passed, including the pre-existing suppression tests

- [ ] **Step 5: Commit**

```bash
cd /tmp/wt-deploy-chat-06bdc124
git add dashboard/email_suppression.py tests/test_unsubscribe_optout_record.py
git commit -m "Record recipient opt-outs in email_suppression

bounce_type='optout' keeps them distinct from delivery failures while
reusing the check every sender already makes."
```

---

### Task 4: The `/email/unsubscribe` route

**Files:**
- Modify: `app.py` (add near the `/portal/claim` route, around line 33600)
- Test: `tests/test_unsubscribe_route.py`

**Interfaces:**
- Consumes: `unsubscribe.verify` (Task 1), `email_suppression.add_optout` (Task 3).
- Produces: `GET /email/unsubscribe` (confirmation page) and `POST /email/unsubscribe` (records the opt-out).

GET must not mutate. Mail scanners and link prefetchers follow GET links, and a GET that opts someone out would unsubscribe people who never clicked.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_unsubscribe_route.py
import pytest
import app as appmod
from dashboard import db, email_suppression as es, unsubscribe


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setattr(unsubscribe, "_SECRET", "test-secret-abc", raising=False)
    appmod.app.config["TESTING"] = True
    with appmod.app.test_client() as c:
        yield c


def _sig(email, scope="global"):
    return unsubscribe.sign(email, scope)


def test_get_shows_a_confirmation_and_does_not_opt_out(client):
    e = "getter@example.com"
    r = client.get(f"/email/unsubscribe?e={e}&scope=global&s={_sig(e)}")
    assert r.status_code == 200
    with db.connect(appmod.LOG_DB) as cx:
        es.init_table(cx)
        assert es.is_suppressed(cx, e) is False


def test_post_with_valid_signature_opts_out(client):
    e = "poster@example.com"
    r = client.post("/email/unsubscribe",
                    data={"e": e, "scope": "global", "s": _sig(e)})
    assert r.status_code == 200
    with db.connect(appmod.LOG_DB) as cx:
        assert es.is_suppressed(cx, e) is True


def test_post_with_a_bad_signature_is_rejected(client):
    e = "victim@example.com"
    r = client.post("/email/unsubscribe",
                    data={"e": e, "scope": "global", "s": "not-a-signature"})
    assert r.status_code == 400
    with db.connect(appmod.LOG_DB) as cx:
        es.init_table(cx)
        assert es.is_suppressed(cx, e) is False


def test_a_signature_for_one_address_cannot_opt_out_another(client):
    r = client.post("/email/unsubscribe",
                    data={"e": "other@example.com", "scope": "global",
                          "s": _sig("mine@example.com")})
    assert r.status_code == 400
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /tmp/wt-deploy-chat-06bdc124 && python -m pytest tests/test_unsubscribe_route.py -v`
Expected: FAIL — GET returns 404, route does not exist

- [ ] **Step 3: Write minimal implementation**

Add to `app.py`:

```python
@app.route("/email/unsubscribe", methods=["GET", "POST"])
def email_unsubscribe():
    """Confirm on GET, record on POST. GET must stay side-effect free: mail
    scanners and link prefetchers follow GET links, and a mutating GET would
    unsubscribe people who never clicked."""
    from dashboard import unsubscribe as _un, email_suppression as _es
    src = request.form if request.method == "POST" else request.args
    email = (src.get("e") or "").strip().lower()
    scope = (src.get("scope") or _un.GLOBAL).strip()
    sig = (src.get("s") or "").strip()
    if not email or not _un.verify(email, scope, sig):
        return render_template_string(
            "<h2>That link is not valid</h2><p>Reply to any email and we will "
            "take care of it.</p>"), 400
    if request.method == "GET":
        return render_template_string(
            "<h2>Unsubscribe {{ e }}?</h2>"
            "<form method='post' action='/email/unsubscribe'>"
            "<input type='hidden' name='e' value='{{ e }}'>"
            "<input type='hidden' name='scope' value='{{ sc }}'>"
            "<input type='hidden' name='s' value='{{ s }}'>"
            "<button type='submit'>Yes, unsubscribe me</button></form>",
            e=email, sc=scope, s=sig)
    with db.connect(LOG_DB) as cx:
        _es.init_table(cx)
        _es.add_optout(cx, email, f"unsubscribe-link:{scope}")
    return render_template_string(
        "<h2>You are unsubscribed</h2><p>{{ e }} will not receive further "
        "mailings. If this was a mistake, just reply to any earlier email.</p>",
        e=email)
```

Confirm `render_template_string` and `request` are already imported in `app.py`; both are used elsewhere in the file.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /tmp/wt-deploy-chat-06bdc124 && python -m pytest tests/test_unsubscribe_route.py -v`
Expected: 4 passed

- [ ] **Step 5: Mutation-test the signature gate**

Temporarily delete `or not _un.verify(email, scope, sig)` from the guard. Run the tests. Expected: `test_post_with_a_bad_signature_is_rejected` and `test_a_signature_for_one_address_cannot_opt_out_another` go RED. Restore, re-run, expect green.

- [ ] **Step 6: Commit**

```bash
cd /tmp/wt-deploy-chat-06bdc124
git add app.py tests/test_unsubscribe_route.py
git commit -m "Add /email/unsubscribe route

GET confirms, POST records. A mutating GET would let link prefetchers
unsubscribe people who never clicked."
```

---

### Task 5: `send_bulk` gains an opt-in footer

**Files:**
- Modify: `dashboard/inbox.py:665-692`
- Test: `tests/test_send_bulk_unsubscribe.py`

**Interfaces:**
- Consumes: `unsubscribe.footer_text`, `unsubscribe.footer_html` (Task 2).
- Produces: `send_bulk(to_email, subject, body, from_name=None, html=None, unsubscribe_scope=None)`. When `unsubscribe_scope` is None the body is unchanged, which keeps every existing caller byte-identical.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_send_bulk_unsubscribe.py
import pytest
from dashboard import inbox, unsubscribe


@pytest.fixture()
def captured(monkeypatch):
    seen = {}

    def fake_send_email(to, subject, body, from_name=None, html=None):
        seen.update(to=to, subject=subject, body=body, html=html)
        return {"via": "gmail"}

    monkeypatch.setattr(inbox, "send_email", fake_send_email)
    monkeypatch.setattr(inbox, "_is_undeliverable", lambda e: False)
    monkeypatch.delenv("BULK_VIA_GHL", raising=False)
    monkeypatch.setattr(unsubscribe, "_SECRET", "test-secret-abc", raising=False)
    return seen


def test_without_scope_the_body_is_untouched(captured):
    inbox.send_bulk("a@b.com", "Subj", "Hello there", html="<p>Hello there</p>")
    assert captured["body"] == "Hello there"
    assert "unsubscribe" not in (captured["html"] or "").lower()


def test_with_scope_the_footer_is_appended_to_both_parts(captured):
    inbox.send_bulk("a@b.com", "Subj", "Hello there",
                    html="<p>Hello there</p>", unsubscribe_scope="global")
    assert "Unsubscribe:" in captured["body"]
    assert "/email/unsubscribe?" in captured["body"]
    assert "/email/unsubscribe?" in captured["html"]


def test_footer_is_addressed_to_the_recipient(captured):
    inbox.send_bulk("who@example.com", "Subj", "Hi", unsubscribe_scope="global")
    assert unsubscribe.sign("who@example.com", "global") in captured["body"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /tmp/wt-deploy-chat-06bdc124 && python -m pytest tests/test_send_bulk_unsubscribe.py -v`
Expected: FAIL with `TypeError: send_bulk() got an unexpected keyword argument 'unsubscribe_scope'`

- [ ] **Step 3: Write minimal implementation**

Change the signature and add the append immediately before the `BULK_VIA_GHL` branch:

```python
def send_bulk(to_email: str, subject: str, body: str, from_name: Optional[str] = None,
              html: Optional[str] = None,
              unsubscribe_scope: Optional[str] = None) -> dict:
    """... existing docstring ...

    unsubscribe_scope: when set, appends an unsubscribe footer to both the text
    and HTML parts. Opt-in on purpose — transactional mail (invoices, magic
    links, portal-ready notices) must NOT carry one."""
```

Then, after the suppression check and before `if os.environ.get("BULK_VIA_GHL"):`

```python
    if unsubscribe_scope:
        from dashboard import unsubscribe as _un
        body = (body or "") + _un.footer_text(to_email, unsubscribe_scope)
        if html:
            html = html + _un.footer_html(to_email, unsubscribe_scope)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /tmp/wt-deploy-chat-06bdc124 && python -m pytest tests/test_send_bulk_unsubscribe.py tests/test_inbox.py -v`
Expected: all passed, including the pre-existing inbox tests

- [ ] **Step 5: Commit**

```bash
cd /tmp/wt-deploy-chat-06bdc124
git add dashboard/inbox.py tests/test_send_bulk_unsubscribe.py
git commit -m "send_bulk: opt-in unsubscribe footer

Default None keeps every existing caller byte-identical, so transactional
mail cannot accidentally gain a footer."
```

---

### Task 6: Retrofit the weekly live invitation

**Files:**
- Modify: `scripts/weekly_live_invitation.py` (the `_copy` function around line 236-244, and its call site around line 370)
- Test: `tests/test_weekly_live_invitation_unsubscribe.py`

**Interfaces:**
- Consumes: `unsubscribe.footer_text`, `unsubscribe.footer_html` (Task 2).
- Produces: `_copy(first, portal_url, eligible, target_date, email)` — note the added trailing `email` parameter.

This is the largest bare sender: 1,912 + 1,013 + 958 = 3,883 of the 4,660 promotional sends found in the audit. It bypasses `send_bulk` and posts to the conversations API directly, so it composes the footer itself.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_weekly_live_invitation_unsubscribe.py
import datetime
import pytest
from dashboard import unsubscribe
from scripts import weekly_live_invitation as wli


@pytest.fixture(autouse=True)
def _secret(monkeypatch):
    monkeypatch.setattr(unsubscribe, "_SECRET", "test-secret-abc", raising=False)


def _call(email):
    return wli._copy("Sam", "https://portal.example/x", True,
                     datetime.date(2026, 9, 2), email)


def test_text_part_carries_an_unsubscribe_link():
    text, _ = _call("a@b.com")
    assert "/email/unsubscribe?" in text


def test_html_part_carries_an_unsubscribe_link():
    _, body_html = _call("a@b.com")
    assert "/email/unsubscribe?" in body_html


def test_link_is_signed_for_that_recipient():
    text, _ = _call("who@example.com")
    assert unsubscribe.sign("who@example.com", "weekly-live") in text


def test_footer_is_outside_the_escaped_body():
    # The body is html.escape()d; the footer's anchor must survive as markup.
    _, body_html = _call("a@b.com")
    assert '<a href="' in body_html
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /tmp/wt-deploy-chat-06bdc124 && python -m pytest tests/test_weekly_live_invitation_unsubscribe.py -v`
Expected: FAIL with `TypeError: _copy() takes 4 positional arguments but 5 were given`

- [ ] **Step 3: Write minimal implementation**

Change the `_copy` signature to accept the recipient, and append the footer **after** the `html.escape` call so the anchor is not escaped:

```python
def _copy(first, portal_url, eligible, target_date, email=""):
    ...
    escaped = html.escape(text).replace("\n\n", "</p><p>").replace("\n", "<br>")
    body_html = ('<div style="font-family:Arial,sans-serif;font-size:16px;'
                 'line-height:1.55"><p>' + escaped + "</p></div>")
    if email:
        from dashboard import unsubscribe as _un
        text = text + _un.footer_text(email, "weekly-live")
        body_html = body_html + _un.footer_html(email, "weekly-live")
    return text, body_html
```

Then update the call site (around line 370) to pass the address:

```python
                text, body_html = _copy(first, portal_url, email in eligible,
                                        target_date, email)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /tmp/wt-deploy-chat-06bdc124 && python -m pytest tests/test_weekly_live_invitation_unsubscribe.py -v`
Expected: 4 passed

- [ ] **Step 5: Verify with the script's own dry run**

Run: `cd /tmp/wt-deploy-chat-06bdc124 && doppler run -p remedy-match -c prd -- python3 scripts/weekly_live_invitation.py --dry-run 2>&1 | head -40`

Expected: the dry-run output shows the composed body containing `/email/unsubscribe?`. Confirm the run reports `DRY_RUN` and sends nothing. If the script has no `--dry-run` flag, read its `argparse` setup and use whatever flag it does provide; do not run it without one.

- [ ] **Step 6: Commit**

```bash
cd /tmp/wt-deploy-chat-06bdc124
git add scripts/weekly_live_invitation.py tests/test_weekly_live_invitation_unsubscribe.py
git commit -m "Weekly live invitation: append unsubscribe footer

Largest bare sender at 3,883 of the 4,660 promotional sends found in the
2026-08-30 audit. Footer is appended after html.escape so the anchor survives."
```

---

### Task 7: Unsubscribe footer in the vault content sender

**Files:**
- Create: `~/AI-Training/03 Marketing/ghl-email-automation/unsub.py`
- Modify: `~/AI-Training/03 Marketing/ghl-email-automation/ghl_send.py` (`cmd_send` loop ~line 228, `cmd_test` ~line 207)
- Test: `~/AI-Training/03 Marketing/ghl-email-automation/tests/test_unsub.py`

**Interfaces:**
- Consumes: the same `CONSOLE_SECRET` and message format as `dashboard/unsubscribe.py` (Task 1).
- Produces: `unsub.footer_html(email: str, scope: str) -> str` and `unsub.sign(email, scope) -> str`.

The five editorial subjects from the audit — "Your Energy 4 Life experience now goes
deeper" (503 sends), "The morning I was told I was going blind" (81), "40% of kids are
now nearsighted" (74), "The world's most expensive biohacker just got sick" (65), and
"Huberman is right about magnesium" (54) — are **not** sent from deploy-chat. They come
from the vault system at `03 Marketing/ghl-email-automation/`, which posts to the GHL
conversations API directly. That is 777 of the 4,660 promotional sends.

This sender runs on Glen's Mac and cannot import from the deploy-chat repo, so it needs
its own copy of the signing function. **Drift between the two is the risk**: a signature
minted here must verify at the deploy-chat route. Both test suites pin the same known
vector so a change to either side goes red.

The vault is exempt from worktree isolation. Edit it directly.

- [ ] **Step 1: Write the failing test**

```python
# ~/AI-Training/03 Marketing/ghl-email-automation/tests/test_unsub.py
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import unsub


def setup_module(_):
    unsub._SECRET = "test-secret-abc"


def test_known_vector_matches_the_deploy_chat_side():
    # This exact value is also asserted in deploy-chat's
    # tests/test_unsubscribe_token.py. If either side changes, both go red.
    assert unsub.sign("a@b.com", "global") == unsub.EXPECTED_VECTOR


def test_footer_is_an_anchor_with_the_signed_url():
    out = unsub.footer_html("a@b.com", "content")
    assert '<a href="' in out
    assert "/email/unsubscribe?" in out
    assert unsub.sign("a@b.com", "content") in out


def test_scope_changes_the_signature():
    assert unsub.sign("a@b.com", "content") != unsub.sign("a@b.com", "global")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/AI-Training/"03 Marketing/ghl-email-automation" && python3 -m pytest tests/test_unsub.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'unsub'`

- [ ] **Step 3: Write minimal implementation**

```python
# ~/AI-Training/03 Marketing/ghl-email-automation/unsub.py
"""Unsubscribe footer for vault-sent content email.

Deliberate duplicate of deploy-chat's dashboard/unsubscribe.py: this sender runs
on Glen's Mac and cannot import from that repo. The signing MUST stay identical
or links minted here will not verify at /email/unsubscribe. Both test suites pin
EXPECTED_VECTOR to catch drift.
"""
import hashlib
import hmac
import html as _html
import os
from urllib.parse import quote

_SECRET = os.environ.get("CONSOLE_SECRET") or os.environ.get("WEBHOOK_SECRET", "")
BASE = os.environ.get("PUBLIC_BASE_URL", "https://illtowell.com").rstrip("/")
POSTAL = "Remedy Match LLC, Hilo, Hawaii"

# sign("a@b.com", "global") under _SECRET == "test-secret-abc".
# Fill this in from the first green run, then never edit it casually.
EXPECTED_VECTOR = "<paste from step 4>"


def _norm(email):
    return (email or "").strip().lower()


def sign(email, scope):
    msg = f"unsub:{scope}:{_norm(email)}".encode()
    return hmac.new(_SECRET.encode(), msg, hashlib.sha256).hexdigest()[:40]


def unsubscribe_url(email, scope):
    e = _norm(email)
    return (f"{BASE}/email/unsubscribe?e={quote(e)}"
            f"&scope={quote(scope)}&s={sign(e, scope)}")


def footer_html(email, scope="content"):
    url = _html.escape(unsubscribe_url(email, scope), quote=True)
    return ('<div style="margin-top:28px;padding-top:14px;border-top:1px solid #ddd;'
            'font-family:Arial,sans-serif;font-size:12px;line-height:1.5;color:#777">'
            "You are receiving this because you signed up at Remedy Match.<br>"
            f'<a href="{url}" style="color:#777">Unsubscribe</a><br>'
            f"{_html.escape(POSTAL)}</div>")
```

- [ ] **Step 4: Fill in the test vector**

Run: `cd ~/AI-Training/"03 Marketing/ghl-email-automation" && CONSOLE_SECRET=test-secret-abc python3 -c "import unsub; unsub._SECRET='test-secret-abc'; print(unsub.sign('a@b.com','global'))"`

Paste the printed value into `EXPECTED_VECTOR`, and add the identical assertion to
deploy-chat's `tests/test_unsubscribe_token.py`:

```python
def test_known_vector_matches_the_vault_sender():
    # Also asserted in 03 Marketing/ghl-email-automation/tests/test_unsub.py.
    assert unsubscribe.sign("a@b.com", "global") == "<the same value>"
```

- [ ] **Step 5: Run both suites to verify they pass**

Run: `cd ~/AI-Training/"03 Marketing/ghl-email-automation" && python3 -m pytest tests/ -v`
Run: `cd /tmp/wt-deploy-chat-06bdc124 && python -m pytest tests/test_unsubscribe_token.py -v`
Expected: both green, with the shared vector asserted on each side

- [ ] **Step 6: Append the footer at both send sites**

In `ghl_send.py`, `cmd_send` loop (~line 228) and `cmd_test` (~line 205), append the
footer to the rendered HTML before sending:

```python
        html = md_to_html(personalize(body_md, c))
        html = html + unsub.footer_html(c.get("email") or "", "content")
```

Add `import unsub` at the top. In `cmd_test` the recipient is `contact`, not `c`; use
`contact.get("email")` there.

- [ ] **Step 7: Verify with the sender's own dry run**

Run: `cd ~/AI-Training/"03 Marketing/ghl-email-automation" && doppler run -p remedy-match -c prd -- python3 ghl_send.py send --file campaigns/slot-mon/general.md --dry-run`

Expected: `DRY RUN — nothing sent.` The dry-run path prints recipients but not the body,
so also assert the footer in a test rather than relying on this output. This step is
here to confirm nothing sends.

---

### Task 8: Consolidate the From address

**Files:**
- Modify: `~/AI-Training/03 Marketing/ghl-email-automation/ghl_send.py:52` (`DEFAULT_FROM`)
- Modify: 16 campaign files under `~/AI-Training/03 Marketing/ghl-email-automation/campaigns/*/*.md` (the `from:` frontmatter line)
- Modify: `/tmp/wt-deploy-chat-06bdc124/scripts/weekly_live_invitation.py` (`FROM_ADDRESS`)
- Test: `~/AI-Training/03 Marketing/ghl-email-automation/tests/test_from_domain_guard.py`

**Interfaces:**
- Consumes: nothing.
- Produces: a domain guard in `ghl_send.send_email` that refuses an unverified From.

Three From identities are in use: `drglen@remedymatch.com` (the 5 vault campaigns),
`info@mail.remedymatch.com` (the weekly invitations), and `drglen@mail.remedymatch.com`
(the app). Only `mail.remedymatch.com` is a verified Mailgun sending domain — it carries
the `mailo` DKIM key and its SPF authorizes Mailgun. The bare `remedymatch.com`
authorizes GrooveTech instead and has no DKIM key of its own; it authenticates today only
because a subdomain signature aligns with the parent under relaxed DMARC.

Target for all promotional mail: `Dr. Glen Swartwout <drglen@mail.remedymatch.com>`.

- [ ] **Step 1: Write the failing test**

```python
# ~/AI-Training/03 Marketing/ghl-email-automation/tests/test_from_domain_guard.py
import os, sys, glob, re
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pytest
import ghl_send

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_default_from_is_on_the_verified_sending_domain():
    assert "@mail.remedymatch.com>" in ghl_send.DEFAULT_FROM


def test_no_campaign_declares_an_unverified_from():
    bad = []
    for path in glob.glob(os.path.join(HERE, "campaigns", "*", "*.md")):
        for line in open(path):
            if line.startswith("from:") and "@mail.remedymatch.com>" not in line:
                bad.append(os.path.relpath(path, HERE))
    assert bad == [], f"unverified From in: {bad}"


def test_send_email_refuses_an_unverified_from():
    with pytest.raises(ValueError, match="unverified sending domain"):
        ghl_send.send_email("cid", "Subj", "<p>x</p>",
                            "Dr. Glen <drglen@remedymatch.com>")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/AI-Training/"03 Marketing/ghl-email-automation" && python3 -m pytest tests/test_from_domain_guard.py -v`
Expected: all three FAIL

- [ ] **Step 3: Update DEFAULT_FROM and add the guard**

In `ghl_send.py`, change line 52 and add the guard at the top of `send_email`:

```python
DEFAULT_FROM = "Dr. Glen Swartwout <drglen@mail.remedymatch.com>"
SENDING_DOMAIN = "mail.remedymatch.com"


def send_email(contact_id, subject, html, email_from):
    # Refuse at the writer, not the reader. A From on an unverified domain
    # authenticates only by relaxed DMARC alignment and breaks the moment
    # alignment is tightened. Fail loudly rather than send unauthenticated.
    if f"@{SENDING_DOMAIN}>" not in email_from and not email_from.endswith(f"@{SENDING_DOMAIN}"):
        raise ValueError(
            f"refusing to send from an unverified sending domain: {email_from!r}. "
            f"Use an address at {SENDING_DOMAIN}.")
    body = {
        "type": "Email",
        ...
```

- [ ] **Step 4: Rewrite the 16 campaign From lines**

```bash
cd ~/AI-Training/"03 Marketing/ghl-email-automation"
grep -rl "^from: Dr. Glen Swartwout <drglen@remedymatch.com>" campaigns/ \
  | xargs sed -i '' 's|^from: Dr. Glen Swartwout <drglen@remedymatch.com>|from: Dr. Glen Swartwout <drglen@mail.remedymatch.com>|'
grep -rn "^from:" campaigns/ | grep -v "@mail.remedymatch.com" || echo "all campaign From lines updated"
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd ~/AI-Training/"03 Marketing/ghl-email-automation" && python3 -m pytest tests/ -v`
Expected: all passed

- [ ] **Step 6: Mutation-test the guard**

Change the guard condition to `if False:`. Run the tests. Expected:
`test_send_email_refuses_an_unverified_from` goes RED. Restore, re-run, expect green.

- [ ] **Step 7: Update the weekly invitation's From**

In `/tmp/wt-deploy-chat-06bdc124/scripts/weekly_live_invitation.py`, change `FROM_ADDRESS`
from the `info@mail.remedymatch.com` identity to
`Dr. Glen Swartwout <drglen@mail.remedymatch.com>`. Read the constant first; do not
assume its current spelling.

- [ ] **Step 8: Commit both repos**

```bash
cd ~/AI-Training && git add "03 Marketing/ghl-email-automation" && git commit -m "Consolidate promotional From on the verified sending domain

drglen@remedymatch.com authorizes GrooveTech, not Mailgun, and has no DKIM
key; it authenticated only via relaxed DMARC alignment. Guard refuses an
unverified From at the writer."

cd /tmp/wt-deploy-chat-06bdc124
git add scripts/weekly_live_invitation.py
git commit -m "Weekly invitation sends from the consolidated From identity"
```

---

## Verification before merge

- [ ] `ci/run-tests.sh` green against the ratchet, failure identities compared with `origin/main`.
- [ ] `grep -rn "unsubscribe_scope" --include=*.py .` shows the parameter reaching only promotional callers.
- [ ] The five transactional subjects have **not** gained a footer: "Your new biofield scan is ready to analyze", the three Healing Oasis readiness notices, and "Correction: your private MyHealingOasis link".
- [ ] The shared signing vector is asserted in both repos and both suites are green.
- [ ] `grep -rn "remedymatch.com" ~/AI-Training/"03 Marketing/ghl-email-automation"/campaigns/ | grep -v mail.remedymatch` returns nothing.
- [ ] One end-to-end check on a real address: send yourself a `weekly-live` message, click the footer link, confirm the GET page appears **without** opting you out, then POST and confirm `is_suppressed` returns True. Delete the row afterwards so you keep receiving mail.
- [ ] The vault cadence has been skipping since 2026-08-19 (`content not refreshed within 8d`). That is a separate open issue, not fixed here; do not mistake a silent slot for a working one.
