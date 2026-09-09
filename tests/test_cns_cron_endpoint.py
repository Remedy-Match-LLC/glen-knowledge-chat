"""The CNS tracking watcher's Render-side entry: run_watch() and /api/cron/cns-tracking.

This job spent ~60 days crash-looping on Glen's Mac while looking scheduled, because
its config named paths that only exist on Render. These tests pin the two things that
made that failure invisible: the runner takes an injected Gmail service (so the web
container never needs a token file), and it reports WHICH mailbox it read (so a
wrong-account token cannot masquerade as an empty inbox).
"""

import base64
import importlib
import json
import sqlite3
import sys
from pathlib import Path

import pytest

from dashboard.tracking import init_tracking_schema, shipment_exists
import cns_tracking_watcher as cns


CONFIRMATION = """
<a href="x?orderUUID=019e3cbe-bd74-7a99-8876-2269d7f83860">order</a>
<td class="item-contents-column"><table><tr><td>
  <p class="bold">Priority Mail&#174;</p>
  <a href="x?tLabelsB08522452499405530109355381515251"> 4208522452499405530109355381515251</a>
  <p class="bold">Shipped To:</p>
  <p class="pt-5">Cyndi O'Brien</p>
  <p class="pt-5">1016 W CHICAGO CT</p>
  <p class="pt-5">CHANDLER AZ 85224-5249 US</p>
</td></tr></table></td><td class="item-total-column"><p>$11.99</p></td>
"""

TRACKING = "9405530109355381515251"


class _FakeMessages:
    def __init__(self, svc):
        self.svc = svc

    def list(self, userId=None, q=None, maxResults=None):
        self.svc.queries.append({"q": q, "max": maxResults})
        # The harvest search reuses this same endpoint with a name query; only the
        # Click-N-Ship query should yield the confirmation.
        ids = [{"id": "m1"}] if "noreply-ecns" in (q or "") else []
        return _Exec({"messages": ids})

    def get(self, userId=None, id=None, format=None):
        html = base64.urlsafe_b64encode(CONFIRMATION.encode()).decode()
        return _Exec({"payload": {"mimeType": "text/html", "body": {"data": html},
                                  "headers": []}})


class _Exec:
    def __init__(self, value):
        self.value = value

    def execute(self):
        return self.value


class _FakeDrafts:
    def __init__(self, svc):
        self.svc = svc

    def create(self, userId=None, body=None):
        self.svc.drafts.append(body)
        return _Exec({"id": f"draft_{len(self.svc.drafts)}"})


class _FakeUsers:
    def __init__(self, svc):
        self.svc = svc

    def getProfile(self, userId=None):
        return _Exec({"emailAddress": self.svc.mailbox})

    def messages(self):
        return _FakeMessages(self.svc)

    def drafts(self):
        return _FakeDrafts(self.svc)


class FakeGmail:
    def __init__(self, mailbox="drglenswartwout@gmail.com"):
        self.mailbox = mailbox
        self.queries = []
        self.drafts = []

    def users(self):
        return _FakeUsers(self)


@pytest.fixture
def db_file(tmp_path):
    p = str(tmp_path / "chat_log.db")
    with sqlite3.connect(p) as cx:
        init_tracking_schema(cx)
        cx.commit()
    return p


def test_injected_service_is_used_and_no_token_file_is_read(db_file, monkeypatch):
    """The web container has no token file. run_watch must never reach for one."""
    def _boom(*a, **k):
        raise AssertionError("run_watch built its own service instead of using the "
                             "injected one — the Render container has no token file")
    monkeypatch.setattr(cns, "gmail_service", _boom)
    svc = FakeGmail()

    summary = cns.run_watch(days=3, db_file=db_file, service=svc)

    assert summary["emails"] == 1
    assert summary["shipments"] == 1


def test_summary_names_the_mailbox_it_actually_read(db_file):
    """A wrong-account token and a genuinely empty inbox both report zero
    confirmations. Only the mailbox tells them apart."""
    svc = FakeGmail(mailbox="someone-else@example.com")

    summary = cns.run_watch(days=1, db_file=db_file, service=svc)

    assert summary["mailbox"] == "someone-else@example.com"


def test_days_reaches_the_gmail_query(db_file):
    """The cron passes days=1 deliberately. If it never reaches the query, the job
    would rescan weeks of shipments and mail people about parcels already delivered."""
    svc = FakeGmail()

    cns.run_watch(days=1, db_file=db_file, service=svc)

    cns_query = next(q for q in svc.queries if "noreply-ecns" in (q["q"] or ""))
    assert "newer_than:1d" in cns_query["q"]


def test_dry_run_drafts_nothing_and_records_nothing(db_file):
    svc = FakeGmail()

    summary = cns.run_watch(live=False, days=1, db_file=db_file, service=svc)

    assert summary["dry_run"] is True
    assert svc.drafts == []
    with sqlite3.connect(db_file) as cx:
        assert not shipment_exists(cx, TRACKING)


