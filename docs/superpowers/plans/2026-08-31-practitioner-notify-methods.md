# Practitioner Notification Preferences Implementation Plan (Section 3a, part 2)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let each practitioner choose any or all of four ways to hear that someone booked her — phone, text, email, calendar — instead of the single hardcoded email she gets today.

**Architecture:** One `notify_methods` list on the existing booking config, validated like every other field. The booking route already has a `try/except` block that notifies her by email; that block becomes a fan-out over her chosen methods. "Phone" is the odd one: it is not an outbound channel at all, it means her number is shown to the client so they can reach her.

**Tech Stack:** Python 3, Flask, sqlite, GoHighLevel's `/conversations/messages` API for SMS. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-08-27-practitioner-website-design.md`, Section 3.

**Branch:** `sess/a191e6ab-s3`, head `89333cf3`. This continues the booking work already on that branch and merges with it.

## Why this exists

Glen, 2026-08-31: *"Allow each practitioner to select any or all preferred methods to receive appointment requests: phone, text, email, calendar."*

It also closes a gap the whole-branch review found and this plan is the fix for: **the confirmation email names no practitioner and carries no phone number, so a `phone`-medium booking currently leaves neither party able to call the other.** A booking whose whole medium is a phone call, with no phone number anywhere, is not a booking.

## Global Constraints

- **Opt-in, never defaulted.** A practitioner who has not chosen gets the current behaviour: email only. Publishing a phone number nobody asked to publish is the same defect as the `accepting_clients` default that nearly shipped on this project — a value the system chose, presented as the person's own.
- **A notification failure must never fail the booking.** `create_booking` commits before any of this runs. The existing block already logs and continues; every new channel goes inside the same guard.
- **A channel that cannot send must degrade, not raise.** No GHL contact, no phone number, SMS not configured: log and carry on to the next method.
- **Validation messages are shown to the practitioner.** Say what to do, not which field failed.
- **Mutation-test every guard:** plant the violation, confirm the test goes red, restore.
- Assert on raw strings and JSON, never a parsed DOM.

## Context the implementer needs

**The config store** is `dashboard/practitioner_booking.py`. `validate_config(cfg)` returns a cleaned dict or raises `BookingConfigError`; `get_config` re-validates on read and returns `None` if anything no longer passes, so a new field must be handled on **both** paths or an old row stops loading. `set_config` upserts. Read `_validate_session_types` for the house style: a list of allowed values, an explicit membership check, a readable message.

**The notification block** already exists in `api_public_book` in `app.py`, inside a `try/except Exception` that logs and continues. Find it with `grep -n "New booking:" app.py`. It builds `subj`, `html_body2`, `text_body2` and calls:

```python
send_evox_email(practitioner_email, "", subj, html_body2, text_body2, b"")
```

That last argument is `ics_bytes` — currently empty. The client's own confirmation two blocks above builds a real one with `_ev.build_ics(..., tz_name=cfg["timezone"])`. **The "calendar" method is mostly wiring an ICS that is already being built.**

**Her email** comes from `dashboard.practitioner_portal.practitioner_email_by_id(pid)`, which returns `""` on any failure. The caller guards with `if practitioner_email:`.

**GHL is the SMS path.** `dashboard/ghl_email.py` has:

```python
def is_configured() -> bool
def _upsert_contact(email: str, name: str = "") -> str   # returns a contactId
def send_via_ghl(to_email, subject, *, html=None, ...)
```

`send_via_ghl` posts to `/conversations/messages` with `{"type": "Email", "contactId": ..., "subject": ...}`. The same endpoint accepts `"type": "SMS"` with a `message` field and no subject. **GHL addresses by `contactId`, not by phone number** — the contact must exist and must carry a phone, or the send fails. `_upsert_contact` takes email and name only, so it does not currently set one.

**Her phone** lives in `practitioners.phone` in Postgres. 24,575 rows across the whole table have one. There is no getter for it beside `practitioner_email_by_id`; you will add one.

**Do not change** `create_booking`, `build_ics`, `send_evox_email` or `send_via_ghl`'s existing signature. Glen's and Rae's live flows use all four.

