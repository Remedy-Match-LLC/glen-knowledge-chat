# Zone-Aware Reminder Window, and Public Bookings in the Portal Calendar

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the last two gaps in multi-tenant booking. A client's day-before reminder should arrive a day before *their* appointment regardless of which timezone their practitioner works in, and a booking they made should appear in their own portal calendar with the right practitioner's name at the right time.

**Architecture:** Both defects have one cause: code written when every booking belonged to Rae or Glen, so "Hawaii" and "Rae or Glen" were safe constants. Neither is true now. The reminder window becomes a coarse SQL prefilter plus a precise per-row decision made in the practitioner's own zone; the portal calendar resolves the practitioner's name and zone per row instead of hardcoding them.

**Tech Stack:** Flask, `app.py` (the reminder cron), `dashboard/portal_calendar.py`, `dashboard/practitioner_booking.py`. Dual store: SQLite `LOG_DB` and Postgres via `dashboard/db.py`. **Production runs `DB_BACKEND=postgres`.**

**Spec:** `docs/superpowers/specs/2026-08-27-practitioner-website-design.md`

## Global Constraints

- **Rae's and Glen's own bookings must behave exactly as they do today.** Their timezone IS `Pacific/Honolulu`, so any zone-aware calculation must produce the identical result for them. Prove it with a test, not by reasoning.
- **The reminder cron sends real email.** Every test touching it patches `send_evox_email` at the module. A call that is safe only because dev lacks an SMTP host is not safe.
- **`reminded_at` is a one-way stamp.** A booking wrongly skipped by a window change is never reminded at all. Widening a window is recoverable; narrowing one is not.
- The portal calendar is client-facing and authenticated. A client must never see another practitioner's client's appointment, and must never see a name or time that is not theirs.
- Copy rules: no em dashes, no `--` in client-facing strings, "client" not "patient".

---

### Task 1: The reminder window becomes zone-aware

**Files:**
- Modify: `app.py` (`evox_run_reminders`, ~23755)
- Test: `tests/test_booking_reminders.py`

`now = _hst_now()` produces naive Hawaii wall time; `lo`/`hi` are `now+24h`/`now+48h` as naive ISO strings. They are compared against `evox_bookings.start_ts`, which is naive wall time **in the practitioner's own zone**.

For Rae and Glen those are the same clock, so the arithmetic was correct. For a practitioner in Anchorage (two hours ahead of Hawaii in summer) her 09:00 string is compared against Hawaii-derived bounds, so her clients' real lead time is 22 to 46 hours rather than 24 to 48. A DST transition shifts that offset mid-year, and consecutive daily runs stop tiling: a one-hour band of appointments can fall through the gap and, because nothing stamps them, simply never be reminded.

**Approach:** keep SQL as a coarse prefilter and make the precise decision per row, where the practitioner's config is already loaded.

- The prefilter must be wide enough that no eligible row is excluded by any real offset. IANA zones span UTC-11 to UTC+14, so a naive string can be up to ~25 hours away from the Hawaii-naive equivalent in either direction. Choose bounds that cannot clip, and say in a comment why the number is what it is.
- Then, per row, compute the appointment's true instant from `start_ts` plus the practitioner's zone, and include it only when it falls in the real 24-to-48-hour band from now. Rae and Glen resolve to `Pacific/Honolulu` and must land identically.
- A row whose zone cannot be resolved must be **skipped, not stamped** — a reminder we cannot place in time is one we must be able to send later.

- [ ] **Step 1: Write the failing tests.** An Anchorage practitioner's booking is reminded with a true 24-to-48-hour lead, not 22-to-46; a Honolulu practitioner's booking behaves exactly as before (assert against the same expectations the existing tests use); a booking just outside the real band is not reminded and not stamped; a booking whose practitioner config is unreadable is skipped and not stamped. Freeze or inject `now` — **never pin a calendar date**, which turns green today and red forever after a week.
- [ ] **Step 2: Run them and watch them fail.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run the tests to green**, including every existing reminder test unchanged.
- [ ] **Step 5: Mutation-test the band.** Revert the per-row check so the coarse prefilter alone decides; confirm the "just outside the band" test goes RED. Restore. Report the actual output.
- [ ] **Step 6: Commit.**

---

### Task 2: Public bookings in the client's portal calendar

**Files:**
- Modify: `dashboard/portal_calendar.py`
- Test: `tests/test_portal_calendar.py` (or the file that covers it)

`dashboard/portal_calendar.py:156` filters `practitioner IN ('rae','glen')`. That filter is **load-bearing and deliberate** — read the comment above it before changing anything. This view renders `"Dr. Glen" if practitioner == "glen" else "Rae"` and stamps every row `Pacific/Honolulu` through `_zoned_iso`. Both are wrong for a public booking, whose practitioner is another practitioner's id working in her own zone. Without the filter, a client would see their appointment mislabelled "Rae" at a Honolulu time in their authenticated portal, marked Confirmed.

So the filter may only come out **after** the view is practitioner-aware. Build the naming and the zone first, then drop it — the same ordering the reminder branch required, and for the same reason.

**Interfaces:**
- Consumes: the practitioner's timezone and display name. `dashboard/practitioner_booking.get_config` gives the zone. For the name, find what already resolves a practitioner id to a display name and reuse it; do **not** make `dashboard/portal_calendar.py` import `app`.

- [ ] **Step 1: Write the failing tests.** A public booking appears in its client's portal with that practitioner's name and her zone; a Rae booking and a Glen booking are unchanged, name and time both; a booking whose practitioner cannot be resolved is omitted rather than shown under a wrong name; and a client never sees a booking that is not theirs.
- [ ] **Step 2: Run them and watch them fail.**
- [ ] **Step 3: Implement the naming and the zone, then remove the filter, in that order.**
- [ ] **Step 4: Run the tests to green.**
- [ ] **Step 5: Mutation-test the fallback.** Make an unresolvable practitioner render under the old `"Rae"` default; confirm the omission test goes RED. Restore.
- [ ] **Step 6: Commit.**

---

### Task 3: Verify on Postgres

**Files:** none (verification only)

- [ ] **Step 1: Local Postgres** (`initdb` + `pg_ctl`, spare port, socket dir under `/tmp` so the path stays under 103 bytes). Seed one Honolulu practitioner and one Anchorage practitioner, each with a booking in the real band and one just outside it.
- [ ] **Step 2: Drive the real `/api/evox/run-reminders` endpoint** through the Flask test client with `send_evox_email` patched, and read the actual messages. Then read the portal calendar payload for each client. **Drive the endpoints, do not replicate their queries** — a hand-copied query tests your copy, not the code, and has already misled this branch once.
- [ ] **Step 3: Report** the actual output and tear the instance down.