def test_live_run_records_the_shipment_and_the_rerun_skips_it(db_file, monkeypatch):
    """Idempotence is what makes a 15-minute cadence safe. Without it the same
    customer is emailed four times an hour."""
    # GHL upsert + send are out of scope here; stub them so the test exercises the
    # record-and-skip path rather than the network.
    monkeypatch.setattr(cns, "make_persist_contact", lambda: None)
    monkeypatch.setattr(cns, "make_ghl_send_fn", lambda: (lambda **k: "msg_1"))
    svc = FakeGmail()

    first = cns.run_watch(live=True, days=1, db_file=db_file, service=svc)
    assert first["shipments"] == 1
    with sqlite3.connect(db_file) as cx:
        assert shipment_exists(cx, TRACKING)

    second = cns.run_watch(live=True, days=1, db_file=db_file, service=svc)
    assert list(second["actions"]) == ["skipped (already processed)"]


# ── The endpoint ────────────────────────────────────────────────────────────

def _app():
    repo = Path(__file__).resolve().parent.parent
    if str(repo) not in sys.path:
        sys.path.insert(0, str(repo))
    try:
        return importlib.import_module("app")
    except Exception as e:
        pytest.skip(f"app not importable: {e}")


def _seed_token_table(db_file):
    with sqlite3.connect(db_file) as cx:
        cx.execute("CREATE TABLE IF NOT EXISTS oauth_tokens (name TEXT PRIMARY KEY, "
                   "token_json TEXT NOT NULL, updated_at TEXT NOT NULL)")
        cx.commit()


def test_endpoint_rejects_a_wrong_secret(db_file, monkeypatch):
    app_module = _app()
    monkeypatch.setattr(app_module, "LOG_DB", db_file)
    monkeypatch.setenv("CRON_SECRET", "s3cret")
    client = app_module.app.test_client()

    r = client.post("/api/cron/cns-tracking", headers={"X-Cron-Secret": "wrong"})

    assert r.status_code == 401


def test_endpoint_defaults_to_one_day_and_auto_send(db_file, monkeypatch):
    """days=1 and auto_send are the live contract. A silent change to either would
    either re-mail an old backlog or quietly stop notifying anyone."""
    app_module = _app()
    _seed_token_table(db_file)
    monkeypatch.setattr(app_module, "LOG_DB", db_file)
    monkeypatch.setenv("CRON_SECRET", "s3cret")

    class _Loaded:
        creds = object()
    monkeypatch.setattr("dashboard.gmail_token.load_gmail_credentials",
                        lambda *a, **k: _Loaded())
    monkeypatch.setattr("dashboard.gmail_token.persist_refreshed_credentials",
                        lambda *a, **k: False)
    monkeypatch.setattr("dashboard.gmail_token.record_ok", lambda *a, **k: None)
    monkeypatch.setattr("googleapiclient.discovery.build", lambda *a, **k: FakeGmail())

    seen = {}
    def _capture(**kwargs):
        seen.update(kwargs)
        return {"mode": "LIVE+AUTO-SEND", "dry_run": False, "auto_send": True,
                "days": kwargs["days"], "mailbox": "x@y.z", "emails": 0,
                "shipments": 0, "actions": {}, "db": db_file}
    monkeypatch.setattr(cns, "run_watch", _capture)

    r = client_post = app_module.app.test_client().post(
        "/api/cron/cns-tracking", headers={"X-Cron-Secret": "s3cret"})

    assert r.status_code == 200
    assert seen["days"] == 1
    assert seen["auto_send"] is True
    assert seen["live"] is True


def test_endpoint_days_is_clamped(db_file, monkeypatch):
    """?days is operator input. An unbounded window would scan the whole mailbox."""
    app_module = _app()
    _seed_token_table(db_file)
    monkeypatch.setattr(app_module, "LOG_DB", db_file)
    monkeypatch.setenv("CRON_SECRET", "s3cret")

    class _Loaded:
        creds = object()
    monkeypatch.setattr("dashboard.gmail_token.load_gmail_credentials",
                        lambda *a, **k: _Loaded())
    monkeypatch.setattr("dashboard.gmail_token.persist_refreshed_credentials",
                        lambda *a, **k: False)
    monkeypatch.setattr("dashboard.gmail_token.record_ok", lambda *a, **k: None)
    monkeypatch.setattr("googleapiclient.discovery.build", lambda *a, **k: FakeGmail())

    seen = {}
    def _capture(**kwargs):
        seen.update(kwargs)
        return {"mode": "LIVE", "dry_run": False, "auto_send": True, "days": 1,
                "mailbox": None, "emails": 0, "shipments": 0, "actions": {}, "db": ""}
    monkeypatch.setattr(cns, "run_watch", _capture)

    app_module.app.test_client().post(
        "/api/cron/cns-tracking?days=9999", headers={"X-Cron-Secret": "s3cret"})

    assert seen["days"] == 90
