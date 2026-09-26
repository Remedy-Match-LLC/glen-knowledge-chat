# Portal Staff Guard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Staff inside a client's portal must confirm every client action first, see a banner, never trigger background writes as the client, and never receive the client's sign-in cookie.

**Architecture:** The portal page tags every request with `X-Portal-View: 1`. A `before_request` hook refuses tagged non-GET staff requests with `409 staff_confirm` unless they carry `X-Staff-Confirmed: 1`. It exempts fold saves and answers background writes with 204. The page's fetch wrapper turns a 409 into an in-page dialog, then re-sends. GET routes with side effects skip their writes for staff.

**Tech Stack:** Flask (`app.py`), a pure helper module, plain JS in `static/client-portal.html`, node and Playwright tests.

**Spec:** `docs/superpowers/specs/2026-09-26-portal-staff-guard-design.md`

## Global Constraints

- Header names: `X-Portal-View: 1` (the page tags), `X-Staff-Confirmed: 1` (confirmed re-send).
- The 409 body is `{"staff_confirm": {"action": <text>, "client": <display name>}}`.
- Exempt: `PUT /api/portal/<token>/folds`.
- Background, answered 204 for staff: `POST .../cards/<k>/open`, `.../process-request`, `/api/intake/save-draft`, `.../recommendation-section`, `.../eye-vision-state`, `.../scene-pref`.
- Staff means any valid console credential, VA included: master key, any role's access token, or the console login cookie.
- The dialog is in-page. Never call `window.confirm`, `alert` or `prompt`.
- No em dashes and no ALL CAPS in client-visible or staff-visible text.
- Never run the bare full pytest suite; it sends real email. Run named files with the CI fakes exported.
- Run every `tests/*.js` under node before pushing any `static/` change. Grep `tests/` for any markup string you change.
- Work in `~/worktrees/wt-deploy-chat-58bbe53b`, on branch `sess/58bbe53b-staff-guard`. Stage named files. Never use `git stash`.

## Review Focus

1. A client's own request must never hit the guard. A leftover console cookie in a client's browser is the risk. Pinned in Task 2: a request with no valid credential passes, even with `X-Portal-View`.
2. A staff member working in the console must never be stopped. Pinned in Task 2: no `X-Portal-View` means no guard.
3. A write that is not `fetch`, such as a `<form>` post or `sendBeacon`, would skip the wrapper. Pinned in Task 4: a coverage test fails on any such call in portal scripts.
4. A confirmed re-send must happen once. A double click must not book twice. Pinned in Task 4: while a dialog is open, a second 409 queues behind it and does not open a second dialog.
5. The guard fails closed. If the dialog cannot render, the action does not happen. Pinned in Task 4.

---

### Task 1: Pure classifier and descriptions

**Files:**
- Create: `dashboard/staff_guard.py`
- Test: `tests/test_staff_guard_classify.py`

**Interfaces:**
- Produces: `classify(method, path) -> "pass" | "exempt" | "background" | "guard"`, `describe(path, client) -> str`, constants `EXEMPT`, `BACKGROUND`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_staff_guard_classify.py
import pytest

from dashboard import staff_guard as sg


@pytest.mark.parametrize("method,path,want", [
    ("GET", "/api/portal/T/view", "pass"),
    ("PUT", "/api/portal/T/folds", "exempt"),
    ("POST", "/api/portal/T/cards/abc/open", "background"),
    ("POST", "/api/portal/T/process-request", "background"),
    ("POST", "/api/intake/save-draft", "background"),
    ("POST", "/api/portal/T/recommendation-section", "background"),
    ("POST", "/api/portal/T/eye-vision-state", "background"),
    ("POST", "/api/portal/T/scene-pref", "background"),
    ("POST", "/api/onboarding/book", "guard"),
    ("POST", "/api/portal/T/chat", "guard"),
    ("DELETE", "/api/portal/T/remedies/x", "guard"),
    ("PATCH", "/api/anything", "guard"),
])
def test_classify(method, path, want):
    assert sg.classify(method, path) == want


