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
    """The default IS the queue's meaning: with no ?status the route must ask
    for 'submitted'. Asserted, not assumed -- a default of None would list
    every draft including ones the practitioner is still typing."""
    monkeypatch.setattr(appmod, "_console_key_ok", lambda: True)
    monkeypatch.setattr(appmod, "LOG_DB", str(tmp_path / "chat_log.db"))
    seen = {}

    def _fake_list(cx, status=None, limit=200):
        seen["status"] = status
        return [{"practitioner_id": PID, "status": "submitted",
                 "fields": {"bio": "x"}}]

    monkeypatch.setattr("dashboard.practitioner_drafts.list_by_status", _fake_list)
    r = client.get("/api/console/practitioner-drafts")
    assert r.status_code == 200
    assert r.get_json()["drafts"][0]["practitioner_id"] == PID
    assert seen["status"] == "submitted"


def test_reject_without_a_note_is_a_400(client, monkeypatch):
    monkeypatch.setattr(appmod, "_console_key_ok", lambda: True)
    r = client.post(f"/api/console/practitioner-drafts/{PID}/reject", json={})
    assert r.status_code == 400


def test_approve_succeeds_and_publishes(client, monkeypatch, tmp_path):
    monkeypatch.setattr(appmod, "_console_key_ok", lambda: True)
    monkeypatch.setattr(appmod, "LOG_DB", str(tmp_path / "chat_log.db"))
    monkeypatch.setattr("dashboard.practitioner_drafts.approve",
                        lambda cx, pid, note="": True)
    monkeypatch.setattr("dashboard.practitioner_profile.publish_draft",
                        lambda cx, pid: True)
    r = client.post(f"/api/console/practitioner-drafts/{PID}/approve")
    assert r.status_code == 200
    assert r.get_json()["published"] is True


def test_approve_succeeds_but_publish_fails_reports_failure(client, monkeypatch, tmp_path):
    """The important one: a failed publish must never look like success."""
    monkeypatch.setattr(appmod, "_console_key_ok", lambda: True)
    monkeypatch.setattr(appmod, "LOG_DB", str(tmp_path / "chat_log.db"))
    monkeypatch.setattr("dashboard.practitioner_drafts.approve",
                        lambda cx, pid, note="": True)
    monkeypatch.setattr("dashboard.practitioner_profile.publish_draft",
                        lambda cx, pid: False)
    r = client.post(f"/api/console/practitioner-drafts/{PID}/approve")
    assert r.status_code != 200
    assert r.status_code == 500
    assert r.get_json()["error"] == "publish_failed"


def test_approve_retries_publish_after_a_previous_failure(client, monkeypatch, tmp_path):
    """approve() 409s (already approved from a prior attempt) but the route
    must retry the publish rather than stranding the draft un-retryably."""
    monkeypatch.setattr(appmod, "_console_key_ok", lambda: True)
    monkeypatch.setattr(appmod, "LOG_DB", str(tmp_path / "chat_log.db"))
    monkeypatch.setattr("dashboard.practitioner_drafts.approve",
                        lambda cx, pid, note="": False)
    monkeypatch.setattr("dashboard.practitioner_drafts.get_draft",
                        lambda cx, pid: {"practitioner_id": pid, "status": "approved",
                                         "fields": {"bio": "x"}})
    monkeypatch.setattr("dashboard.practitioner_profile.publish_draft",
                        lambda cx, pid: True)
    r = client.post(f"/api/console/practitioner-drafts/{PID}/approve")
    assert r.status_code == 200
    assert r.get_json()["published"] is True


def test_reject_with_no_submitted_draft_is_a_409(client, monkeypatch, tmp_path):
    monkeypatch.setattr(appmod, "_console_key_ok", lambda: True)
    monkeypatch.setattr(appmod, "LOG_DB", str(tmp_path / "chat_log.db"))
    monkeypatch.setattr("dashboard.practitioner_drafts.reject",
                        lambda cx, pid, note: False)
    r = client.post(f"/api/console/practitioner-drafts/{PID}/reject",
                    json={"note": "needs more detail"})
    assert r.status_code == 409


