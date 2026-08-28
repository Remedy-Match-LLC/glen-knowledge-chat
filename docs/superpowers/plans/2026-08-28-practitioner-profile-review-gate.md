# Practitioner Profile Draft/Review Gate - Implementation Plan (Section 2a)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A practitioner's profile edit becomes a DRAFT that Glen reviews and approves before anything reaches the public page.

**Architecture:** A `practitioner_profile_drafts` table in the **sqlite LOG_DB** holds one working copy per practitioner, with a `draft -> submitted -> approved` lifecycle modelled directly on `dashboard/ff_match_drafts.py`, which lives in the same store. `save_profile` is renamed `save_draft` and writes only the draft. A new `publish_draft` copies approved values into `practitioners` and stamps `profile_self_authored_at`, which is the single thing that makes a profile public. A console queue lets Glen approve or reject.

**Tech Stack:** Python 3, Flask, Postgres via `db_supabase.supabase_cursor`, sqlite (LOG_DB) via `dashboard.db`, pytest.

**Spec:** `docs/superpowers/specs/2026-08-27-practitioner-website-design.md` (section 2)

## Global Constraints

- **Nothing becomes public without an explicit approval.** `profile_for_slug` gates on `practitioners.profile_self_authored_at`; ONLY `publish_draft` may set it. No other code path may stamp it.
- **Mechanism general, policy conservative.** Build per-field review policy support, then set the beta policy so EVERY field is reviewed. Relaxing later must be a constant change, not a schema change.
- **The public whitelist stays.** `PRACTITIONER_PUBLIC_FIELDS` in `dashboard/public_surface.py` is unchanged by this plan. Adding a public field is a separate, deliberate decision (that is section 2b).
- **Fail closed.** Any error reading draft state yields "no draft", never a partial publish and never a 500 on a public path.
- **Drafts are sqlite (LOG_DB), live profile is Postgres (`practitioners`).** This split is deliberate. `db_supabase.supabase_cursor()` is a raw psycopg2 cursor with NO placeholder translation, so `?` placeholders silently pass sqlite tests and fail in production. Draft code uses `?` against `dashboard.db`; ONLY `_write_live_profile` talks to Postgres, and it uses `%s`. Never mix the two in one function.
- **Console auth:** every console route guards with `if not _console_key_ok(): return jsonify({"error": "unauthorized"}), 401` (`app.py:34384`).
- **Stage named files only.** NEVER `git add -A` or `git add .` in this shared checkout.
- **Never run the full test suite.** Run only named files.
- Work in `/tmp/wt-deploy-chat-a191e6ab`, branch `sess/a191e6ab-s2`.

## Scope boundary

**In scope:** the draft table, the lifecycle, the review policy, the rename of `save_profile`, the publish path, and the console queue.

**Out of scope (later 2b-2e plans):** the new profile fields (tagline, how_i_work, credentials, SEO), the `logo_url` dead-write and `photo_url` validation bugs, the claim-language lint, own-storage assets, and the slug-claim UI. This plan changes WHEN a value goes public, not WHICH values exist.

## File Structure

- **Create `dashboard/practitioner_drafts.py`** - the draft store and its lifecycle. sqlite (LOG_DB) only, via `dashboard.db`. No Flask import, no Postgres import. Needs no migration: `init_tables` creates it, exactly as `dashboard/referrals.py` and `dashboard/ff_match_drafts.py` do.
- **Modify `dashboard/practitioner_profile.py`** - rename `save_profile` to `save_draft`, redirect it at the draft store, add `publish_draft`.
- **Modify `app.py`** - update the settings POST call site; add three console routes.
- **Modify `tests/test_practitioner_profile_routes.py`** - update the tests that patch `save_profile` by name.
- **Create `tests/test_practitioner_drafts.py`** - store and lifecycle unit tests.
- **Create `tests/test_practitioner_review_console.py`** - console route tests.

---

### Task 1: The draft store