**Environment:** `doppler run -p remedy-match -c dev -- python3 -m pytest <files> -q -p no:randomly`. **Never run the full suite — it sends real email.** Known pre-existing failures, not yours: `tests/test_practitioner_personal_order.py::test_personal_card_return_books_one_sales_receipt`, `tests/test_membership.py::test_studio_credit_post_inserts_intent_sends_glen_notification`.

---

## File Structure

| File | Responsibility |
|---|---|
| `dashboard/practitioner_booking.py` | `NOTIFY_METHODS`, validation of `notify_methods` on write and re-validation on read. |
| `dashboard/practitioner_portal.py` | `practitioner_phone_by_id(pid)`, beside the existing email getter. |
| `dashboard/ghl_email.py` | `send_sms_via_ghl(to_email, message, *, phone="")` — a new function, not a changed one. |
| `app.py` | The notification block becomes a fan-out; the client confirmation gains her number when she chose `phone`. |
| `static/practitioner-booking.html` | Four checkboxes and a phone field. |
| `dashboard/public_surface.py`, `dashboard/practitioner_render.py` | `practitioner_phone` as a public field, shown only on opt-in. |
| `tests/test_practitioner_booking.py`, `tests/test_practitioner_booking_routes.py` | Validation, fan-out, and the opt-in gate. |

---

## Task 1: `notify_methods` on the config

**Files:**
- Modify: `dashboard/practitioner_booking.py`
- Test: `tests/test_practitioner_booking.py`

**Interfaces:**
- Consumes: `validate_config`, `get_config`, `set_config`, `BookingConfigError` (existing).
- Produces: `NOTIFY_METHODS = ("phone", "text", "email", "calendar")`, and `notify_methods` as a validated list on the config dict. Tasks 2-4 read `cfg["notify_methods"]`.

- [ ] **Step 1: Write the failing test**

```python
def test_notify_methods_round_trip(cx):
    pb.set_config(cx, PID, _cfg(notify_methods=["email", "text"]))
    assert pb.get_config(cx, PID)["notify_methods"] == ["email", "text"]


def test_notify_methods_defaults_to_email_only():
    """A practitioner who never touches this keeps exactly today's behaviour.
    It must NOT default to every method: publishing her phone number because
    she left a box alone is the system choosing on her behalf."""
    out = pb.validate_config(_cfg())
    assert out["notify_methods"] == ["email"]


def test_every_method_may_be_chosen():
    out = pb.validate_config(_cfg(notify_methods=["phone", "text", "email", "calendar"]))
    assert set(out["notify_methods"]) == {"phone", "text", "email", "calendar"}


def test_an_unknown_method_is_rejected():
    with pytest.raises(pb.BookingConfigError):
        pb.validate_config(_cfg(notify_methods=["carrier-pigeon"]))


def test_duplicates_are_collapsed_not_rejected():
    """A checkbox UI can submit the same value twice. That is not the
    practitioner's mistake and should not be an error she has to fix."""
    out = pb.validate_config(_cfg(notify_methods=["email", "email", "text"]))
    assert out["notify_methods"] == ["email", "text"]


def test_an_empty_list_is_rejected():
    """Choosing nothing means she never hears about a booking at all. That is
    almost certainly a mis-click, and the cost of guessing wrong is she misses
    an appointment."""
    with pytest.raises(pb.BookingConfigError) as e:
        pb.validate_config(_cfg(notify_methods=[]))
    assert "at least one" in str(e.value).lower()


def test_a_string_instead_of_a_list_is_rejected():
    with pytest.raises(pb.BookingConfigError):
        pb.validate_config(_cfg(notify_methods="email"))


def test_a_stored_row_with_bad_notify_methods_fails_closed(cx):
    """get_config re-validates on read. A row whose methods no longer parse
    must return None like every other unreadable field, not a half-config."""
    pb.set_config(cx, PID, _cfg())
    cx.execute("UPDATE practitioner_booking_config SET notify_methods=? "
               "WHERE practitioner_id=?", ("{not json", PID))
    cx.commit()
    assert pb.get_config(cx, PID) is None
    assert pb.is_bookable(cx, PID) is False
```

- [ ] **Step 2: Run to verify it fails**