# --- practitioner-side submit action ---------------------------------------
#
# PRACTITIONER_REVIEW_GATE_ENABLED is the rollout flag and defaults OFF, in
# which case a save publishes immediately (pre-feature behavior). Every test
# below that means to exercise the QUEUE therefore turns it ON explicitly --
# otherwise it would be the flag, not the policy, producing the result, and
# the test would pass for the wrong reason.


@pytest.fixture
def gate_on(monkeypatch):
    monkeypatch.setenv("PRACTITIONER_REVIEW_GATE_ENABLED", "1")


@pytest.fixture
def gate_off(monkeypatch):
    monkeypatch.delenv("PRACTITIONER_REVIEW_GATE_ENABLED", raising=False)


def _seed_draft(dbpath, pid, fields):
    """Create the real table and a real draft row, through the real writer."""
    import sqlite3 as _sqlite3
    from dashboard import practitioner_drafts as _pd
    with appmod.db.connect(dbpath) as cx:
        cx.row_factory = _sqlite3.Row
        _pd.init_tables(cx)
        _pd.upsert_draft(cx, pid, fields)


def _spy_on_submit(monkeypatch):
    """Record the pid the route passes to submit(), then run the REAL submit.
    A spy that replaces the behavior would make the assertions below vacuous."""
    from dashboard import practitioner_drafts as _pd
    seen = {}
    real_submit = _pd.submit

    def _spy(cx, pid):
        seen["pid"] = pid
        return real_submit(cx, pid)

    monkeypatch.setattr("dashboard.practitioner_drafts.submit", _spy)
    return seen


def _draft_status(dbpath, pid):
    import sqlite3 as _sqlite3
    from dashboard import practitioner_drafts as _pd
    with appmod.db.connect(dbpath) as cx:
        cx.row_factory = _sqlite3.Row
        d = _pd.get_draft(cx, pid)
    return d and d["status"]


def test_submit_requires_a_signed_in_practitioner(client, monkeypatch):
    monkeypatch.setattr(appmod, "_practitioner_session_pid", lambda: None)
    r = client.post("/api/practitioner/profile/submit")
    assert r.status_code == 401


def test_submit_moves_the_practitioners_own_draft(client, monkeypatch, tmp_path, gate_on):
    """The doubles here are FAITHFUL on purpose: the table really exists and a
    real draft row is really seeded. An earlier version no-op'd init_tables,
    so the route's get_draft hit a missing table and the test passed through
    the fail-closed `except db.OperationalError` branch -- it would have kept
    passing with the policy logic deleted."""
    dbpath = str(tmp_path / "chat_log.db")
    monkeypatch.setattr(appmod, "_practitioner_session_pid", lambda: PID)
    monkeypatch.setattr(appmod, "LOG_DB", dbpath)
    _seed_draft(dbpath, PID, {"bio": "hello world"})

    seen = _spy_on_submit(monkeypatch)
    r = client.post("/api/practitioner/profile/submit")
    assert r.status_code == 200 and r.get_json()["ok"] is True
    assert seen["pid"] == PID
    assert _draft_status(dbpath, PID) == "submitted"


def test_submit_uses_the_session_pid_not_a_supplied_one(client, monkeypatch, tmp_path, gate_on):
    """A practitioner must never be able to submit someone else's draft."""
    dbpath = str(tmp_path / "chat_log.db")
    monkeypatch.setattr(appmod, "_practitioner_session_pid", lambda: PID)
    monkeypatch.setattr(appmod, "LOG_DB", dbpath)
    _seed_draft(dbpath, PID, {"bio": "hello world"})

    seen = _spy_on_submit(monkeypatch)
    client.post("/api/practitioner/profile/submit",
                json={"practitioner_id": "99999999-9999-9999-9999-999999999999"})
    assert seen["pid"] == PID
    assert _draft_status(dbpath, PID) == "submitted"


def test_submit_with_a_review_field_leaves_it_queued_and_unpublished(
        client, monkeypatch, tmp_path, gate_on):
    """Normal case with the gate ON: every field needs review under the beta
    policy, so the draft stays 'submitted' and publish_draft is never called."""
    dbpath = str(tmp_path / "chat_log.db")
    monkeypatch.setattr(appmod, "_practitioner_session_pid", lambda: PID)
    monkeypatch.setattr(appmod, "LOG_DB", dbpath)
    _seed_draft(dbpath, PID, {"bio": "hello world"})

    published = {"called": False}
    monkeypatch.setattr(
        "dashboard.practitioner_profile.publish_draft",
        lambda cx, pid: published.__setitem__("called", True) or True)

    r = client.post("/api/practitioner/profile/submit")
    assert r.status_code == 200
    body = r.get_json()
    assert body["ok"] is True
    assert body["status"] == "submitted"
    assert published["called"] is False
    assert _draft_status(dbpath, PID) == "submitted"