**Files:**
- Create: `dashboard/practitioner_drafts.py`
- Test: `tests/test_practitioner_drafts.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `STATUSES: frozenset`, `init_tables(cx) -> None`, `get_draft(cx, pid) -> dict|None`, `upsert_draft(cx, pid, fields: dict) -> dict`. All take a sqlite connection (`cx`), matching `dashboard/referrals.py`.

A draft row is one working copy per practitioner. `fields` is a JSON object of proposed profile values, deliberately schemaless here so section 2b can add fields without a migration.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_practitioner_drafts.py
"""Unit tests for the practitioner profile draft store.

Uses sqlite as a stand-in for the Postgres cursor: every statement in this
module is portable, which is itself the constraint being tested.
"""
import json
import sqlite3

import pytest

from dashboard import practitioner_drafts as pd

PID = "11111111-1111-1111-1111-111111111111"


@pytest.fixture
def cur(tmp_path):
    """Named `cur` throughout for brevity, but it is a sqlite CONNECTION --
    the same thing dashboard/referrals.py and ff_match_drafts.py take."""
    cx = sqlite3.connect(str(tmp_path / "t.db"))
    cx.row_factory = sqlite3.Row
    pd.init_tables(cx)
    return cx


def test_statuses_are_the_three_lifecycle_states():
    assert pd.STATUSES == frozenset({"draft", "submitted", "approved"})


def test_get_draft_returns_none_when_absent(cur):
    assert pd.get_draft(cur, PID) is None


def test_upsert_creates_a_draft_status_row(cur):
    out = pd.upsert_draft(cur, PID, {"bio": "hello"})
    assert out["status"] == "draft"
    assert out["fields"] == {"bio": "hello"}


def test_upsert_is_idempotent_on_practitioner_id(cur):
    pd.upsert_draft(cur, PID, {"bio": "one"})
    pd.upsert_draft(cur, PID, {"bio": "two"})
    assert pd.get_draft(cur, PID)["fields"] == {"bio": "two"}


def test_editing_an_approved_draft_returns_it_to_draft(cur):
    """A published practitioner who edits again must re-enter review."""
    pd.upsert_draft(cur, PID, {"bio": "one"})
    cur.execute("UPDATE practitioner_profile_drafts SET status='approved'"
                " WHERE practitioner_id=?", (PID,))
    pd.upsert_draft(cur, PID, {"bio": "two"})
    assert pd.get_draft(cur, PID)["status"] == "draft"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /tmp/wt-deploy-chat-a191e6ab && python3 -m pytest tests/test_practitioner_drafts.py -v`
Expected: FAIL, `ModuleNotFoundError: dashboard.practitioner_drafts`.

- [ ] **Step 3: Write minimal implementation**

```python
# dashboard/practitioner_drafts.py
"""Practitioner profile drafts: the working copy Glen reviews before it goes public.

Spec: docs/superpowers/specs/2026-08-27-practitioner-website-design.md section 2.
Lifecycle and storage modelled on dashboard/ff_match_drafts.py, which lives in
the same sqlite LOG_DB: draft -> submitted -> approved.

Drafts are sqlite ON PURPOSE. The live profile is Postgres, but
db_supabase.supabase_cursor() is a raw psycopg2 cursor with no placeholder
translation -- `?` there would pass every sqlite test and fail in production.
Keeping drafts in sqlite keeps one dialect per module. The sqlite -> Postgres
hop happens once, in practitioner_profile.publish_draft, exactly as
profile_for_slug already hops the other way.

Nothing in this module makes anything public. The public page gates on
practitioners.profile_self_authored_at, and ONLY practitioner_profile.publish_draft
sets that. Losing track of this distinction is how a review gate leaks.
"""

import datetime
import json

STATUSES = frozenset({"draft", "submitted", "approved"})


def _now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def init_tables(cx):
    """Create the drafts table if absent. Idempotent; called on read paths,
    matching dashboard/referrals.py."""
    cx.execute("""CREATE TABLE IF NOT EXISTS practitioner_profile_drafts (
        practitioner_id TEXT PRIMARY KEY,
        fields TEXT NOT NULL DEFAULT '{}',
        status TEXT NOT NULL DEFAULT 'draft',
        review_note TEXT, submitted_at TEXT, reviewed_at TEXT,
        created_at TEXT, updated_at TEXT)""")
    cx.execute("CREATE INDEX IF NOT EXISTS ix_ppd_status"
               " ON practitioner_profile_drafts(status, updated_at DESC)")
    cx.commit()


def _row(r):
    if r is None:
        return None
    d = dict(r)
    raw = d.get("fields") or "{}"
    d["fields"] = json.loads(raw) if isinstance(raw, str) else raw
    return d


def get_draft(cx, pid):
    """The practitioner's working copy, or None."""
    row = cx.execute("SELECT * FROM practitioner_profile_drafts"
                     " WHERE practitioner_id=?", (str(pid),)).fetchone()
    return _row(row)


def upsert_draft(cx, pid, fields):
    """Write the practitioner's proposed values and put the row in 'draft'.

    Editing ALWAYS returns the row to 'draft', including from 'approved': a
    practitioner who changes their page after approval must be reviewed again,
    or the gate is one edit wide.
    """
    payload, now = json.dumps(fields or {}), _now()
    if get_draft(cx, pid):
        cx.execute("UPDATE practitioner_profile_drafts SET fields=?, status='draft',"
                   " review_note=NULL, updated_at=? WHERE practitioner_id=?",
                   (payload, now, str(pid)))
    else:
        cx.execute("INSERT INTO practitioner_profile_drafts"
                   " (practitioner_id, fields, status, created_at, updated_at)"
                   " VALUES (?,?, 'draft', ?, ?)", (str(pid), payload, now, now))
    cx.commit()
    return get_draft(cx, pid)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /tmp/wt-deploy-chat-a191e6ab && python3 -m pytest tests/test_practitioner_drafts.py -v`
