"""The /webhook/ses-events route reaches dashboard.ses_events and writes LOG_DB.

Skips when app is not importable (the secretless CI run), like the other route
tests. The verifier itself is covered without the app in tests/test_ses_events.py."""
import importlib
import json
import sys
from pathlib import Path

import pytest

from dashboard import db, email_suppression as es, ses_events as se


def _client(tmp_path, monkeypatch):
    scratch = str(tmp_path / "log.db")
    repo = Path(__file__).resolve().parent.parent
    if str(repo) not in sys.path:
        sys.path.insert(0, str(repo))
    try:
        import app as appmod
        importlib.reload(appmod)
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"app not importable: {e}")
    monkeypatch.setattr(appmod, "LOG_DB", scratch, raising=False)
    appmod.app.config["TESTING"] = True
    return appmod.app.test_client(), scratch


def test_unconfigured_route_refuses(tmp_path, monkeypatch):
    monkeypatch.delenv(se.TOPIC_ENV, raising=False)
    c, _ = _client(tmp_path, monkeypatch)
    r = c.post("/webhook/ses-events", data=json.dumps({"Type": "Notification"}),
               content_type="text/plain")
    assert r.status_code == 403


def test_verified_bounce_lands_in_log_db(tmp_path, monkeypatch):
    monkeypatch.setenv(se.TOPIC_ENV, "arn:aws:sns:us-west-2:1:t")
    monkeypatch.setattr(se, "verify", lambda msg, fetch_cert=None: None)
    c, scratch = _client(tmp_path, monkeypatch)
    event = {"eventType": "Bounce", "bounce": {"bounceType": "Permanent",
             "bouncedRecipients": [{"emailAddress": "gone@example.com"}]}}
    body = {"Type": "Notification", "TopicArn": "arn:aws:sns:us-west-2:1:t",
            "Message": json.dumps(event)}
    r = c.post("/webhook/ses-events", data=json.dumps(body), content_type="text/plain")
    assert r.status_code == 200, r.data
    with db.connect(scratch) as cx:
        assert es.suppression_reason(cx, "gone@example.com") == "hard"
