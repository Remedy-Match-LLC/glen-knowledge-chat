"""POST /api/console/sequence-push receives a sequence parsed from the vault.

The vault holds the copy; the server holds the serving copy. This route is the
seam. A push must be safe to repeat, must refuse a malformed sequence outright
rather than store half of it, and must never flip a sequence live as a side
effect of a copy edit.
"""
import importlib
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent

GOOD = {
    "slug": "nurture",
    "name": "Nurture Follow-Up",
    "trigger_kind": "on_contact_created",
    "active": False,
    "steps": [
        {"step_no": 1, "subject": "A quiet check-in", "body_md": "Aloha,", "delay_days": 0},
        {"step_no": 2, "subject": "The quiet multiplier", "body_md": "Body", "delay_days": 4},
    ],
}


def _client(tmp_path, monkeypatch):
    if str(REPO) not in sys.path:
        sys.path.insert(0, str(REPO))
    try:
        import app as appmod
        importlib.reload(appmod)
    except Exception as e:  # noqa: BLE001 — matches the other route tests
        pytest.skip(f"app not importable: {e}")
    monkeypatch.setattr(appmod, "LOG_DB", str(tmp_path / "log.db"), raising=False)
    monkeypatch.setattr(appmod, "CONSOLE_SECRET", "", raising=False)
    import dashboard as _d
    monkeypatch.setattr(_d, "CONSOLE_SECRET", "", raising=False)
    appmod.app.config["TESTING"] = True
    return appmod.app.test_client(), appmod


def _stored(appmod, slug):
    from dashboard import db, sequences
    with db.connect(appmod.LOG_DB) as cx:
        sequences.init_tables(cx)
        return sequences.get(cx, slug)


def test_a_push_stores_the_sequence(tmp_path, monkeypatch):
    c, appmod = _client(tmp_path, monkeypatch)
    r = c.post("/api/console/sequence-push", json=GOOD)
    assert r.status_code == 200
    body = r.get_json()
    assert body["ok"] is True and body["steps"] == 2
    got = _stored(appmod, "nurture")
    assert got["name"] == "Nurture Follow-Up"
    assert [s["subject"] for s in got["steps"]] == \
        ["A quiet check-in", "The quiet multiplier"]


def test_a_repeated_push_is_idempotent(tmp_path, monkeypatch):
    c, appmod = _client(tmp_path, monkeypatch)
    for _ in range(3):
        c.post("/api/console/sequence-push", json=GOOD)
    assert len(_stored(appmod, "nurture")["steps"]) == 2


def test_a_malformed_sequence_is_refused_whole(tmp_path, monkeypatch):
    # Step numbers 1 and 3: the day-4 email would silently not exist.
    c, appmod = _client(tmp_path, monkeypatch)
    bad = dict(GOOD, steps=[
        {"step_no": 1, "subject": "a", "body_md": "x", "delay_days": 0},
        {"step_no": 3, "subject": "c", "body_md": "z", "delay_days": 8}])
    r = c.post("/api/console/sequence-push", json=bad)
    assert r.status_code == 400
    assert "contiguous" in r.get_json()["error"]
    assert _stored(appmod, "nurture") is None, "nothing may be stored on refusal"


def test_backwards_delays_are_refused(tmp_path, monkeypatch):
    c, appmod = _client(tmp_path, monkeypatch)
    bad = dict(GOOD, steps=[
        {"step_no": 1, "subject": "a", "body_md": "x", "delay_days": 5},
        {"step_no": 2, "subject": "b", "body_md": "y", "delay_days": 2}])
    assert c.post("/api/console/sequence-push", json=bad).status_code == 400


def test_a_push_without_a_slug_is_refused(tmp_path, monkeypatch):
    c, _ = _client(tmp_path, monkeypatch)
    assert c.post("/api/console/sequence-push",
                  json=dict(GOOD, slug="")).status_code == 400


def test_a_push_cannot_activate_a_sequence(tmp_path, monkeypatch):
    """Copy edits must not go live by accident.

    `active` is owned by a deliberate console action, not by whatever happens to
    be in a vault file when someone runs the push script. Slice 2 sends nothing,
    but this is the guard that keeps slice 4 from firing early.
    """
    c, appmod = _client(tmp_path, monkeypatch)
    c.post("/api/console/sequence-push", json=dict(GOOD, active=True))
    assert _stored(appmod, "nurture")["active"] is False


def test_a_push_preserves_an_existing_active_flag(tmp_path, monkeypatch):
    c, appmod = _client(tmp_path, monkeypatch)
    c.post("/api/console/sequence-push", json=GOOD)
    from dashboard import db, sequences
    with db.connect(appmod.LOG_DB) as cx:
        sequences.init_tables(cx)
        cx.execute("UPDATE sequences SET active=1 WHERE slug='nurture'")
        cx.commit()
    c.post("/api/console/sequence-push", json=dict(GOOD, name="Renamed"))
    got = _stored(appmod, "nurture")
    assert got["name"] == "Renamed"
    assert got["active"] is True, "a copy push must not deactivate a live sequence"


def test_listing_sequences(tmp_path, monkeypatch):
    c, _ = _client(tmp_path, monkeypatch)
    c.post("/api/console/sequence-push", json=GOOD)
    rows = c.get("/api/console/sequences").get_json()["sequences"]
    assert rows[0]["slug"] == "nurture" and rows[0]["step_count"] == 2
