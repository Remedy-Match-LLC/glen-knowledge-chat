"""The console can send the free-ebook library email again, through SES, to a lead whose
first one never arrived.

Eight went out from Glen's Gmail on 29 Oct and bounced on its daily cap, after the
once-guard was written, so nothing could ever send them again. Glen, 2026-11-02, platform
tab: "yes" to resending through SES. The resend uses the link the lead was first given,
never a new one, and creates no portal and no grant.
"""
import sqlite3

import pytest

from tests.test_library_ready_never_uses_glens_gmail import (  # noqa: F401  (fixtures)
    _optin, appmod, ses, transports)

SLUG = "healing-glaucoma-starter"


LATER = "9999-01-01T00:00:00+00:00"


def _resend(appmod, email="lead@x.com", slug=SLUG, key=True, before=LATER):
    headers = {"X-Console-Key": appmod.CONSOLE_SECRET} if key else {}
    return appmod.app.test_client().post("/api/console/library-ready/resend",
                                         json={"email": email, "ebook_slug": slug,
                                               "sent_before": before},
                                         headers=headers)


def _guard_rows(appmod):
    cx = sqlite3.connect(appmod.LOG_DB)
    return cx.execute("SELECT email, ebook_slug FROM ebook_library_welcome_sent").fetchall()


@pytest.fixture
def console(appmod, monkeypatch):
    monkeypatch.setattr(appmod, "CONSOLE_SECRET", "test-console-key")
    monkeypatch.setattr(appmod, "portal_link", lambda t: f"https://x.test/p/{t}")
    return appmod


def test_a_resend_goes_out_again_through_ses_with_the_same_link(console, transports, ses):
    assert _optin(console).status_code == 200
    assert _optin(console).status_code == 200      # the guard stops a second opt-in send
    assert len(ses.calls) == 1
    r = _resend(console)
    assert r.status_code == 200 and r.get_json() == {"ok": True, "sent_via": "ses"}
    assert len(ses.calls) == 2 and transports == []
    first, again = (c["Content"]["Raw"]["Data"].decode() for c in ses.calls)
    link = next(w for w in first.split() if w.startswith("https://x.test/p/"))
    assert link in again
    subject = lambda raw: raw.split("\r\nSubject: ", 1)[1].split("\r\n", 1)[0]
    assert subject(again) == subject(first) and "waiting for you in your library" in again
    # Sent again, the guard is back, so the next opt-in still sends nothing.
    assert _guard_rows(console) == [("lead@x.com", SLUG)]


def test_the_console_key_is_required(console, transports, ses):
    _optin(console)
    assert _resend(console, key=False).status_code == 401
    assert len(ses.calls) == 1


def test_no_grant_sends_nothing_and_creates_nothing(console, transports, ses):
    r = _resend(console, email="stranger@x.com")
    assert r.status_code == 404 and ses.calls == [] and transports == []
    cx = sqlite3.connect(console.LOG_DB)
    tables = {t for (t,) in cx.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if "client_portals" in tables:
        assert cx.execute("SELECT COUNT(*) FROM client_portals WHERE email=?",
                          ("stranger@x.com",)).fetchone()[0] == 0


@pytest.mark.parametrize("slug", ["", "no-such-ebook"])
def test_an_unknown_ebook_is_refused(console, transports, ses, slug):
    _optin(console)
    assert _resend(console, slug=slug).status_code == 400
    assert len(ses.calls) == 1


def test_a_lead_with_no_stored_link_gets_nothing(console, transports, ses):
    """A new link would stop any copy they hold from working, so none is issued."""
    _optin(console)
    from dashboard import db
    with db.connect(console.LOG_DB) as cx:
        cx.execute("UPDATE portal_notify_state SET portal_token=NULL WHERE email=?",
                   ("lead@x.com",))
        cx.commit()
    r = _resend(console)
    assert r.status_code == 409 and len(ses.calls) == 1


def test_a_suppressed_lead_gets_nothing(console, transports, ses):
    _optin(console)
    from dashboard import db, email_suppression as es
    with db.connect(console.LOG_DB) as cx:
        es.init_table(cx)
        es.add(cx, "lead@x.com", "hard", "bounce", "test")
    r = _resend(console)
    assert r.get_json()["ok"] is False and len(ses.calls) == 1 and transports == []


def test_a_retry_after_a_resend_sends_nothing(console, transports, ses):
    """Review round 2: a retry, or a second call, must not send a second copy."""
    _optin(console)
    cx = sqlite3.connect(console.LOG_DB)
    (first_at,) = cx.execute("SELECT sent_at FROM ebook_library_welcome_sent").fetchone()
    cx.close()
    import datetime as _dt
    cutoff = (_dt.datetime.fromisoformat(first_at) + _dt.timedelta(microseconds=1)).isoformat()
    assert _resend(console, before=cutoff).get_json()["ok"] is True
    r = _resend(console, before=cutoff)
    assert r.status_code == 409 and len(ses.calls) == 2


def test_a_send_after_the_cutoff_is_not_released(console, transports, ses):
    _optin(console)
    r = _resend(console, before="2000-01-01T00:00:00+00:00")
    assert r.status_code == 409 and len(ses.calls) == 1
    assert _guard_rows(console) == [("lead@x.com", SLUG)]


def test_sent_before_is_required(console, transports, ses):
    _optin(console)
    assert _resend(console, before="").status_code == 400 and len(ses.calls) == 1