@pytest.mark.parametrize("path,needle", [
    ("/api/onboarding/book", "books a real appointment and emails Peach and Rae"),
    ("/api/consult/book", "books a real appointment and emails Peach and Rae"),
    ("/calendar/register", "registers Peach for a live session with Zoom"),
    ("/api/portal/T/checkout", "starts a payment as Peach"),
    ("/api/portal/T/family-plan/cancel", "cancels Peach's subscription or plan"),
    ("/api/portal/T/chat", "sends a chat message as Peach"),
    ("/api/intake/submit", "submits Peach's intake form"),
    ("/api/portal/T/cart/set-qty", "changes Peach's cart or invoice"),
    ("/api/portal/T/share-consent", "changes Peach's consent or preferences"),
    ("/api/portal/T/photo", "uploads a file to Peach's record"),
    ("/api/coach-thread/member/message", "sends a message or request as Peach to another member"),
    ("/api/portal/T/something-new", "This changes Peach's account."),
])
def test_describe(path, needle):
    assert needle in sg.describe(path, "Peach")


def test_describe_without_a_name():
    assert sg.describe("/api/portal/T/chat", "") == "This sends a chat message as the client."
```

- [ ] **Step 2: Run to verify it fails**

Run: `python3 -m pytest tests/test_staff_guard_classify.py -q`
Expected: FAIL, ImportError.

- [ ] **Step 3: Implement**

```python
# dashboard/staff_guard.py
"""Which portal requests a staff member must confirm, and how to describe them.

Spec: docs/superpowers/specs/2026-09-26-portal-staff-guard-design.md. Pure, so it is
testable without app.py. The hook in app.py decides WHO is staff; this decides WHAT.
"""
import re

_WRITE = {"POST", "PUT", "PATCH", "DELETE"}
EXEMPT = (re.compile(r"^/api/portal/[^/]+/folds$"),)            # PUT: the viewer's own folds
BACKGROUND = tuple(re.compile(p) for p in (
    r"^/api/portal/[^/]+/cards/[^/]+/open$",
    r"^/api/portal/[^/]+/process-request$",
    r"^/api/intake/save-draft$",
    r"^/api/portal/[^/]+/recommendation-section$",
    r"^/api/portal/[^/]+/eye-vision-state$",
    r"^/api/portal/[^/]+/scene-pref$",
))
_DESCRIPTIONS = [
    (r"^/api/(onboarding|consult)/book$", "books a real appointment and emails {c} and Rae"),
    (r"appointment-proposals", "sends an appointment request or reply to the team"),
    (r"^/calendar/register$", "registers {c} for a live session with Zoom"),
    (r"/cancel$", "cancels {cp} subscription or plan"),
    (r"(checkout|/subscribe|caregiver-pay|coach-subscribe)", "starts a payment as {c}"),
    (r"/chat$", "sends a chat message as {c}"),
    (r"^/api/intake/submit$", "submits {cp} intake form"),
    (r"(add-to-invoice|order-add|/cart|set-qty|set-format)", "changes {cp} cart or invoice"),
    (r"(consent|agree-tos|sharing|notify-pref|scan-prefs|cc-pref)", "changes {cp} consent or preferences"),
    (r"(photo|documents|five-voice)", "uploads a file to {cp} record"),
    (r"(coach-thread|/api/peer|coach-request)", "sends a message or request as {c} to another member"),
]


def classify(method, path):
    method = (method or "").upper()
    if method not in _WRITE:
        return "pass"
    if method == "PUT" and any(p.search(path) for p in EXEMPT):
        return "exempt"
    if method == "POST" and any(p.search(path) for p in BACKGROUND):
        return "background"
    return "guard"