# --- the rollout flag ------------------------------------------------------

def test_gate_off_publishes_immediately(client, monkeypatch, tmp_path, gate_off):
    """Flag OFF is the DEFAULT and must be today's behavior: a practitioner's
    save reaches her public page with no human in the loop. Same _pd.approve +
    publish_draft calls the auto-policy path uses -- one publish path, one
    writer."""
    dbpath = str(tmp_path / "chat_log.db")
    monkeypatch.setattr(appmod, "_practitioner_session_pid", lambda: PID)
    monkeypatch.setattr(appmod, "LOG_DB", dbpath)
    _seed_draft(dbpath, PID, {"bio": "hello world"})

    seen = {}
    monkeypatch.setattr(
        "dashboard.practitioner_profile.publish_draft",
        lambda cx, pid: seen.setdefault("published_pid", pid) or True)

    r = client.post("/api/practitioner/profile/submit")
    assert r.status_code == 200
    body = r.get_json()
    assert body["status"] == "approved" and body["published"] is True
    assert seen["published_pid"] == PID
    assert _draft_status(dbpath, PID) == "approved"


def test_gate_on_queues_the_same_draft(client, monkeypatch, tmp_path, gate_on):
    """The ONLY difference from the test above is the flag."""
    dbpath = str(tmp_path / "chat_log.db")
    monkeypatch.setattr(appmod, "_practitioner_session_pid", lambda: PID)
    monkeypatch.setattr(appmod, "LOG_DB", dbpath)
    _seed_draft(dbpath, PID, {"bio": "hello world"})

    published = {"called": False}
    monkeypatch.setattr(
        "dashboard.practitioner_profile.publish_draft",
        lambda cx, pid: published.__setitem__("called", True) or True)

    r = client.post("/api/practitioner/profile/submit")
    assert r.get_json()["status"] == "submitted"
    assert published["called"] is False


@pytest.mark.parametrize("gate", ["1", ""])
def test_an_empty_draft_always_needs_a_human(client, monkeypatch, tmp_path, gate):
    """I1: split_by_policy({}) returns ({}, {}), so an empty field set used to
    look exactly like "nothing needs review" -- and the auto path published
    _write_live_profile(pid, {}), setting every column to its default and
    stamping provenance with no human involved. Emptiness outranks BOTH the
    policy and the flag, so this holds in either flag state."""
    monkeypatch.setenv("PRACTITIONER_REVIEW_GATE_ENABLED", gate)
    dbpath = str(tmp_path / "chat_log.db")
    monkeypatch.setattr(appmod, "_practitioner_session_pid", lambda: PID)
    monkeypatch.setattr(appmod, "LOG_DB", dbpath)
    _seed_draft(dbpath, PID, {})

    published = {"called": False}
    monkeypatch.setattr(
        "dashboard.practitioner_profile.publish_draft",
        lambda cx, pid: published.__setitem__("called", True) or True)

    r = client.post("/api/practitioner/profile/submit")
    assert r.status_code == 200
    assert r.get_json()["status"] == "submitted"
    assert published["called"] is False, "an empty draft must never auto-publish"


# --- the policy branch -----------------------------------------------------

def test_submit_auto_publishes_when_nothing_needs_review(
        client, monkeypatch, tmp_path, gate_on):
    """Policy branch, with the gate ON so it is the POLICY doing the work and
    not the flag. If every field in the draft is policied 'auto', submit skips
    the queue and publishes immediately through publish_draft. REVIEW_POLICY is
    restored in finally -- under the real beta policy every field is 'review'
    and this path is inert."""
    from dashboard import practitioner_drafts as _pd

    dbpath = str(tmp_path / "chat_log.db")
    monkeypatch.setattr(appmod, "_practitioner_session_pid", lambda: PID)
    monkeypatch.setattr(appmod, "LOG_DB", dbpath)
    _seed_draft(dbpath, PID, {"bio": "hello world"})

    seen = {}
    monkeypatch.setattr(
        "dashboard.practitioner_profile.publish_draft",
        lambda cx, pid: seen.setdefault("published_pid", pid) or True)

    original_policy = dict(_pd.REVIEW_POLICY)
    _pd.REVIEW_POLICY["bio"] = "auto"
    try:
        r = client.post("/api/practitioner/profile/submit")
    finally:
        _pd.REVIEW_POLICY.clear()
        _pd.REVIEW_POLICY.update(original_policy)

    assert r.status_code == 200
    body = r.get_json()
    assert body["ok"] is True
    assert body["published"] is True
    assert seen["published_pid"] == PID