Expected: PASS, 5 tests.

- [ ] **Step 5: Commit**

```bash
cd /tmp/wt-deploy-chat-a191e6ab
git add dashboard/practitioner_drafts.py tests/test_practitioner_drafts.py
git commit -m "feat(drafts): practitioner profile draft store and migration"
```

---

### Task 2: Submit, approve, reject, and the queue read

**Files:**
- Modify: `dashboard/practitioner_drafts.py`
- Test: `tests/test_practitioner_drafts.py`

**Interfaces:**
- Consumes: `init_tables`, `get_draft`, `upsert_draft` from Task 1.
- Produces: `submit(cx, pid) -> bool`, `approve(cx, pid, note="") -> bool`, `reject(cx, pid, note) -> bool`, `list_by_status(cx, status=None, limit=200) -> list[dict]`. All take a sqlite connection.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_practitioner_drafts.py
PID2 = "22222222-2222-2222-2222-222222222222"


def test_submit_moves_draft_to_submitted(cur):
    pd.upsert_draft(cur, PID, {"bio": "x"})
    assert pd.submit(cur, PID) is True
    d = pd.get_draft(cur, PID)
    assert d["status"] == "submitted" and d["submitted_at"]


def test_submit_is_false_when_there_is_no_draft(cur):
    assert pd.submit(cur, PID) is False


def test_approve_marks_approved_and_stamps_review_time(cur):
    pd.upsert_draft(cur, PID, {"bio": "x"})
    pd.submit(cur, PID)
    assert pd.approve(cur, PID) is True
    d = pd.get_draft(cur, PID)
    assert d["status"] == "approved" and d["reviewed_at"]


def test_approve_refuses_a_row_that_was_never_submitted(cur):
    """Approving straight from 'draft' would skip the practitioner's own submit."""
    pd.upsert_draft(cur, PID, {"bio": "x"})
    assert pd.approve(cur, PID) is False
    assert pd.get_draft(cur, PID)["status"] == "draft"


def test_reject_returns_it_to_draft_with_the_note(cur):
    pd.upsert_draft(cur, PID, {"bio": "x"})
    pd.submit(cur, PID)
    assert pd.reject(cur, PID, "please remove the health claim") is True
    d = pd.get_draft(cur, PID)
    assert d["status"] == "draft"
    assert d["review_note"] == "please remove the health claim"


def test_reject_requires_a_note(cur):
    pd.upsert_draft(cur, PID, {"bio": "x"})
    pd.submit(cur, PID)
    with pytest.raises(ValueError):
        pd.reject(cur, PID, "")


def test_list_by_status_returns_only_that_status(cur):
    pd.upsert_draft(cur, PID, {"bio": "a"})
    pd.upsert_draft(cur, PID2, {"bio": "b"})
    pd.submit(cur, PID)
    subs = pd.list_by_status(cur, "submitted")
    assert [d["practitioner_id"] for d in subs] == [PID]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /tmp/wt-deploy-chat-a191e6ab && python3 -m pytest tests/test_practitioner_drafts.py -v`
Expected: FAIL, `AttributeError: module 'dashboard.practitioner_drafts' has no attribute 'submit'`.

- [ ] **Step 3: Write minimal implementation**

```python
# append to dashboard/practitioner_drafts.py

def submit(cx, pid):
    """Practitioner sends their draft for review. True if a draft moved."""
    now = _now()
    cur = cx.execute("UPDATE practitioner_profile_drafts SET status='submitted',"
                     " submitted_at=?, updated_at=?"
                     " WHERE practitioner_id=? AND status='draft'",
                     (now, now, str(pid)))
    cx.commit()
    return cur.rowcount == 1


def approve(cx, pid, note=""):
    """Glen approves a SUBMITTED draft. True if one moved.

    Deliberately refuses a row still in 'draft': approving something the
    practitioner has not submitted would publish an edit they were mid-way
    through writing.
    """
    now = _now()
    cur = cx.execute("UPDATE practitioner_profile_drafts SET status='approved',"
                     " review_note=?, reviewed_at=?, updated_at=?"
                     " WHERE practitioner_id=? AND status='submitted'",
                     (note or None, now, now, str(pid)))
    cx.commit()
    return cur.rowcount == 1


