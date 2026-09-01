"""Explicit enrollment and activation, the two producers slice 4 wires.

Both are deliberately dumb: they take exactly the addresses given and act on
exactly the slug given. Nothing here selects an audience, because an endpoint
that picks its own recipients is one bad query away from mailing everyone.
"""
import importlib
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent

STEPS = [{"step_no": 1, "subject": "s1", "body_md": "b1", "delay_days": 0},
         {"step_no": 2, "subject": "s2", "body_md": "b2", "delay_days": 4}]


def _client(tmp_path, monkeypatch):
    if str(REPO) not in sys.path:
        sys.path.insert(0, str(REPO))
    try:
        import app as appmod
        importlib.reload(appmod)
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"app not importable: {e}")
    monkeypatch.setattr(appmod, "LOG_DB", str(tmp_path / "log.db"), raising=False)
    monkeypatch.setattr(appmod, "CONSOLE_SECRET", "", raising=False)
    import dashboard as _d
    monkeypatch.setattr(_d, "CONSOLE_SECRET", "", raising=False)
    appmod.app.config["TESTING"] = True
    from dashboard import db, sequences
    with db.connect(appmod.LOG_DB) as cx:
        sequences.init_tables(cx)
        sequences.upsert(cx, slug="pilot", name="Pilot", trigger_kind="manual",
                         steps=STEPS)
    return appmod.app.test_client(), appmod


def _enrollments(appmod, slug="pilot"):
    from dashboard import db
    with db.connect(appmod.LOG_DB) as cx:
        return cx.execute("SELECT email, status FROM sequence_enrollments "
                          "WHERE slug=? ORDER BY email", (slug,)).fetchall()


def test_enrolling_one_address(tmp_path, monkeypatch):
    c, appmod = _client(tmp_path, monkeypatch)
    r = c.post("/api/console/sequence-enroll",
               json={"slug": "pilot", "emails": ["A@B.com"]})
    assert r.status_code == 200 and r.get_json()["enrolled"] == 1
    assert _enrollments(appmod) == [("a@b.com", "active")]


def test_enrolling_is_idempotent(tmp_path, monkeypatch):
    c, appmod = _client(tmp_path, monkeypatch)
    for _ in range(3):
        c.post("/api/console/sequence-enroll",
               json={"slug": "pilot", "emails": ["a@b.com"]})
    assert len(_enrollments(appmod)) == 1


def test_enrolling_an_unknown_sequence_is_refused(tmp_path, monkeypatch):
    c, appmod = _client(tmp_path, monkeypatch)
    r = c.post("/api/console/sequence-enroll",
               json={"slug": "nope", "emails": ["a@b.com"]})
    assert r.status_code == 404
    assert _enrollments(appmod, "nope") == []


def test_an_empty_address_list_is_refused(tmp_path, monkeypatch):
    c, _ = _client(tmp_path, monkeypatch)
    assert c.post("/api/console/sequence-enroll",
                  json={"slug": "pilot", "emails": []}).status_code == 400


def test_enrollment_is_capped_so_a_slip_cannot_mail_everyone(tmp_path, monkeypatch):
    c, appmod = _client(tmp_path, monkeypatch)
    r = c.post("/api/console/sequence-enroll",
               json={"slug": "pilot",
                     "emails": [f"u{i}@b.com" for i in range(51)]})
    assert r.status_code == 400
    assert "50" in r.get_json()["error"]
    assert _enrollments(appmod) == [], "nothing may be enrolled on refusal"


def test_a_suppressed_address_is_not_enrolled(tmp_path, monkeypatch):
    c, appmod = _client(tmp_path, monkeypatch)
    from dashboard import db, email_suppression as es
    with db.connect(appmod.LOG_DB) as cx:
        es.init_table(cx)
        es.add_optout(cx, "gone@b.com", "unsubscribe-link:global")
    r = c.post("/api/console/sequence-enroll",
               json={"slug": "pilot", "emails": ["gone@b.com", "ok@b.com"]})
    assert r.get_json() == {"ok": True, "enrolled": 1, "suppressed": 1,
                            "slug": "pilot"}
    assert _enrollments(appmod) == [("ok@b.com", "active")]


def test_activation_is_its_own_endpoint(tmp_path, monkeypatch):
    c, appmod = _client(tmp_path, monkeypatch)
    from dashboard import db, sequences
    with db.connect(appmod.LOG_DB) as cx:
        assert sequences.get(cx, "pilot")["active"] is False
    r = c.post("/api/console/sequence-activate",
               json={"slug": "pilot", "active": True})
    assert r.status_code == 200
    with db.connect(appmod.LOG_DB) as cx:
        assert sequences.get(cx, "pilot")["active"] is True


def test_deactivation_works_too(tmp_path, monkeypatch):
    c, appmod = _client(tmp_path, monkeypatch)
    c.post("/api/console/sequence-activate", json={"slug": "pilot", "active": True})
    c.post("/api/console/sequence-activate", json={"slug": "pilot", "active": False})
    from dashboard import db, sequences
    with db.connect(appmod.LOG_DB) as cx:
        assert sequences.get(cx, "pilot")["active"] is False


def test_activating_an_unknown_sequence_is_refused(tmp_path, monkeypatch):
    c, _ = _client(tmp_path, monkeypatch)
    assert c.post("/api/console/sequence-activate",
                  json={"slug": "nope", "active": True}).status_code == 404
