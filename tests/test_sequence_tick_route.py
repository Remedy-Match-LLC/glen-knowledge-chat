"""The cron endpoint that drives one tick.

Gated like the other cron routes. The property worth pinning is that it is a
no-op while every sequence is inactive, which is what makes it safe to register
the cron in slice 3 rather than waiting for slice 4.
"""
import importlib
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent


def _client(tmp_path, monkeypatch, secret="cron-secret"):
    if str(REPO) not in sys.path:
        sys.path.insert(0, str(REPO))
    monkeypatch.setenv("CRON_SECRET", secret)
    try:
        import app as appmod
        importlib.reload(appmod)
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"app not importable: {e}")
    monkeypatch.setattr(appmod, "LOG_DB", str(tmp_path / "log.db"), raising=False)
    appmod.app.config["TESTING"] = True
    return appmod.app.test_client()


def test_without_the_secret_it_is_unauthorized(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch)
    assert c.post("/api/cron/sequence-tick").status_code == 401


def test_a_wrong_secret_is_unauthorized(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch)
    r = c.post("/api/cron/sequence-tick", headers={"X-Cron-Secret": "nope"})
    assert r.status_code == 401


def test_a_tick_with_no_sequences_is_a_clean_noop(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch)
    r = c.post("/api/cron/sequence-tick", headers={"X-Cron-Secret": "cron-secret"})
    assert r.status_code == 200
    b = r.get_json()
    assert b["ok"] is True and b["sent"] == 0 and b["failed"] == 0


def test_an_inactive_sequence_sends_nothing(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch)
    import app as appmod
    from dashboard import db, sequences
    with db.connect(appmod.LOG_DB) as cx:
        sequences.init_tables(cx)
        sequences.upsert(cx, slug="n", name="N", trigger_kind="manual",
                         steps=[{"step_no": 1, "subject": "s", "body_md": "b",
                                 "delay_days": 0}])
        sequences.enroll(cx, "n", "a@b.com")
    r = c.post("/api/cron/sequence-tick", headers={"X-Cron-Secret": "cron-secret"})
    assert r.get_json()["sent"] == 0


def test_dry_run_reports_without_claiming(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch)
    import app as appmod
    from dashboard import db, sequences
    with db.connect(appmod.LOG_DB) as cx:
        sequences.init_tables(cx)
        sequences.upsert(cx, slug="n", name="N", trigger_kind="manual",
                         steps=[{"step_no": 1, "subject": "s", "body_md": "b",
                                 "delay_days": 0}])
        sequences.set_active(cx, "n", True)
        sequences.enroll(cx, "n", "a@b.com")
    r = c.post("/api/cron/sequence-tick?dry_run=1",
               headers={"X-Cron-Secret": "cron-secret"})
    b = r.get_json()
    assert b["dry_run"] is True and b["would_send"] == 1 and b["sent"] == 0
    with db.connect(appmod.LOG_DB) as cx:
        assert cx.execute("SELECT COUNT(*) FROM sequence_sends").fetchone()[0] == 0