Run: `doppler run -p remedy-match -c dev -- python3 -m pytest tests/test_practitioner_booking.py -q -p no:randomly`

Expected: FAIL — `notify_methods` is not in the returned config.

- [ ] **Step 3: Implement**

Add to `dashboard/practitioner_booking.py`:

```python
# The four ways a practitioner can hear that someone booked her. "phone" is
# not an outbound channel: it means her number is shown to the client so they
# can reach her. The other three are things we send.
NOTIFY_METHODS = ("phone", "text", "email", "calendar")
DEFAULT_NOTIFY_METHODS = ["email"]


def _validate_notify_methods(value):
    """Clean the chosen notification methods, or raise.

    Defaults to email only when absent. It must NOT default to all four:
    publishing a practitioner's phone number because she left a checkbox alone
    is the system making a claim on her behalf, which is the same defect class
    as an availability flag nobody set.
    """
    if value is None:
        return list(DEFAULT_NOTIFY_METHODS)
    if not isinstance(value, list):
        raise BookingConfigError(
            "Choose how you would like to hear about a booking.")
    seen, out = set(), []
    for m in value:
        m = str(m or "").strip().lower()
        if m not in NOTIFY_METHODS:
            raise BookingConfigError(
                f"'{m}' is not one of: {', '.join(NOTIFY_METHODS)}.")
        if m not in seen:
            seen.add(m)
            out.append(m)
    if not out:
        raise BookingConfigError(
            "Pick at least one way to hear about a booking, or you will not "
            "find out someone has taken a time.")
    return out
```

In `validate_config`, add to the returned dict:

```python
        "notify_methods": _validate_notify_methods(cfg.get("notify_methods")),
```

In `init_tables`, add the column additively so an existing row keeps working:

```python
    for _col, _decl in (("notify_methods", "TEXT"),):
        try:
            cx.execute(f"ALTER TABLE practitioner_booking_config "
                       f"ADD COLUMN {_col} {_decl}")
        except Exception:
            pass
```

In `set_config`, store `json.dumps(clean["notify_methods"])`. In `get_config`, parse it inside the **same** `try` that already guards `session_types`, and run it through `_validate_notify_methods` so a corrupt value fails closed exactly like the other fields. Treat a `NULL` column — a row written before this change — as the default rather than a failure.

- [ ] **Step 4: Run to verify it passes**

Expected: PASS.

- [ ] **Step 5: Mutation-test both guards**

1. Change the default to `list(NOTIFY_METHODS)`. Confirm `test_notify_methods_defaults_to_email_only` goes **red**. Restore.
2. Remove the `_validate_notify_methods` call from `get_config`'s read path. Confirm `test_a_stored_row_with_bad_notify_methods_fails_closed` goes **red**. Restore.

- [ ] **Step 6: Commit**

```bash
git add dashboard/practitioner_booking.py tests/test_practitioner_booking.py
git commit -m "feat(booking): let a practitioner choose how she hears about a booking"
```

---

## Task 2: Her phone number, and the SMS sender

**Files:**
- Modify: `dashboard/practitioner_portal.py`, `dashboard/ghl_email.py`
- Test: `tests/test_practitioner_booking_routes.py`

**Interfaces:**
- Consumes: `_upsert_contact`, `is_configured`, `_headers`, `_V2` from `ghl_email` (existing).
- Produces:
  - `practitioner_phone_by_id(pid) -> str` — the phone, or `""` on any failure
  - `send_sms_via_ghl(to_email, message, *, phone="") -> dict` — `{"id": ...}` on success, `{"skipped": reason}` when it cannot send

**Neither function may raise.** Both are called from inside a notification block whose whole job is to not break a booking that has already committed.

- [ ] **Step 1: Write the failing test**

