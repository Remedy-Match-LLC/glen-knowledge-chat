# tests/test_library_ready_never_uses_glens_gmail.py
"""The free-ebook "library ready" email never goes out from Glen's own Gmail.

2026-10-29: about 50 a day of these, to anyone who typed an address into the public
opt-in form, filled his Gmail's daily cap and drew "Message blocked" bounces. His
own replies then bounced too. They now go through GHL/Mailgun only.
"""
import importlib
import sys
import threading
from pathlib import Path

import pytest


@pytest.fixture
def appmod(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    repo = Path(__file__).resolve().parent.parent
    if str(repo) not in sys.path:
        sys.path.insert(0, str(repo))
    import app as a
    importlib.reload(a)
    return a


@pytest.fixture
def transports(appmod, monkeypatch):
    sent = []
    from dashboard import ghl_email, inbox
    monkeypatch.setattr(inbox, "send_email", lambda *a, **k: sent.append("gmail"))
    monkeypatch.setattr(ghl_email, "is_configured", lambda: True)
    monkeypatch.setattr(ghl_email, "send_via_ghl", lambda *a, **k: sent.append("ghl"))
    for k, v in (("SMTP_HOST", "smtp.gmail.com"), ("SMTP_USER", "glen@x.com"),
                 ("SMTP_PASS", "fake")):
        monkeypatch.setenv(k, v)
    import smtplib
    monkeypatch.setattr(smtplib, "SMTP", lambda *a, **k: sent.append("smtp") or 1 / 0)
    return sent


def test_bulk_skips_gmail_and_smtp(appmod, transports):
    assert appmod._send_full_report_email("a@b.co", "", "s", "b", bulk=True) == ("ghl", None)
    assert transports == ["ghl"]


def test_bulk_skips_smtp_when_ghl_fails(appmod, transports, monkeypatch):
    from dashboard import ghl_email
    monkeypatch.setattr(ghl_email, "send_via_ghl", lambda *a, **k: 1 / 0)
    via, _ = appmod._send_full_report_email("a@b.co", "", "s", "b", bulk=True)
    assert via == "console-log" and transports == []


def test_other_mail_still_prefers_gmail(appmod, transports):
    assert appmod._send_full_report_email("a@b.co", "", "s", "b")[0] == "gmail-api"
    assert transports == ["gmail"]


def _now_threads(monkeypatch):
    real = threading.Thread

    class Now(real):
        def start(self):
            self.run()
    monkeypatch.setattr(threading, "Thread", Now)


def _optin(appmod, email="lead@x.com"):
    return appmod.app.test_client().post("/api/ebook/optin", json={
        "email": email, "ebook_slug": "healing-glaucoma-starter",
        "source_site": "healingglaucoma.com"})


def _guard_rows(appmod):
    import sqlite3
    cx = sqlite3.connect(appmod.LOG_DB)
    return cx.execute("SELECT COUNT(*) FROM ebook_library_welcome_sent").fetchone()[0]


def test_the_optin_sends_bulk_without_logging_the_link(appmod, monkeypatch):
    calls = []
    monkeypatch.setattr(appmod, "_send_full_report_email",
                        lambda *a, **k: calls.append(k) or ("ghl", None))
    real = threading.Thread

    class Now(real):
        def start(self):
            self.run()
    monkeypatch.setattr(threading, "Thread", Now)
    r = appmod.app.test_client().post("/api/ebook/optin", json={
        "email": "lead@x.com", "ebook_slug": "healing-glaucoma-starter",
        "source_site": "healingglaucoma.com"})
    assert r.status_code == 200
    assert calls == [{"bulk": True, "log_body": False}]


def test_a_failed_send_never_logs_the_link_and_frees_the_guard(appmod, transports,
                                                                monkeypatch, capsys):
    """Review round 2: run the REAL sender with GHL failing, and read the logs."""
    from dashboard import ghl_email
    def boom(to, subject, *, html=None, text=None, **k):
        raise RuntimeError("GHL rejected: " + (text or ""))   # an error that quotes the body
    monkeypatch.setattr(ghl_email, "send_via_ghl", boom)
    monkeypatch.setattr(appmod, "portal_link", lambda t: "https://x.test/p/SECRET-LINK-123")
    _now_threads(monkeypatch)
    assert _optin(appmod).status_code == 200
    got = capsys.readouterr()
    assert "SECRET-LINK-123" not in got.out + got.err
    assert transports == []
    assert _guard_rows(appmod) == 0          # the next opt-in tries again


def test_a_sent_email_keeps_the_guard(appmod, transports, monkeypatch):
    _now_threads(monkeypatch)
    assert _optin(appmod).status_code == 200
    assert transports == ["ghl"] and _guard_rows(appmod) == 1
    assert _optin(appmod).status_code == 200
    assert transports == ["ghl"]             # once per address and ebook


def test_a_junk_address_never_reaches_ghl(appmod, transports, monkeypatch):
    _now_threads(monkeypatch)
    assert _optin(appmod, "someone@example.com").status_code == 200
    assert transports == [] and _guard_rows(appmod) == 1