def reject(cx, pid, note):
    """Send a submitted draft back with a reason. The note is required:
    a rejection the practitioner cannot act on just produces a resubmit."""
    if not (note or "").strip():
        raise ValueError("a rejection needs a note")
    now = _now()
    cur = cx.execute("UPDATE practitioner_profile_drafts SET status='draft',"
                     " review_note=?, reviewed_at=?, updated_at=?"
                     " WHERE practitioner_id=? AND status='submitted'",
                     (note.strip(), now, now, str(pid)))
    cx.commit()
    return cur.rowcount == 1


def list_by_status(cx, status=None, limit=200):
    """Rows for the review queue, newest first."""
    if status:
        rows = cx.execute("SELECT * FROM practitioner_profile_drafts WHERE status=?"
                          " ORDER BY updated_at DESC LIMIT ?",
                          (status, int(limit))).fetchall()
    else:
        rows = cx.execute("SELECT * FROM practitioner_profile_drafts"
                          " ORDER BY updated_at DESC LIMIT ?",
                          (int(limit),)).fetchall()
    return [_row(r) for r in rows]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /tmp/wt-deploy-chat-a191e6ab && python3 -m pytest tests/test_practitioner_drafts.py -v`
Expected: PASS, 12 tests.

- [ ] **Step 5: Commit**

```bash
cd /tmp/wt-deploy-chat-a191e6ab
git add dashboard/practitioner_drafts.py tests/test_practitioner_drafts.py
git commit -m "feat(drafts): submit/approve/reject lifecycle and queue read"
```

---

### Task 3: Per-field review policy

**Files:**
- Modify: `dashboard/practitioner_drafts.py`
- Test: `tests/test_practitioner_drafts.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `REVIEW_POLICY: dict[str, str]`, `DEFAULT_POLICY: str`, `needs_review(field: str) -> bool`, `split_by_policy(fields: dict) -> tuple[dict, dict]`.

`split_by_policy` returns `(auto_fields, review_fields)`. Task 4 uses it. For the beta every field is reviewed, so `auto_fields` is empty — that is the point: the mechanism exists and the policy is conservative.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_practitioner_drafts.py

def test_beta_policy_reviews_every_known_field():
    """Conservative on purpose. Relaxing a field must be a one-line policy
    change here, never a schema change."""
    assert all(v == "review" for v in pd.REVIEW_POLICY.values())


def test_unknown_fields_default_to_review():
    assert pd.needs_review("a_field_invented_next_year") is True


def test_needs_review_reads_the_policy():
    pd.REVIEW_POLICY["location"] = "auto"
    try:
        assert pd.needs_review("location") is False
        assert pd.needs_review("bio") is True
    finally:
        pd.REVIEW_POLICY["location"] = "review"


def test_split_by_policy_separates_auto_from_review():
    pd.REVIEW_POLICY["location"] = "auto"
    try:
        auto, review = pd.split_by_policy({"location": "Hilo, HI", "bio": "x"})
        assert auto == {"location": "Hilo, HI"}
        assert review == {"bio": "x"}
    finally:
        pd.REVIEW_POLICY["location"] = "review"


