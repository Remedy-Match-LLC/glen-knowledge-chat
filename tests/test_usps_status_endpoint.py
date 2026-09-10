"""The USPS status sync's Render-side entry: /api/cron/usps-status.

The parser and the sweep's guards are pinned in test_usps_status.py. These pin the
web boundary: auth, the operator-supplied window, dry-run, and that the endpoint
hands the sweep the app's own order-advancing function rather than a stub of its
own. That last one matters — the whole point of this endpoint is to reach
_advance_orders_by_tracking_status, which is the only code that moves an order
card out of new/packed.
"""

import importlib
import sqlite3
import sys
from pathlib import Path

import pytest

from dashboard import usps_status as US


@pytest.fixture
def db_file(tmp_path):
    path = str(tmp_path / "chat_log.db")
    with sqlite3.connect(path) as cx:
        cx.execute("CREATE TABLE IF NOT EXISTS oauth_tokens (name TEXT PRIMARY KEY, "
                   "token_json TEXT NOT NULL, updated_at TEXT NOT NULL)")
        cx.commit()
    return path


def _app():
    repo = Path(__file__).resolve().parent.parent
    if str(repo) not in sys.path:
        sys.path.insert(0, str(repo))
    try:
        return importlib.import_module("app")
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"app not importable: {e}")


class _FakeGmail:
    def users(self):
        return self

    def messages(self):
        return self

    def list(self, **kwargs):
        return self

    def getProfile(self, **kwargs):
        return self

    def execute(self):
        return {"messages": [], "emailAddress": "drglenswartwout@gmail.com"}


def _stub_token(monkeypatch):
    class _Loaded:
        creds = object()
    monkeypatch.setattr("dashboard.gmail_token.load_gmail_credentials",
                        lambda *a, **k: _Loaded())
    monkeypatch.setattr("dashboard.gmail_token.persist_refreshed_credentials",
                        lambda *a, **k: False)
    monkeypatch.setattr("dashboard.gmail_token.record_ok", lambda *a, **k: None)
    monkeypatch.setattr("googleapiclient.discovery.build",
                        lambda *a, **k: _FakeGmail())


def _capture_sweep(monkeypatch):
    seen = {}

    def fake(cx, service, **kwargs):
        seen.update(kwargs)
        seen["service"] = service
        return {"mode": "DRY-RUN" if kwargs.get("dry_run") else "LIVE",
                "mailbox": "drglenswartwout@gmail.com", "days": kwargs["days"],
                "emails": 0, "parcels": 0, "acted": 0, "cards_reported": 0,
                "would_act": 0,
                "pre_transit_held": 0, "unknown_parcels": 0,
                "unparsed_emails": 0, "errors": 0, "first_error": None}
    monkeypatch.setattr(US, "run_status_sweep", fake)
    return seen


def test_endpoint_rejects_a_wrong_secret(db_file, monkeypatch):
    app_module = _app()
    monkeypatch.setattr(app_module, "LOG_DB", db_file)
    monkeypatch.setenv("CRON_SECRET", "s3cret")

    r = app_module.app.test_client().post(
        "/api/cron/usps-status", headers={"X-Cron-Secret": "wrong"})

    assert r.status_code == 401


def test_endpoint_rejects_when_no_secret_is_configured(db_file, monkeypatch):
    """An absent gating secret must close the gate, not open it."""
    app_module = _app()
    monkeypatch.setattr(app_module, "LOG_DB", db_file)
    monkeypatch.delenv("CRON_SECRET", raising=False)
    monkeypatch.delenv("CONSOLE_SECRET", raising=False)

    r = app_module.app.test_client().post(
        "/api/cron/usps-status", headers={"X-Cron-Secret": ""})

    assert r.status_code == 401


def test_endpoint_passes_the_apps_own_advance_function(db_file, monkeypatch):
    """The sweep must be handed _advance_orders_by_tracking_status itself. If this
    ever became a local stub the endpoint would report success and move nothing."""
    app_module = _app()
    monkeypatch.setattr(app_module, "LOG_DB", db_file)
    monkeypatch.setenv("CRON_SECRET", "s3cret")
    _stub_token(monkeypatch)
    seen = _capture_sweep(monkeypatch)

    r = app_module.app.test_client().post(
        "/api/cron/usps-status", headers={"X-Cron-Secret": "s3cret"})

    assert r.status_code == 200
    assert seen["advance"] is app_module._advance_orders_by_tracking_status
    # Without a logger the sweep's per-parcel reasons never reach Render's logs,
    # which is how the first live run reported errors=1 with no cause.
    assert callable(seen.get("log"))


def test_endpoint_defaults_to_a_three_day_window_and_is_live(db_file, monkeypatch):
    """days=3 is the live contract: a scan email can land a day or two after the
    event. Unlike the CNS watcher a wide window mails nobody, so this is safe."""
    app_module = _app()
    monkeypatch.setattr(app_module, "LOG_DB", db_file)
    monkeypatch.setenv("CRON_SECRET", "s3cret")
    _stub_token(monkeypatch)
    seen = _capture_sweep(monkeypatch)

    r = app_module.app.test_client().post(
        "/api/cron/usps-status", headers={"X-Cron-Secret": "s3cret"})

    assert r.status_code == 200
    assert seen["days"] == 3
    assert seen["dry_run"] is False


@pytest.mark.parametrize("given,expected", [
    ("0", 1), ("9999", 90), ("abc", 3), ("14", 14),
])
def test_endpoint_clamps_the_window(db_file, monkeypatch, given, expected):
    """?days is operator input. Unbounded, it would scan the whole mailbox."""
    app_module = _app()
    monkeypatch.setattr(app_module, "LOG_DB", db_file)
    monkeypatch.setenv("CRON_SECRET", "s3cret")
    _stub_token(monkeypatch)
    seen = _capture_sweep(monkeypatch)

    app_module.app.test_client().post(
        f"/api/cron/usps-status?days={given}", headers={"X-Cron-Secret": "s3cret"})

    assert seen["days"] == expected


def test_dry_run_reaches_the_sweep(db_file, monkeypatch):
    app_module = _app()
    monkeypatch.setattr(app_module, "LOG_DB", db_file)
    monkeypatch.setenv("CRON_SECRET", "s3cret")
    _stub_token(monkeypatch)
    seen = _capture_sweep(monkeypatch)

    r = app_module.app.test_client().post(
        "/api/cron/usps-status?dry_run=1", headers={"X-Cron-Secret": "s3cret"})

    assert r.status_code == 200
    assert seen["dry_run"] is True
    assert r.get_json()["mode"] == "DRY-RUN"


def test_a_missing_gmail_token_reports_it_rather_than_looking_healthy(
        db_file, monkeypatch):
    """A token failure must not return ok:true with zero parcels. That is the
    shape that hid the 60-day Click-N-Ship outage."""
    app_module = _app()
    monkeypatch.setattr(app_module, "LOG_DB", db_file)
    monkeypatch.setenv("CRON_SECRET", "s3cret")
    from dashboard import gmail_token as gt

    def boom(*a, **k):
        raise gt.GmailTokenMissing("no token")
    monkeypatch.setattr(gt, "load_gmail_credentials", boom)
    monkeypatch.setattr(gt, "should_send_alert", lambda *a, **k: False)

    r = app_module.app.test_client().post(
        "/api/cron/usps-status", headers={"X-Cron-Secret": "s3cret"})

    assert r.status_code == 500
    payload = r.get_json()
    assert payload["ok"] is False
    assert payload["token_missing"] is True