```python
def test_practitioner_phone_is_empty_when_unavailable(monkeypatch):
    """Same contract as practitioner_email_by_id: a Supabase failure returns
    "", never an exception, because the caller is mid-notification on a
    booking that is already committed."""
    from dashboard import practitioner_portal as _pp
    import db_supabase

    def boom():
        raise RuntimeError("supabase is down")
    monkeypatch.setattr(db_supabase, "supabase_cursor", boom)
    assert _pp.practitioner_phone_by_id("pid-x") == ""


def test_sms_is_skipped_not_raised_when_ghl_is_unconfigured(monkeypatch):
    from dashboard import ghl_email as _g
    monkeypatch.setattr(_g, "is_configured", lambda: False)
    out = _g.send_sms_via_ghl("her@example.com", "New booking")
    assert out.get("skipped")
    assert "id" not in out


def test_sms_is_skipped_when_the_contact_lookup_fails(monkeypatch):
    from dashboard import ghl_email as _g
    monkeypatch.setattr(_g, "is_configured", lambda: True)

    def boom(email, name="", phone=""):
        raise RuntimeError("no contact")
    monkeypatch.setattr(_g, "_upsert_contact", boom)
    out = _g.send_sms_via_ghl("her@example.com", "New booking")
    assert out.get("skipped")


def test_the_sms_payload_is_type_sms_not_email():
    """The same GHL endpoint sends both. Sending type Email here would deliver
    a subject-less email instead of a text and look like success.

    Tested through a PURE payload builder rather than through
    send_sms_via_ghl, because that function short-circuits under pytest before
    it ever posts -- see the next test. A payload builder with no I/O is the
    only seam where this shape can actually be asserted."""
    from dashboard import ghl_email as _g
    body = _g._sms_payload("c-1", "New booking from A Client")
    assert body["type"] == "SMS"
    assert body["contactId"] == "c-1"
    assert "New booking" in body["message"]
    assert "subject" not in body


def test_send_sms_never_touches_the_live_crm_under_pytest(monkeypatch):
    """_upsert_contact is a LIVE CRM WRITE. Its own comment says a new caller
    must carry the pytest guard itself, so assert ours does -- if this test
    ever goes red, a test run is writing to the real CRM."""
    from dashboard import ghl_email as _g
    monkeypatch.setattr(_g, "is_configured", lambda: True)

    def must_not_run(*a, **kw):
        raise AssertionError("_upsert_contact reached under pytest")
    monkeypatch.setattr(_g, "_upsert_contact", must_not_run)
    out = _g.send_sms_via_ghl("her@example.com", "New booking")
    assert out.get("skipped") == "pytest"
```

- [ ] **Step 2: Run to verify it fails**

Expected: FAIL — neither function exists.

- [ ] **Step 3: Implement**

In `dashboard/practitioner_portal.py`, beside `practitioner_email_by_id`, matching its shape exactly:

```python
def practitioner_phone_by_id(pid) -> str:
    """Her phone, or "" if we cannot get one.

    Never raises: the caller is inside a post-booking notification block, and
    a booking that is already committed must not fail because a lookup did.
    """
    try:
        from db_supabase import supabase_cursor
        with supabase_cursor() as cur:
            cur.execute("SELECT phone FROM practitioners WHERE id=%s", (str(pid),))
            row = cur.fetchone()
        return (row.get("phone") or "").strip() if row else ""
    except Exception:
        return ""
```

In `dashboard/ghl_email.py`, a **new** function beside the email one:

```python
def _sms_payload(contact_id: str, message: str) -> dict:
    """The request body, split out with no I/O so its shape is testable.

    send_sms_via_ghl short-circuits under pytest before it posts anything, so
    this is the only place the SMS-vs-Email type can be asserted.
    """
    return {"type": "SMS", "contactId": contact_id, "message": message}


def send_sms_via_ghl(to_email: str, message: str, *, phone: str = "") -> dict:
    """Send an SMS through GHL, or say why it could not.

    GHL addresses by contactId, not by phone number, so the recipient must
    exist as a contact and that contact must carry a phone. We pass the number
    through on the upsert so a contact created here is textable.

    Returns {"id": ...} on success and {"skipped": reason} otherwise. It never
    raises: every caller is inside a notification block guarding a booking
    that has already committed, and a text that cannot be sent is not a reason
    to tell someone their booking failed.
    """
    if not is_configured():
        return {"skipped": "ghl not configured"}
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return {"skipped": "pytest"}
    try:
        contact_id = _upsert_contact(to_email, phone=phone)
    except Exception as e:  # noqa: BLE001
        return {"skipped": f"contact lookup failed: {e!r}"}
    body = _sms_payload(contact_id, message)
    try:
        r = requests.post(f"{_V2}/conversations/messages",
                          headers=_headers(), json=body, timeout=20)
        if r.status_code >= 300:
            return {"skipped": f"ghl returned {r.status_code}: {r.text[:200]}"}
        return {"id": (r.json() or {}).get("messageId"), "via": "ghl-sms"}
    except Exception as e:  # noqa: BLE001
        return {"skipped": f"send failed: {e!r}"}
```

