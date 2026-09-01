# Booking: Where To Meet, and Reminders For Public Bookings

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A client who books a practitioner is told *where* the appointment happens, and is reminded the day before. Today a zoom booking says only "How: zoom" with no link, an in-person booking has no address at all, and a public booking gets no reminder of any kind.

**Architecture:** One optional `location` string per session type carries the "where" — a meeting link for zoom, a street address for in-person — and flows into both confirmation emails and the existing `LOCATION:` field of the ICS invite, which `evox.build_ics` already accepts. Separately, `evox_bookings` gains `visitor_tz` so a reminder can state the appointment in the same timezone the client's confirmation used, and the daily reminder cron gains a practitioner-aware branch instead of excluding public bookings.

**Tech Stack:** Flask, `dashboard/practitioner_booking.py`, `dashboard/evox.py`, `app.py`, `static/practitioner-booking.html`. Dual store: SQLite `LOG_DB` and Postgres via `dashboard/db.py` (which translates `?`). **Production runs `DB_BACKEND=postgres`.**

**Spec:** `docs/superpowers/specs/2026-08-27-practitioner-website-design.md`

## Global Constraints

- **The daily reminder cron sends real email to real clients.** Every test touching it must patch `send_evox_email` at the module, never rely on an unset `SMTP_HOST`. A call that is safe only because dev lacks a credential is not safe.
- **`reminded_at` is a one-way stamp.** Once set, no later reminder can fire for that booking. A wrong reminder is therefore not just wrong, it permanently suppresses the right one. Stamp only after a send actually succeeds.
- **Never widen the cron's recipient set without a matching branch.** Its `if/elif/else` on `session_type` is a claim that every unlisted value means "EVOX session, call Rae". Adding rows it can see without adding a branch hands strangers Rae's phone number.
- `location` is practitioner-authored free text that reaches a client's inbox and calendar. Escape it everywhere it renders, and cap its length.
- Copy rules: no em dashes, no `--` in client-facing or practitioner-facing strings, "client" not "patient".
- Identity always comes from `_practitioner_session_pid()`; a practitioner_id in a request body is ignored.

---

### Task 1: `location` on a session type

**Files:**
- Modify: `dashboard/practitioner_booking.py` (`_validate_session_types`, `MAX_*` constants)
- Modify: `static/practitioner-booking.html`
- Test: `tests/test_practitioner_booking.py`, `tests/test_practitioner_booking_routes.py`

**Interfaces:**
- Produces: each dict in `cfg["session_types"]` gains `"location"` (a string, `""` when unset). Existing stored rows have no such key and must read back as `""`, never `None` and never a KeyError.

- [ ] **Step 1: Write the failing tests.** A session type round-trips its location; a stored row predating the field reads back `""`; a location longer than the cap is refused with a readable message; a location is optional (a session type without one still validates).
- [ ] **Step 2: Run them and watch them fail.**
- [ ] **Step 3: Implement.** Follow `_validate_session_types`' existing shape exactly — it already validates `slug`, `label`, `duration_min`, `medium` and raises `BookingConfigError` with practitioner-readable text. Use the existing `_text(value, limit)` helper rather than a new one.
- [ ] **Step 4: Add the form field.** One input per session type, its label chosen by medium: "Meeting link" for zoom, "Address" for in-person, and for phone say the client will be given her number if she has chosen the phone notify method, else leave it out. Respect the page's existing `loaded` lockout.
- [ ] **Step 5: Run the tests to green.**
- [ ] **Step 6: Mutation-test the length cap.** Remove it, confirm the over-length test goes RED, restore. Report the actual output.
- [ ] **Step 7: Commit.**

---

### Task 2: The location reaches the client