def test_submit_auto_path_is_reachable_through_the_real_save_draft_path(
        client, monkeypatch, tmp_path, gate_on):
    """The auto branch must be reachable via the REAL production write path
    (practitioner_profile.save_draft), not a hand-built fields dict.
    save_draft persists 'city' and 'state' separately -- there is no
    'location' key in a real draft -- so this is the test that would have
    caught REVIEW_POLICY being keyed on the wrong field names (it was, and
    the auto path could never fire on real data). REVIEW_POLICY is restored
    in finally."""
    import sqlite3 as _sqlite3
    from dashboard import practitioner_drafts as _pd
    from dashboard import practitioner_profile as _pp

    dbpath = str(tmp_path / "chat_log.db")
    monkeypatch.setattr(appmod, "_practitioner_session_pid", lambda: PID)
    monkeypatch.setattr(appmod, "LOG_DB", dbpath)

    with appmod.db.connect(dbpath) as cx:
        cx.row_factory = _sqlite3.Row
        _pp.save_draft(cx, PID, {
            "bio": "Hello, I'm a practitioner.",
            "services": ["Coaching"],
            "city": "Hilo",
            "state": "HI",
            "photo_url": "https://example.com/p.jpg",
            "accepting_clients": True,
        })

    seen = {}
    monkeypatch.setattr(
        "dashboard.practitioner_profile.publish_draft",
        lambda cx, pid: seen.setdefault("published_pid", pid) or True)

    original_policy = dict(_pd.REVIEW_POLICY)
    for field in _pd.REVIEW_POLICY:
        _pd.REVIEW_POLICY[field] = "auto"
    try:
        r = client.post("/api/practitioner/profile/submit")
    finally:
        _pd.REVIEW_POLICY.clear()
        _pd.REVIEW_POLICY.update(original_policy)

    assert r.status_code == 200
    body = r.get_json()
    assert body["ok"] is True
    assert body["status"] == "approved"
    assert body["published"] is True
    assert seen["published_pid"] == PID


def test_submit_auto_path_reports_failure_when_publish_fails(
        client, monkeypatch, tmp_path, gate_on):
    """Mirrors the console approve route (app.py api_console_practitioner_draft_approve):
    a failed publish on the auto path must never report success, and must
    never claim 'approved' status is live when it is not."""
    from dashboard import practitioner_drafts as _pd

    dbpath = str(tmp_path / "chat_log.db")
    monkeypatch.setattr(appmod, "_practitioner_session_pid", lambda: PID)
    monkeypatch.setattr(appmod, "LOG_DB", dbpath)
    _seed_draft(dbpath, PID, {"bio": "hello world"})

    monkeypatch.setattr(
        "dashboard.practitioner_profile.publish_draft",
        lambda cx, pid: False)

    original_policy = dict(_pd.REVIEW_POLICY)
    _pd.REVIEW_POLICY["bio"] = "auto"
    try:
        r = client.post("/api/practitioner/profile/submit")
    finally:
        _pd.REVIEW_POLICY.clear()
        _pd.REVIEW_POLICY.update(original_policy)

    assert r.status_code == 500
    body = r.get_json()
    assert body["ok"] is False
    assert body["error"] == "publish_failed"


# --- C1: a settings save IS a submit ---------------------------------------