Widen `_upsert_contact` to accept an optional `phone=""` and include it in its payload when non-empty. Additive, so existing callers are unaffected — but **do not trust its docstring**, which claims "its only caller is send_via_ghl". That is stale: `grep -rn "_upsert_contact" --include='*.py' .` finds the name in `app.py`, `cns_tracking_watcher.py`, `scrapers/practitioner_finder/ghl_sync.py`, `migrate_zyto.py` and `scripts/backfill_beta_cohort.py`. Check whether those are calls to *this* function or same-named locals, and say which in your report.

**Carry its warning forward.** That function is commented `LIVE CRM WRITE ... a new caller must guard itself`, and `send_via_ghl` guards with `if os.environ.get("PYTEST_CURRENT_TEST")`. Your new function is a new caller: the guard above is not optional, and a test asserts it fires.

- [ ] **Step 4: Run to verify it passes**

Expected: PASS.

- [ ] **Step 5: Mutation-test**

Change `"type": "SMS"` back to `"type": "Email"`. Confirm `test_sms_posts_the_sms_type_not_email` goes **red**. Restore.

- [ ] **Step 6: Commit**

```bash
git add dashboard/practitioner_portal.py dashboard/ghl_email.py \
        tests/test_practitioner_booking_routes.py
git commit -m "feat(booking): practitioner phone getter and a GHL SMS sender"
```

---

## Task 3: Fan the notification out across her chosen methods

**Files:**
- Modify: `app.py`
- Test: `tests/test_practitioner_booking_routes.py`

**Interfaces:**
- Consumes: `NOTIFY_METHODS` and `cfg["notify_methods"]` from Task 1; `practitioner_phone_by_id`, `send_sms_via_ghl` from Task 2; `send_evox_email`, `_ev.build_ics` (existing).
- Produces: nothing later depends on this.

**What each method means, and this is the part to get right:**

| Method | What happens |
|---|---|
| `email` | The notification she already gets. Unchanged. |
| `calendar` | The same email, with the ICS attached — the invite is already built for the client two blocks above. |
| `text` | An SMS via GHL: who booked, what, and when in her own zone. |
| `phone` | **Nothing is sent to her.** Her number goes to the client instead — Task 4. |

`phone` being a no-op here is the whole subtlety. A practitioner who picks only `phone` receives nothing, by design, because she has said "the client should call me." Make that explicit in a comment or the next reader will file it as a bug.

- [ ] **Step 1: Write the failing test**