def test_split_by_policy_sends_everything_to_review_under_beta_policy():
    auto, review = pd.split_by_policy({"bio": "x", "location": "Hilo, HI"})
    assert auto == {}
    assert review == {"bio": "x", "location": "Hilo, HI"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /tmp/wt-deploy-chat-a191e6ab && python3 -m pytest tests/test_practitioner_drafts.py -v -k policy`
Expected: FAIL, `AttributeError: ... has no attribute 'REVIEW_POLICY'`.

- [ ] **Step 3: Write minimal implementation**

```python
# append to dashboard/practitioner_drafts.py

DEFAULT_POLICY = "review"

# Per-field review policy. BETA POLICY: everything is reviewed.
# Relaxing a field to "auto" is a deliberate decision to let it reach the
# public page unreviewed -- make it here, one line, never in the schema.
REVIEW_POLICY = {
    "bio": "review",
    "photo_url": "review",
    "logo_url": "review",
    "services": "review",
    "location": "review",
    "accepting_clients": "review",
}


def needs_review(field):
    """True unless the field is explicitly policied 'auto'. Unknown fields
    default to review, so a field added later is safe before anyone thinks
    about it."""
    return REVIEW_POLICY.get(field, DEFAULT_POLICY) != "auto"


def split_by_policy(fields):
    """Partition proposed values into (auto_publishable, needs_review)."""
    auto, review = {}, {}
    for k, v in (fields or {}).items():
        (review if needs_review(k) else auto)[k] = v
    return auto, review
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /tmp/wt-deploy-chat-a191e6ab && python3 -m pytest tests/test_practitioner_drafts.py -v`
Expected: PASS, 17 tests.

- [ ] **Step 5: Commit**

```bash
cd /tmp/wt-deploy-chat-a191e6ab
git add dashboard/practitioner_drafts.py tests/test_practitioner_drafts.py
git commit -m "feat(drafts): per-field review policy, conservative for the beta"
```

---

### Task 4: Rewire save_profile to write a draft, and add publish_draft

This is the behavioral heart of the plan and the task most likely to break something. Read it fully before starting.

**Files:**
- Modify: `dashboard/practitioner_profile.py`
- Modify: `app.py` (the `save_profile` call site, currently `app.py:19054`)
- Modify: `tests/test_practitioner_profile_routes.py`
- Test: `tests/test_practitioner_drafts.py`

**Interfaces:**
- Consumes: `upsert_draft`, `get_draft`, `approve` from Tasks 1-2.
- Produces: `save_draft(cx, pid, profile) -> dict` (replaces `save_profile`), `publish_draft(cx, pid) -> bool`. Both take the sqlite connection; only `_write_live_profile` opens Postgres.

**The rename matters.** `save_profile` no longer publishes, so keeping the name would leave a function whose name claims something it stopped doing. It is renamed `save_draft`.

**Grep for the pinning tests FIRST.** `tests/test_practitioner_profile_routes.py` patches `save_profile` BY NAME in at least four places (lines ~75, 86, 96, 127). If you rename the function without updating those, the tests will patch an attribute nothing calls and **pass while testing nothing**. Update every one, then prove the updated test still bites: temporarily make the route stop calling `save_draft` and confirm the test goes red, then restore.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_practitioner_drafts.py
from dashboard import practitioner_profile as pp


def test_publish_draft_refuses_an_unapproved_draft(cur, monkeypatch):
    """The gate: only an APPROVED draft may reach the public table."""
    pd.upsert_draft(cur, PID, {"bio": "x"})
    written = {}
    monkeypatch.setattr(pp, "_write_live_profile", lambda pid, f: written.update(f))
    assert pp.publish_draft(cur, PID) is False
    assert written == {}, "an unapproved draft must not reach the live table"


def test_publish_draft_writes_live_only_when_approved(cur, monkeypatch):
    pd.upsert_draft(cur, PID, {"bio": "hello"})
    pd.submit(cur, PID)
    pd.approve(cur, PID)
    written = {}
    monkeypatch.setattr(pp, "_write_live_profile", lambda pid, f: written.update(f))
    assert pp.publish_draft(cur, PID) is True
    assert written["bio"] == "hello"


def test_save_draft_never_touches_the_live_table(cur, monkeypatch):
    """The whole point of section 2a: saving is not publishing."""
    called = {"n": 0}
    monkeypatch.setattr(pp, "_write_live_profile",
                        lambda pid, f: called.__setitem__("n", called["n"] + 1))
    pp.save_draft(cur, PID, {"bio": "hello", "city": "Hilo", "state": "HI"})
    assert called["n"] == 0
    assert pd.get_draft(cur, PID)["fields"]["bio"] == "hello"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /tmp/wt-deploy-chat-a191e6ab && python3 -m pytest tests/test_practitioner_drafts.py -v -k publish`
Expected: FAIL, `AttributeError: module 'dashboard.practitioner_profile' has no attribute 'publish_draft'`.

- [ ] **Step 3: Write minimal implementation**

In `dashboard/practitioner_profile.py`, replace `save_profile` with:

```python
def _write_live_profile(pid, fields):
    """The ONLY place profile_self_authored_at is ever stamped. Everything
    public flows through this one statement, which is what makes the review
    gate auditable."""
    from db_supabase import supabase_cursor
    with supabase_cursor() as cur:
        cur.execute(
            "UPDATE practitioners SET bio=%s, photo_url=%s, specialties=%s,"
            " city=%s, state=%s, accepting_new_patients=%s,"
            " profile_self_authored_at=now(), updated_at=now() WHERE id=%s",
            (fields.get("bio", ""), fields.get("photo_url", ""),
             fields.get("services", []), fields.get("city", ""),
             fields.get("state", ""), bool(fields.get("accepting_clients", True)),
             str(pid)))


def save_draft(cx, pid, profile):
    """Write the practitioner's proposed profile to their DRAFT.

    Renamed from save_profile in section 2a: this no longer publishes
    anything. Sanitization is unchanged and still runs here, so a too-long
    bio is refused at the point the practitioner typed it rather than at
    review time.
    """
    from dashboard import practitioner_drafts as _pd
    fields = {
        "bio": sanitize_bio(profile.get("bio", "")),
        "services": clean_services(profile.get("services")),
        "city": _norm(profile.get("city"))[:MAX_LOC_LEN],
        "state": _norm(profile.get("state"))[:MAX_LOC_LEN],
        "photo_url": (profile.get("photo_url") or "").strip(),
        "accepting_clients": bool(profile.get("accepting_clients", True)),
    }
    _pd.init_tables(cx)
    _pd.upsert_draft(cx, pid, fields)
    return fields


def publish_draft(cx, pid):
    """Copy an APPROVED draft into the public practitioners row.

    Returns False and writes nothing unless the draft is approved. This is
    the gate: no other code path may stamp profile_self_authored_at.
    """
    from dashboard import practitioner_drafts as _pd
    _pd.init_tables(cx)
    d = _pd.get_draft(cx, pid)
    if not d or d.get("status") != "approved":
        return False
    _write_live_profile(pid, d["fields"])
    return True
```

- [ ] **Step 4: Update the call site and the pinning tests**

In `app.py`, change the one call site (search for `save_profile`, currently near line 19054):

```python
            with db.connect(LOG_DB) as _cx:
                profile_out = _pp.save_draft(_cx, pid, body["profile"])
```

Then in `tests/test_practitioner_profile_routes.py`, update EVERY `monkeypatch.setattr(_pp, "save_profile", ...)` to `"save_draft"`, and rename the local helpers accordingly. Do not delete any of these tests — they still assert real route behavior; only the name they patch changed.

- [ ] **Step 5: Prove the updated tests still bite**

Temporarily change the `app.py` call site to not call `_pp.save_draft` at all, run
`cd /tmp/wt-deploy-chat-a191e6ab && doppler run --project remedy-match --config dev -- python3 -m pytest tests/test_practitioner_profile_routes.py -v`
and confirm tests go RED. Then restore the call and confirm GREEN. Put both raw transcripts in your report. A renamed patch target that nothing calls is the exact failure this step exists to rule out.

- [ ] **Step 6: Run the full affected set**

Run: `cd /tmp/wt-deploy-chat-a191e6ab && doppler run --project remedy-match --config dev -- python3 -m pytest tests/test_practitioner_drafts.py tests/test_practitioner_profile.py tests/test_practitioner_profile_routes.py -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
cd /tmp/wt-deploy-chat-a191e6ab
git add dashboard/practitioner_profile.py app.py tests/test_practitioner_profile_routes.py tests/test_practitioner_drafts.py
git commit -m "feat(profile): save writes a draft; only an approved draft publishes"
```

---

### Task 5: The console review queue API

**Files:**
- Modify: `app.py` (add near the other `/api/console/` routes)
- Test: `tests/test_practitioner_review_console.py`

**Interfaces:**
- Consumes: `list_by_status`, `approve`, `reject` from Task 2; `publish_draft` from Task 4.
- Produces: `GET /api/console/practitioner-drafts`, `POST /api/console/practitioner-drafts/<pid>/approve`, `POST /api/console/practitioner-drafts/<pid>/reject`.

Approve does two things in order: mark the draft approved, then publish it. If the publish fails the endpoint reports it rather than claiming success.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_practitioner_review_console.py
"""Console review queue for practitioner profile drafts."""
import os

import pytest

if not os.environ.get("PINECONE_API_KEY"):
    pytest.skip("needs doppler env for import app", allow_module_level=True)

import app as appmod

PID = "11111111-1111-1111-1111-111111111111"


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(appmod, "CONSOLE_SECRET", "s3cret")
    monkeypatch.setitem(appmod.app.config, "TESTING", True)
    return appmod.app.test_client()


def test_queue_requires_the_console_key(client):
    assert client.get("/api/console/practitioner-drafts").status_code == 401


def test_approve_requires_the_console_key(client):
    r = client.post(f"/api/console/practitioner-drafts/{PID}/approve")
    assert r.status_code == 401


def test_reject_requires_the_console_key(client):
    r = client.post(f"/api/console/practitioner-drafts/{PID}/reject",
                    json={"note": "no"})
    assert r.status_code == 401


def test_queue_lists_submitted_drafts(client, monkeypatch, tmp_path):
    monkeypatch.setattr(appmod, "_console_key_ok", lambda: True)
    monkeypatch.setattr(appmod, "LOG_DB", str(tmp_path / "chat_log.db"))
    monkeypatch.setattr("dashboard.practitioner_drafts.list_by_status",
                        lambda cx, status=None, limit=200: [
                            {"practitioner_id": PID, "status": "submitted",
                             "fields": {"bio": "x"}}])
    r = client.get("/api/console/practitioner-drafts")
    assert r.status_code == 200
    assert r.get_json()["drafts"][0]["practitioner_id"] == PID


def test_reject_without_a_note_is_a_400(client, monkeypatch):
    monkeypatch.setattr(appmod, "_console_key_ok", lambda: True)
    r = client.post(f"/api/console/practitioner-drafts/{PID}/reject", json={})
    assert r.status_code == 400


```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /tmp/wt-deploy-chat-a191e6ab && doppler run --project remedy-match --config dev -- python3 -m pytest tests/test_practitioner_review_console.py -v`
Expected: FAIL with 404s, because the routes do not exist.

- [ ] **Step 3: Write minimal implementation**

Add to `app.py`, next to the other `/api/console/` routes:

```python
@app.route("/api/console/practitioner-drafts", methods=["GET"])
def api_console_practitioner_drafts():
    """Profile drafts awaiting Glen's review."""
    if not _console_key_ok():
        return jsonify({"error": "unauthorized"}), 401
    from dashboard import practitioner_drafts as _pd
    from dashboard import practitioner_profile as _pp
    status = request.args.get("status", "submitted")
    with db.connect(LOG_DB) as cx:
        cx.row_factory = sqlite3.Row
        _pd.init_tables(cx)
        drafts = _pd.list_by_status(cx, status)
    return jsonify({"ok": True, "drafts": drafts})


@app.route("/api/console/practitioner-drafts/<pid>/approve", methods=["POST"])
def api_console_practitioner_draft_approve(pid):
    """Approve a submitted draft AND publish it. Publishing is what makes it
    public, so a failed publish must not report success."""
    if not _console_key_ok():
        return jsonify({"error": "unauthorized"}), 401
    from dashboard import practitioner_drafts as _pd
    from dashboard import practitioner_profile as _pp
    note = ((request.get_json(silent=True) or {}).get("note") or "").strip()
    with _db_lock, db.connect(LOG_DB) as cx:
        cx.row_factory = sqlite3.Row
        _pd.init_tables(cx)
        if not _pd.approve(cx, pid, note):
            return jsonify({"ok": False, "error": "no submitted draft"}), 409
        published = _pp.publish_draft(cx, pid)
    if not published:
        return jsonify({"ok": False, "error": "publish_failed"}), 500
    return jsonify({"ok": True, "published": True})


@app.route("/api/console/practitioner-drafts/<pid>/reject", methods=["POST"])
def api_console_practitioner_draft_reject(pid):
    """Send a draft back with a required reason."""
    if not _console_key_ok():
        return jsonify({"error": "unauthorized"}), 401
    from dashboard import practitioner_drafts as _pd
    from dashboard import practitioner_profile as _pp
    note = ((request.get_json(silent=True) or {}).get("note") or "").strip()
    if not note:
        return jsonify({"ok": False, "error": "a rejection needs a note"}), 400
    with _db_lock, db.connect(LOG_DB) as cx:
        cx.row_factory = sqlite3.Row
        _pd.init_tables(cx)
        if not _pd.reject(cx, pid, note):
            return jsonify({"ok": False, "error": "no submitted draft"}), 409
    return jsonify({"ok": True})
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /tmp/wt-deploy-chat-a191e6ab && doppler run --project remedy-match --config dev -- python3 -m pytest tests/test_practitioner_review_console.py -v`
Expected: PASS, 5 tests.

- [ ] **Step 5: Mutation-test the auth guard**

Temporarily delete the `if not _console_key_ok():` block from the GET route, re-run, and confirm `test_queue_requires_the_console_key` goes RED. Restore and confirm GREEN. Raw transcripts in your report. An auth guard that has never been observed refusing is not known to work.

- [ ] **Step 6: Commit**

```bash
cd /tmp/wt-deploy-chat-a191e6ab
git add app.py tests/test_practitioner_review_console.py
git commit -m "feat(console): practitioner profile review queue API"
```

---

### Task 6: The practitioner-side submit action

**Files:**
- Modify: `app.py`
- Test: `tests/test_practitioner_review_console.py`

**Interfaces:**
- Consumes: `submit` from Task 2.
- Produces: `POST /api/practitioner/profile/submit`.

Without this the practitioner can save a draft but never send it, and the queue stays permanently empty.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_practitioner_review_console.py

def test_submit_requires_a_signed_in_practitioner(client, monkeypatch):
    monkeypatch.setattr(appmod, "_practitioner_session_pid", lambda: None)
    r = client.post("/api/practitioner/profile/submit")
    assert r.status_code == 401


def test_submit_moves_the_practitioners_own_draft(client, monkeypatch, tmp_path):
    monkeypatch.setattr(appmod, "_practitioner_session_pid", lambda: PID)
    monkeypatch.setattr(appmod, "LOG_DB", str(tmp_path / "chat_log.db"))
    seen = {}
    monkeypatch.setattr("dashboard.practitioner_drafts.submit",
                        lambda cx, pid: seen.setdefault("pid", pid) or True)
    monkeypatch.setattr("dashboard.practitioner_drafts.init_tables",
                        lambda cx: None)
    r = client.post("/api/practitioner/profile/submit")
    assert r.status_code == 200 and r.get_json()["ok"] is True
    assert seen["pid"] == PID


def test_submit_uses_the_session_pid_not_a_supplied_one(client, monkeypatch, tmp_path):
    """A practitioner must never be able to submit someone else's draft."""
    monkeypatch.setattr(appmod, "_practitioner_session_pid", lambda: PID)
    monkeypatch.setattr(appmod, "LOG_DB", str(tmp_path / "chat_log.db"))
    seen = {}
    monkeypatch.setattr("dashboard.practitioner_drafts.submit",
                        lambda cx, pid: seen.setdefault("pid", pid) or True)
    monkeypatch.setattr("dashboard.practitioner_drafts.init_tables",
                        lambda cx: None)
    client.post("/api/practitioner/profile/submit",
                json={"practitioner_id": "99999999-9999-9999-9999-999999999999"})
    assert seen["pid"] == PID
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /tmp/wt-deploy-chat-a191e6ab && doppler run --project remedy-match --config dev -- python3 -m pytest tests/test_practitioner_review_console.py -v -k submit`
Expected: FAIL with 404, route missing.

- [ ] **Step 3: Write minimal implementation**

Add to `app.py`, next to the other `/api/practitioner/` routes:

```python
@app.route("/api/practitioner/profile/submit", methods=["POST"])
def api_practitioner_profile_submit():
    """Practitioner sends their own draft for review.

    The practitioner id comes from the SESSION, never from the request body,
    so nobody can submit another practitioner's draft.
    """
    pid = _practitioner_session_pid()
    if not pid:
        return jsonify({"ok": False, "error": "not signed in"}), 401
    from dashboard import practitioner_drafts as _pd
    with _db_lock, db.connect(LOG_DB) as cx:
        cx.row_factory = sqlite3.Row
        _pd.init_tables(cx)
        moved = _pd.submit(cx, pid)
    if not moved:
        return jsonify({"ok": False, "error": "nothing to submit"}), 409
    return jsonify({"ok": True, "status": "submitted"})
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /tmp/wt-deploy-chat-a191e6ab && doppler run --project remedy-match --config dev -- python3 -m pytest tests/test_practitioner_review_console.py -v`
Expected: PASS, 8 tests.

- [ ] **Step 5: Run the whole affected set**

Run: `cd /tmp/wt-deploy-chat-a191e6ab && doppler run --project remedy-match --config dev -- python3 -m pytest tests/test_practitioner_drafts.py tests/test_practitioner_profile.py tests/test_practitioner_profile_routes.py tests/test_practitioner_review_console.py tests/test_public_surface_routes.py tests/test_practitioner_site_routes.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
cd /tmp/wt-deploy-chat-a191e6ab
git add app.py tests/test_practitioner_review_console.py
git commit -m "feat(practitioner): submit a profile draft for review"
```

---

## Done criteria

- A practitioner's save writes a draft and changes nothing public.
- Only an approved draft can stamp `profile_self_authored_at`.
- Editing after approval returns the row to `draft`, so the gate is not one edit wide.
- Approving from `draft` (never submitted) is refused.
- A rejection carries a note the practitioner can act on.
- Every console route refuses without the console key, and that refusal has been observed.

## Deployment note

**No migration is needed.** Drafts live in the sqlite LOG_DB on Render's persistent disk, and `init_tables` creates the table on first use, exactly as `dashboard/referrals.py` and `dashboard/ff_match_drafts.py` do. Nothing to apply by hand, and nothing to forget.

**Behavior change to announce:** after this ships, a practitioner's profile edit no longer appears on their public page until Glen approves it. Any practitioner mid-edit when this deploys will have their next save become a draft. Mary should be told before it lands.
