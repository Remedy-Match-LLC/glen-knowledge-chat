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


def test_submit_with_a_review_field_leaves_it_queued_and_unpublished(client, monkeypatch, tmp_path):
    """Normal case under the beta policy: every field needs review, so the
    draft stays 'submitted' and publish_draft is never called."""
    import sqlite3 as _sqlite3
    from dashboard import practitioner_drafts as _pd

    dbpath = str(tmp_path / "chat_log.db")
    monkeypatch.setattr(appmod, "_practitioner_session_pid", lambda: PID)
    monkeypatch.setattr(appmod, "LOG_DB", dbpath)

    with appmod.db.connect(dbpath) as cx:
        cx.row_factory = _sqlite3.Row
        _pd.init_tables(cx)
        _pd.upsert_draft(cx, PID, {"bio": "hello world"})

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

    with appmod.db.connect(dbpath) as cx:
        cx.row_factory = _sqlite3.Row
        assert _pd.get_draft(cx, PID)["status"] == "submitted"


def test_submit_auto_publishes_when_nothing_needs_review(client, monkeypatch, tmp_path):
    """Policy branch: split_by_policy's consumer. If every field in the draft
    is policied 'auto', submit skips the queue and publishes immediately
    through publish_draft. REVIEW_POLICY is restored in finally so this
    doesn't contaminate later tests -- under the real beta policy every field
    is 'review' and this path is inert."""
    import sqlite3 as _sqlite3
    from dashboard import practitioner_drafts as _pd

    dbpath = str(tmp_path / "chat_log.db")
    monkeypatch.setattr(appmod, "_practitioner_session_pid", lambda: PID)
    monkeypatch.setattr(appmod, "LOG_DB", dbpath)

    with appmod.db.connect(dbpath) as cx:
        cx.row_factory = _sqlite3.Row
        _pd.init_tables(cx)
        _pd.upsert_draft(cx, PID, {"bio": "hello world"})

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


def test_submit_auto_path_is_reachable_through_the_real_save_draft_path(client, monkeypatch, tmp_path):
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


def test_submit_auto_path_reports_failure_when_publish_fails(client, monkeypatch, tmp_path):
    """Mirrors the console approve route (app.py api_console_practitioner_draft_approve):
    a failed publish on the auto path must never report success, and must
    never claim 'approved' status is live when it is not."""
    import sqlite3 as _sqlite3
    from dashboard import practitioner_drafts as _pd

    dbpath = str(tmp_path / "chat_log.db")
    monkeypatch.setattr(appmod, "_practitioner_session_pid", lambda: PID)
    monkeypatch.setattr(appmod, "LOG_DB", dbpath)

    with appmod.db.connect(dbpath) as cx:
        cx.row_factory = _sqlite3.Row
        _pd.init_tables(cx)
        _pd.upsert_draft(cx, PID, {"bio": "hello world"})

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