```python
def _spy_sends(monkeypatch):
    sends = {"email": [], "sms": []}
    monkeypatch.setattr(appmod, "send_evox_email",
                        lambda to, name, subj, html, text, ics: sends["email"].append(
                            {"to": to, "subj": subj, "ics": ics}))
    from dashboard import ghl_email as _g
    monkeypatch.setattr(appmod, "_send_sms_via_ghl",
                        lambda to, msg, phone="": sends["sms"].append(
                            {"to": to, "msg": msg, "phone": phone}))
    return sends


def test_email_only_is_the_default_behaviour(public, logdb, monkeypatch):
    sends = _spy_sends(monkeypatch)
    with _open(logdb) as c:
        pb.set_config(c, PID, CFG)          # no notify_methods -> ["email"]
    _book_one(public)
    to_her = [s for s in sends["email"] if s["to"] == PRACTITIONER_EMAIL]
    assert len(to_her) == 1
    assert to_her[0]["ics"] == b"", "no calendar method chosen, so no invite"
    assert sends["sms"] == []


def test_calendar_attaches_the_invite_to_her_notification(public, logdb, monkeypatch):
    sends = _spy_sends(monkeypatch)
    with _open(logdb) as c:
        pb.set_config(c, PID, dict(CFG, notify_methods=["email", "calendar"]))
    _book_one(public)
    to_her = [s for s in sends["email"] if s["to"] == PRACTITIONER_EMAIL]
    assert to_her and to_her[0]["ics"].startswith(b"BEGIN:VCALENDAR")


def test_text_sends_an_sms_with_her_own_timezone(public, logdb, monkeypatch):
    sends = _spy_sends(monkeypatch)
    with _open(logdb) as c:
        pb.set_config(c, PID, dict(CFG, notify_methods=["text"]))
    _book_one(public)
    assert len(sends["sms"]) == 1
    assert "A Client" in sends["sms"][0]["msg"]
    assert not [s for s in sends["email"] if s["to"] == PRACTITIONER_EMAIL], \
        "she chose text only; she should not also get an email"


def test_phone_only_sends_her_nothing(public, logdb, monkeypatch):
    """Deliberate. 'Phone' means the client calls her, so there is nothing to
    send. Her number reaching the client is Task 4's job."""
    sends = _spy_sends(monkeypatch)
    with _open(logdb) as c:
        pb.set_config(c, PID, dict(CFG, notify_methods=["phone"]))
    _book_one(public)
    assert not [s for s in sends["email"] if s["to"] == PRACTITIONER_EMAIL]
    assert sends["sms"] == []


def test_a_failing_sms_does_not_fail_the_booking(public, logdb, monkeypatch):
    def boom(to, msg, phone=""):
        raise RuntimeError("ghl is down")
    monkeypatch.setattr(appmod, "_send_sms_via_ghl", boom)
    with _open(logdb) as c:
        pb.set_config(c, PID, dict(CFG, notify_methods=["text"]))
    r = _book_one(public)
    assert r.status_code == 200
    with _open(logdb) as c:
        assert c.execute("SELECT COUNT(*) c FROM evox_bookings "
                         "WHERE status='booked'").fetchone()["c"] == 1


def test_one_failing_method_does_not_stop_the_others(public, logdb, monkeypatch):
    """She chose text and email. GHL is down. She must still get the email --
    a fan-out that aborts on the first failure is worse than no fan-out,
    because it silently drops the channel that would have worked."""
    sends = _spy_sends(monkeypatch)

    def boom(to, msg, phone=""):
        raise RuntimeError("ghl is down")
    monkeypatch.setattr(appmod, "_send_sms_via_ghl", boom)
    with _open(logdb) as c:
        pb.set_config(c, PID, dict(CFG, notify_methods=["text", "email"]))
    _book_one(public)
    assert [s for s in sends["email"] if s["to"] == PRACTITIONER_EMAIL]
```

Add the two helpers the tests use near the top of the file, beside `_open`:

```python
PRACTITIONER_EMAIL = "her@example.com"


def _book_one(client, slug="mary-boyd"):
    """Book the first offered slot. Returns the POST response."""
    slot = client.get(f"/api/book/{slug}/slots?session=intro").get_json()["slots"][0]
    return client.post(f"/api/book/{slug}", json={
        "session": "intro", "start": slot["start"],
        "name": "A Client", "email": "client@example.com"})
```

and in the `public` fixture, make her address resolvable:

```python
    from dashboard import practitioner_portal as _pp
    monkeypatch.setattr(_pp, "practitioner_email_by_id", lambda pid: PRACTITIONER_EMAIL)
    monkeypatch.setattr(_pp, "practitioner_phone_by_id", lambda pid: "+15550100")
```

- [ ] **Step 2: Run to verify it fails**

Expected: FAIL — every method currently produces the same single email.

- [ ] **Step 3: Implement**

Add a thin indirection near the notification block so the tests can patch one name:

```python
def _send_sms_via_ghl(to_email, message, phone=""):
    """Indirection over ghl_email.send_sms_via_ghl, so the notification
    fan-out has a single patchable seam and app.py does not import GHL at
    module load."""
    from dashboard import ghl_email as _g
    return _g.send_sms_via_ghl(to_email, message, phone=phone)
```