def describe(path, client):
    c = (client or "").strip() or "the client"
    cp = (c + "'s") if client else "the client's"
    for pattern, text in _DESCRIPTIONS:
        if re.search(pattern, path or ""):
            return "This " + text.format(c=c, cp=cp) + "."
    return f"This changes {cp} account."
```

The `/cancel` pattern sits before the payment pattern on purpose. `family-plan/cancel` must not read as a payment.

- [ ] **Step 4: Run to verify it passes**

Run: `python3 -m pytest tests/test_staff_guard_classify.py -q`
Expected: all pass. If a `describe` case fails on article or possessive wording, fix the templates, not the test.

- [ ] **Step 5: Commit**

```bash
git add dashboard/staff_guard.py tests/test_staff_guard_classify.py
git commit -m "Staff guard: classify portal writes and describe them"
```

---

### Task 2: The server hook

**Files:**
- Modify: `app.py`. Add `_is_staff_request()` directly after `_cookie_console_key()`, and a new `@app.before_request` function directly after `_console_browser_login`, so the console bridge has run first.
- Test: `tests/test_staff_guard_hook.py`

**Interfaces:**
- Consumes: `staff_guard.classify/describe`, `_present_console_key`, `_role_for_token`, `CONSOLE_SECRET`, `dashboard.CONSOLE_SECRET`.
- Produces: `_is_staff_request() -> bool`, `_staff_guard_client_name(path) -> str`, hook `_portal_staff_guard`.

`_is_staff_request()`: `k = _present_console_key()`. It returns true when `k` equals `CONSOLE_SECRET` or `dashboard.CONSOLE_SECRET` (each only when set), or when `_role_for_token(k)` is not None. `_cookie_console_key` only recognises owner cookies, so also treat a valid VA login cookie as staff if one exists. Check `_console_cookie_valid` and the VA login path. If VA browsers have no cookie login, the header path is enough, so record that as a ruling.

Hook: return None unless `request.headers.get("X-Portal-View") == "1"` and `_is_staff_request()`. Then run `classify(request.method, request.path)`:
- `pass` or `exempt`: return None.
- `background`: return `("", 204)`.
- `guard`: return None if `X-Staff-Confirmed == "1"`. Otherwise return `409` with the spec's body.

`_staff_guard_client_name(path)`: when the path has `/api/portal/<token>/`, resolve the portal record with `_portal_record_for` and return its `name`, else its email local part. Otherwise read `?token=` through `_evox_ident`. Return "" on any failure. It must never raise.

- [ ] **Step 1: Write the failing tests**

Use the `client` fixture from `tests/test_client_portal_routes.py` and its `_seed_portal`, and `_seed_user` from `tests/test_portal_folds_routes.py` (copy them in). Patch `app._onboarding_send_confirmations`, `app.send_email`, `app.send_evox_email`, `app._zoom_register_for_series`, and the Stripe session creator (find it with `grep -n "stripe.checkout.Session.create" app.py`). Each fake appends to a `sent` list. Tests:

1. `POST /api/onboarding/book` with a staff key plus `X-Portal-View: 1`, no confirmation: status 409, `staff_confirm.action` contains "books a real appointment", `sent == []`, and no `evox_bookings` row.
2. The same request with `X-Staff-Confirmed: 1` passes the guard: status is not 409. The route's own checks may still refuse, and that's fine.
3. The same request with no console credential and `X-Portal-View: 1`: status is not 409.
4. A staff key without `X-Portal-View`: status is not 409.
5. `PUT /api/portal/<t>/folds` from staff with `X-Portal-View`: not 409.
6. `POST /api/portal/<t>/process-request` from staff with `X-Portal-View`: 204. Patch `process_queue.enqueue` and assert it was not called.
7. A VA token (`workspace:shaira`) with `X-Portal-View` on `POST /api/portal/<t>/chat`: 409.
8. The 409 names the client: seed the portal named "Brooke Webb" and assert `client == "Brooke Webb"`.
9. A garbage `X-Console-Key` with `X-Portal-View`: not 409. A garbage key is not staff.

- [ ] **Step 2: Run to verify they fail**

Run, with the CI fakes exported: `python3 -m pytest tests/test_staff_guard_hook.py -q`
Expected: tests 1, 6, 7 and 8 fail. The rest pass already, because they assert that nothing changed.

- [ ] **Step 3: Implement** as described above.

- [ ] **Step 4: Run to verify they pass**, plus `tests/test_console_cookie_login.py` and `tests/test_client_portal_routes.py`. Compare failing sets with origin/main.

- [ ] **Step 5: Mutate**

Break each rule in turn and watch its test fail, then restore:
1. Drop the `X-Portal-View` check.
2. Treat any non-empty key as staff.
3. Classify background as guard.

- [ ] **Step 6: Commit**

```bash
git add app.py tests/test_staff_guard_hook.py
git commit -m "Staff guard: refuse unconfirmed staff writes from the portal view"
```

---

### Task 3: GET side effects, the cookie, and the banner data

**Files:**
- Modify: `app.py`. The `mark_engaged` call in `api_client_portal`; `mark_read` and `get_or_create_thread` in `GET /api/coach-thread/member`; `mark_read` in `GET /api/peer-thread/<id>`; `set_optin(..., False)` in `GET /api/peer/state`; the session bridge in `client_portal_page`; `api_client_portal`'s JSON gains `staff_view`.
- Test: `tests/test_staff_guard_side_effects.py`

Each write is wrapped in `if not _is_staff_request():`. `get_or_create_thread` for staff reads an existing thread. If there is none, it returns an empty thread without creating one. Record that as a ruling if the route's shape makes it awkward.

`staff_view`: `{"client": <portal name or email local part>}` when `_is_staff_request()`, else absent.

- [ ] **Step 1: Write the failing tests**
1. `GET /api/portal/<t>` with a staff key: `notify_state` `engaged` stays 0, and `staff_view.client` equals the seeded name. With no key: `engaged` becomes 1, and there's no `staff_view`.
2. `GET /portal/<t>` with a staff key: no `Set-Cookie: rm_portal_session`. With no key, and client login enabled through monkeypatching `_client_login_enabled` to True: the cookie is set.
3. The coach-thread and peer-thread GETs with a staff key: patch `coach_threads.mark_read` and assert it was not called. With no key it is called. Seed the rows those routes need, following the existing tests (`grep -ln "coach-thread" tests/`).
4. `GET /api/peer/state` with a staff key: `set_optin` not called.

- [ ] **Step 2: Run, verify fail. Step 3: Implement. Step 4: Run, verify pass. Step 5: Mutate each guard. Step 6: Commit**

```bash
git add app.py tests/test_staff_guard_side_effects.py
git commit -m "Staff guard: no background writes, no client cookie, banner data for staff"
```

---

### Task 4: The page: tag, dialog, re-send, banner

**Files:**
- Modify: `static/client-portal.html`. Put a new inline `<script>` before the first existing `<script>` (search `<script>` near the top of the body) that wraps `window.fetch`. Add the dialog and banner functions and their CSS.
- Test: `tests/test_staff_guard_page.py`, a node harness like `tests/test_portal_folds_glue.py`, plus the coverage test.

Behaviour:
- The wrapper adds `X-Portal-View: 1` to every request it sends. It uses `new Headers(init.headers)`, so existing headers survive.
- A 409 whose JSON has `staff_confirm`: the wrapper calls `_staffConfirm(info)`, which returns a Promise of true or false. True re-sends once, with `X-Staff-Confirmed: 1`, and resolves to the re-send's response. False resolves to the original 409 response, rebuilt from its body text so the caller can still read it.
- `_staffConfirm` shows one `div.staff-confirm` dialog: the heading "Do this as {client}?", the `action` text, and the buttons "Do it as {client}" and "Cancel". Focus goes to Cancel. Escape cancels.
- While a dialog is open, a second 409 waits its turn in a queue rather than opening a second dialog (Review Focus 4).
- If building the dialog throws, `_staffConfirm` resolves false (Review Focus 5).
- Banner: when the portal data carries `staff_view`, render one fixed `div.staff-banner` reading "You are viewing {client}'s portal as staff. Anything you do here asks first." It has no close button. Add it in `render()` beside `wirePortalFolds()`, and create it only once.

- [ ] **Step 1: Write the failing tests** (node harness with fake fetch and a fake DOM)
1. Every request carries `X-Portal-View: 1`, and existing headers such as `Content-Type` survive.
2. A 409 `staff_confirm`: the dialog shows the heading and the action text. "Do it" re-sends exactly once, with `X-Staff-Confirmed: 1`, and the caller gets the second response.
3. "Cancel": no re-send, and the caller gets a response with status 409.
4. Two 409s at once: only one dialog shows. The second opens after the first closes.
5. The dialog code throws: the caller gets the 409, and nothing is re-sent.
6. A 409 without `staff_confirm`, such as `slot_taken`, passes straight through with no dialog.
7. Static checks: the page never calls `window.confirm(`, `alert(` or `prompt(`. The wrapper script comes before every other `<script` in the page.

Coverage test (Review Focus 3): in `static/client-portal.html` and `static/js/portal-*.js`, `static/portal-mentor.js`, `static/tts-output.js` and `static/entity-ref.js`, find no `XMLHttpRequest`, no `sendBeacon(`, and no `<form` with `method="post"`. Strip comments first. If any exists, the test lists it. Then route it through `fetch` in this task, or record a ruling with the reason.

- [ ] **Step 2: Run, verify fail. Step 3: Implement. Step 4: Run, verify pass.**
- [ ] **Step 5: Run every node test and grep `tests/` for any markup you changed.** Regenerate the setting-off snapshot only if the diff is limited to this change, and check the diff line by line.
- [ ] **Step 6: Commit**

```bash
git add static/client-portal.html tests/test_staff_guard_page.py
git commit -m "Staff guard: portal tags requests, asks before a staff write, shows a banner"
```

---

### Task 5: Browser check

**Files:**
- Create: `tests/test_staff_guard_e2e.py`, using the live-server fixture from `tests/test_portal_folds_e2e.py`.

1. A staff context (`extra_http_headers={"X-Console-Key": SECRET}`) opens the portal. The banner is visible and names the client.
2. In that context, call a guarded action through the page's own code, such as `fetch('/api/portal/<t>/chat', {method:'POST', ...})`. The dialog appears. Cancel: the server saw no second request, and nothing was written. Assert chat rows are unchanged.
3. Repeat and choose "Do it". The server receives the request once, with `X-Staff-Confirmed`.
4. A client context, with no key, sends the same request. No dialog appears.

- [ ] Write, run, and mutate the dialog trigger. The test must fail. Then commit.

---

### Task 6: Review, merge, deploy, verify

- [ ] Run all named test files from Tasks 1 to 5, every node test, and the neighbouring route files. Compare failing sets with origin/main.
- [ ] Run three blind review rounds, each with fresh reviewers asking one question:
  1. Can a client's own request ever be blocked or asked?
  2. Can any staff write in a portal slip through unasked?
  3. What does staff see when something fails?
- [ ] Push, open the PR, and ask Glen to merge in the platform tab.
- [ ] After merge, deploy and verify live with a read-only check: a staff POST with `X-Portal-View` and no confirmation to a harmless guarded route gets 409. Use a probe route whose route code would refuse anyway, such as chat with an empty body, so nothing runs even if the guard failed. Check that the served page contains the wrapper.
- [ ] Update `platform/STATE.md` and `SESSION_LOG.md`.