def test_settings_save_submits_and_the_queue_shows_it(client, monkeypatch, tmp_path, gate_on):
    """C1, end to end. /api/practitioner/profile/submit has no caller in the
    UI -- the settings page posts only to /api/practitioner/settings. Without
    this wiring the draft sat at status='draft' forever, the console queue
    (which defaults to status='submitted') was permanently empty, nothing ever
    published, and the practitioner was told "Settings saved" while her page
    never changed.

    Asserts the real chain: settings POST -> draft submitted -> visible in the
    DEFAULT console queue view, with nothing published."""
    dbpath = str(tmp_path / "chat_log.db")
    monkeypatch.setattr(appmod, "_practitioner_session_pid", lambda: PID)
    monkeypatch.setattr(appmod, "_console_key_ok", lambda: True)
    monkeypatch.setattr(appmod, "LOG_DB", dbpath)

    published = {"called": False}
    monkeypatch.setattr(
        "dashboard.practitioner_profile.publish_draft",
        lambda cx, pid: published.__setitem__("called", True) or True)

    r = client.post("/api/practitioner/settings", json={
        "profile": {"bio": "I help people see better.", "city": "Hilo",
                    "state": "HI", "services": ["Coaching"],
                    "accepting_clients": True}})
    assert r.status_code == 200, r.get_data(as_text=True)
    assert r.get_json()["profile_status"] == "submitted"
    assert published["called"] is False

    assert _draft_status(dbpath, PID) == "submitted"

    q = client.get("/api/console/practitioner-drafts")   # default: submitted
    assert q.status_code == 200
    queued = q.get_json()["drafts"]
    assert [d["practitioner_id"] for d in queued] == [PID]
    assert queued[0]["fields"]["bio"] == "I help people see better."


def test_settings_save_publishes_when_the_gate_is_off(
        client, monkeypatch, tmp_path, gate_off):
    """Flag OFF: the same save reaches her public page, as it does today."""
    dbpath = str(tmp_path / "chat_log.db")
    monkeypatch.setattr(appmod, "_practitioner_session_pid", lambda: PID)
    monkeypatch.setattr(appmod, "LOG_DB", dbpath)

    seen = {}
    monkeypatch.setattr(
        "dashboard.practitioner_profile.publish_draft",
        lambda cx, pid: seen.setdefault("published_pid", pid) or True)

    r = client.post("/api/practitioner/settings", json={
        "profile": {"bio": "I help people see better.", "city": "Hilo",
                    "state": "HI"}})
    assert r.status_code == 200, r.get_data(as_text=True)
    assert r.get_json()["profile_status"] == "approved"
    assert seen["published_pid"] == PID


def test_a_settings_save_without_a_profile_never_submits(client, monkeypatch, tmp_path, gate_on):
    """Saving pricing/branding alone must not push a half-typed draft into
    Glen's queue."""
    dbpath = str(tmp_path / "chat_log.db")
    monkeypatch.setattr(appmod, "_practitioner_session_pid", lambda: PID)
    monkeypatch.setattr(appmod, "LOG_DB", dbpath)
    _seed_draft(dbpath, PID, {"bio": "still typing"})

    r = client.post("/api/practitioner/settings", json={"branding": {}})
    assert r.status_code == 200
    assert "profile_status" not in r.get_json()
    assert _draft_status(dbpath, PID) == "draft"


def test_approve_retry_records_a_supplied_note(client, monkeypatch, tmp_path):
    """Minor: on a RETRY, _pd.approve returns False without running its UPDATE,
    so the note supplied with the retry was silently dropped."""
    import sqlite3 as _sqlite3
    from dashboard import practitioner_drafts as _pd

    dbpath = str(tmp_path / "chat_log.db")
    monkeypatch.setattr(appmod, "_console_key_ok", lambda: True)
    monkeypatch.setattr(appmod, "LOG_DB", dbpath)

    _seed_draft(dbpath, PID, {"bio": "x"})
    with appmod.db.connect(dbpath) as cx:
        cx.row_factory = _sqlite3.Row
        _pd.submit(cx, PID)
        _pd.approve(cx, PID)          # a prior attempt approved but failed to publish

    monkeypatch.setattr("dashboard.practitioner_profile.publish_draft",
                        lambda cx, pid: True)
    r = client.post(f"/api/console/practitioner-drafts/{PID}/approve",
                    json={"note": "published on the retry"})
    assert r.status_code == 200

    with appmod.db.connect(dbpath) as cx:
        cx.row_factory = _sqlite3.Row
        assert _pd.get_draft(cx, PID)["review_note"] == "published on the retry"