Replace the body of the existing notification `try` with a fan-out. Keep it inside the same `try/except Exception` that already wraps it, **and wrap each method individually** so one failing channel cannot take out the others:

```python
            methods = cfg.get("notify_methods") or ["email"]
            # "phone" is deliberately absent from this loop. It is not an
            # outbound channel: it means she wants the client to call her, so
            # her number goes to the CLIENT (see the confirmation block above)
            # and nothing is sent to her here. A practitioner who picks only
            # "phone" correctly receives nothing.
            if "email" in methods or "calendar" in methods:
                ics_for_her = ics if "calendar" in methods else b""
                try:
                    send_evox_email(practitioner_email, "", subj,
                                    html_body2, text_body2, ics_for_her)
                except Exception as e:  # noqa: BLE001
                    print(f"[public-book] practitioner email failed for {pid!r}: {e!r}",
                          flush=True)
            if "text" in methods:
                try:
                    sms = (f"New {st['label']} booking: {' '.join(name.split())} "
                           f"on {her_nice} ({cfg['timezone']}). {email}")
                    _send_sms_via_ghl(practitioner_email, sms,
                                      _pp.practitioner_phone_by_id(pid))
                except Exception as e:  # noqa: BLE001
                    print(f"[public-book] practitioner sms failed for {pid!r}: {e!r}",
                          flush=True)
```

`ics` is the invite already built for the client's confirmation. If it is not in scope at this point, hoist its construction above both blocks rather than building it twice.

- [ ] **Step 4: Run to verify it passes**

Expected: PASS.

- [ ] **Step 5: Mutation-test**

1. Remove the per-method `try/except` around the SMS call. Confirm `test_one_failing_method_does_not_stop_the_others` goes **red**. Restore.
2. Add `"phone"` to the email condition. Confirm `test_phone_only_sends_her_nothing` goes **red**. Restore.

- [ ] **Step 6: Commit**

```bash
git add app.py tests/test_practitioner_booking_routes.py
git commit -m "feat(booking): notify the practitioner by her chosen methods"
```

---

## Task 4: Her number reaches the client, on opt-in only

**Files:**
- Modify: `app.py`, `dashboard/public_surface.py`, `dashboard/practitioner_render.py`, `static/practitioner-booking.html`
- Test: `tests/test_practitioner_booking_routes.py`, `tests/test_practitioner_render.py`

**Interfaces:**
- Consumes: `cfg["notify_methods"]`, `practitioner_phone_by_id` from Tasks 1-2.
- Produces: nothing later depends on this.

**This is the task that closes the original gap:** a `phone`-medium booking today gives neither party a number. It is also the task that publishes a phone number, so the opt-in gate is the whole point.

**The whitelist matters.** `dashboard/public_surface.py` has `PRACTITIONER_PUBLIC_FIELDS`, a frozenset of 14 names, and `_public_only` drops anything not in it. A field that is not added there **cannot reach the page**, and a field added there without a renderer reaches the payload and stops — a guard test in `tests/test_public_surface_routes.py` asserts every whitelisted field is rendered, so adding one without rendering it turns that test red. Add `practitioner_phone` to both the whitelist and the renderer, and to that test's sentinel map.

- [ ] **Step 1: Write the failing test**

```python
def test_her_number_is_in_the_confirmation_when_she_chose_phone(public, logdb, monkeypatch):
    sends = _spy_sends(monkeypatch)
    with _open(logdb) as c:
        pb.set_config(c, PID, dict(CFG, notify_methods=["phone"]))
    _book_one(public)
    to_client = [s for s in sends["email"] if s["to"] == "client@example.com"]
    assert to_client and "+15550100" in str(to_client[0])


def test_her_number_is_absent_when_she_did_not_choose_phone(public, logdb, monkeypatch):
    """Publishing a phone number nobody asked to publish is the system making
    a claim on her behalf."""
    sends = _spy_sends(monkeypatch)
    with _open(logdb) as c:
        pb.set_config(c, PID, dict(CFG, notify_methods=["email"]))
    _book_one(public)
    to_client = [s for s in sends["email"] if s["to"] == "client@example.com"]
    assert to_client and "+15550100" not in str(to_client[0])
```