**Files:**
- Modify: `app.py` (`api_public_book`'s client confirmation and practitioner notification)
- Test: `tests/test_practitioner_booking_routes.py`

`evox.build_ics` **already takes a `location` argument and already emits `LOCATION:`** — read it before writing anything. The gap is that the booking route passes nothing meaningful.

- [ ] **Step 1: Write the failing tests.** With a zoom session type carrying a link: the client's email body contains it, and the ICS bytes contain a `LOCATION:` line carrying it. With no location set: no empty "Where:" label and no bare `LOCATION:` artifact appears in either. With a location containing HTML: it is escaped in the email.
- [ ] **Step 2: Run them and watch them fail.**
- [ ] **Step 3: Implement.** The practitioner's own copy should carry it too. Do not disturb the `ics = b""` binding above the notification block, nor the notify-method fan-out: both are load-bearing and a previous round broke a booking flow by moving them.
- [ ] **Step 4: Run the tests to green.**
- [ ] **Step 5: Mutation-test the empty case.** Make an unset location render its label anyway; confirm the no-artifact test goes RED; restore.
- [ ] **Step 6: Commit.**

---

### Task 3: Remember the client's timezone

**Files:**
- Modify: `dashboard/evox.py` (`init_evox_tables`, `create_booking`)
- Modify: `app.py` (`api_public_book`)
- Test: `tests/test_evox.py` (or the file that covers `create_booking`)

The confirmation email already renders the client's own timezone when the booking carries `tz`. Nothing stores it, so a reminder could only speak in the practitioner's zone — telling a client "09:00 HST" for the appointment their confirmation called "11:00 AKDT".

**Interfaces:**
- Produces: `evox_bookings.visitor_tz`, additive and nullable. `create_booking` gains a keyword-only `visitor_tz=""`. **Omitting it must leave every existing caller byte-identical** — there are several, and this is the same additive-parameter contract `build_ics`'s `tz_name` already follows.

- [ ] **Step 1: Write the failing tests.** A public booking stores the tz it was given; a booking made without one stores empty and does not raise; the five existing `create_booking` callers still work unchanged.
- [ ] **Step 2: Run them and watch them fail.**
- [ ] **Step 3: Implement.** Use the additive-`ALTER` idiom already at `dashboard/evox.py:44`. Note the Postgres hazard: a swallowed `DuplicateColumn` aborts the transaction, so anything after it in the same transaction silently never runs. Check what follows.
- [ ] **Step 4: Run the tests to green.**
- [ ] **Step 5: Commit.**

---

### Task 4: Remind public bookings, and stop misrouting `triage`

**Files:**
- Modify: `app.py` (the daily reminder cron — the `SELECT ... reminded_at IS NULL` query and its `if/elif/else`)
- Test: a new `tests/test_booking_reminders.py`

Two problems, one branch.

**The live one, which predates all of this.** The chain has no `triage` case, so a triage booking falls to the `else` and the client is told *"Reminder: your EVOX session is tomorrow at {time} HST. Call Rae at {EVOX_RAE_PHONE}."* Glen's own discovery-call clients have been getting Rae's phone number and the wrong session name. Fix it.

**The new one.** The query is scoped `practitioner IN ('rae','glen')`, so a public booking gets no reminder at all. Widen it — but the `else` branch is a claim that every unlisted value means "EVOX, call Rae", so widening the query without a matching branch would hand strangers Rae's number. The branch comes first.

A public booking's reminder needs: her display name, the session label, the location from Task 1, and the time rendered in the booking's `visitor_tz` (Task 3) falling back to her configured timezone. **The hardcoded `HST` label is wrong for any practitioner outside Hawaii** — render the zone that was actually used.

- [ ] **Step 1: Write the failing tests**, with `send_evox_email` patched at the module. Cover: a `triage` booking gets a triage reminder and never Rae's phone number; a public booking gets a reminder naming its practitioner, session and location; that reminder's time is in the client's stored timezone with a matching label; a booking whose practitioner has no config is skipped rather than sent something wrong; a failed send does **not** stamp `reminded_at`; and a second run does not re-send an already-stamped booking.
- [ ] **Step 2: Run them and watch them fail.**
- [ ] **Step 3: Implement the branch first, then widen the query.**
- [ ] **Step 4: Run the tests to green.**
- [ ] **Step 5: Mutation-test the two guards that matter.** Remove the `triage` branch and confirm the "never Rae's phone number" test goes RED. Then make a failed send stamp `reminded_at` anyway and confirm that test goes RED. Restore each. Report the actual output.
- [ ] **Step 6: Commit.**

---

### Task 5: Verify on Postgres, then on production

**Files:** none (verification only)

- [ ] **Step 1: Local Postgres.** `initdb` + `pg_ctl` on a spare port with the socket dir under `/tmp` (the path must stay under 103 bytes). With `DB_BACKEND=postgres` and `PG_DSN` set, run `init_evox_tables` twice on one connection then a read in the same transaction (the aborted-transaction trap), a `create_booking` with and without `visitor_tz`, and a read-back. Report the actual output and tear the instance down.
- [ ] **Step 2: After deploy,** book a real slot on `dr-glen` from a client address, confirm the confirmation email and ICS carry the location, then invoke the reminder endpoint and confirm the reminder names the right practitioner, session, location and timezone. Cancel the test booking afterwards.