And at the renderer level, in `tests/test_practitioner_render.py`:

```python
def test_the_public_page_shows_her_number_only_when_given_one():
    html = pr.render_page_html(_view(practitioner_phone="+15550100"),
                               canonical_url=CANON)
    assert "+15550100" in html
    assert "+15550100" not in pr.render_page_html(_view(), canonical_url=CANON)
```

- [ ] **Step 2: Run to verify it fails**

Expected: FAIL — the number appears nowhere.

- [ ] **Step 3: Implement**

In the **client's** confirmation block in `api_public_book`, when `"phone" in methods`, look her number up once and add a line naming her and it:

```python
    her_phone = _pp.practitioner_phone_by_id(pid) if "phone" in methods else ""
    # She asked to be reached by phone, so the client needs the number. Without
    # this a phone-medium booking gives neither party a way to call the other.
    if her_phone:
        lines.append(f"Call: {her_phone}")
```

In `dashboard/public_surface.py`, add `"practitioner_phone"` to `PRACTITIONER_PUBLIC_FIELDS` and populate it in `build_practitioner_storefront` **only** when her booking config exists and includes `phone`, defaulting to `""` otherwise.

In `dashboard/practitioner_render.py`, render it in a `_line("phone", ...)` block beside the location — it returns `""` for an empty value, so an opted-out practitioner emits nothing.

In `static/practitioner-booking.html`, add four checkboxes labelled in plain words, defaulting to email checked and the rest clear, and a phone field shown when `phone` or `text` is ticked. Say next to the phone checkbox that her number will be shown to people who book her, because that is the consequence and she should read it before ticking it.

- [ ] **Step 4: Run to verify it passes**

Also run `tests/test_public_surface_routes.py` — the whole-whitelist guard lives there and will go red if the new field is not rendered.

- [ ] **Step 5: Mutation-test the opt-in gate**

Remove the `if "phone" in methods` condition so her number is always looked up and shown. Confirm `test_her_number_is_absent_when_she_did_not_choose_phone` goes **red**. Restore.

- [ ] **Step 6: Run the wider suite**

Run: `doppler run -p remedy-match -c dev -- python3 -m pytest tests/test_practitioner*.py tests/test_public_surface*.py -q -p no:randomly`

Two failures are expected and are not yours, as named in the constraints.

- [ ] **Step 7: Commit**

```bash
git add app.py dashboard/public_surface.py dashboard/practitioner_render.py \
        static/practitioner-booking.html tests/
git commit -m "feat(booking): show her number to the client when she asked to be phoned"
```

---

## Verification before merge

```bash
curl -s "https://myhealingoasis.com/api/book/<slug>/slots?session=intro" | head -c 300
curl -s "https://myhealingoasis.com/<slug>" | grep -c "Call:"
```

Then, with a test practitioner configured for all four methods, make one real booking and confirm: her email arrives with an ICS attached, her SMS arrives, and the client's confirmation carries her number. **The SMS is the one nothing else proves** — GHL's contact requirement means it can fail for a practitioner who is not a GHL contact, and no test can tell you whether a real number was reachable.

## Self-review notes

**Coverage.** Four methods, each with its own behaviour and its own test: email (Task 3), calendar (Task 3), text (Tasks 2-3), phone (Task 4). Opt-in default asserted in Task 1 and mutation-tested. Never-fail-the-booking asserted in Task 3 for both a raising channel and a partial failure.

**The known weakness, stated rather than hidden.** SMS depends on the practitioner existing as a GHL contact with a phone. `_upsert_contact` creates one from her email and now carries her number, but a GHL account misconfiguration will surface as `{"skipped": ...}` in a log line and nowhere else — she will simply not get texts and will not know why. A delivery-visibility surface is out of scope here; `dashboard/sms_delivery.py` already records Twilio-shaped statuses and would be the place to put one.

**Not in scope.** In-person bookings still have no address field, so `medium="in-person"` remains non-functional end to end. That needs a location field on the config and a decision about what a practitioner must supply before enabling it — the same product question this plan answers for phone, and it should be answered the same way, deliberately rather than by default.
