"""Confirming a free product review emails the client.

Glen's ruling, 2026-09-09. Before this, a confirmed review sat in the portal with no
notification, so a client who did not revisit never learned it was ready.

What these lock down:
  - one mail on the transition to confirmed, and none on a repeat confirm
  - no mail to a suppressed address, and none when suppression cannot be read
  - the mail carries the portal link and never the review text
  - a send failure does not undo the confirm
"""
import sqlite3

import pytest

from dashboard import email_suppression as es
from dashboard import ghl_email as ghl
from dashboard import supplement_reviews as sr
from dashboard import supplement_review_notify as srn
from dashboard import supplement_reviews_actions as sra


REVIEW_TEXT = "Magnesium glycinate at 200 mg is a reasonable evening dose."


@pytest.fixture
def cx():
    c = sqlite3.connect(":memory:")
    sr.init_table(c)
    yield c
    c.close()


@pytest.fixture
def sent(monkeypatch):
    """Capture sends instead of making them.

    Patches the functions on the real modules rather than swapping the modules in
    sys.modules. `from dashboard import x` reads the attribute off the already
    imported package, so a sys.modules swap is ignored once any earlier test has
    imported it, and these tests passed alone but failed in a full run.
    """
    box = []

    def _send(to, subject, *, html=None, text=None, **kw):
        box.append({"to": to, "subject": subject, "html": html, "text": text})
        return {"id": "fake", "via": "ghl"}

    monkeypatch.setattr(ghl, "is_configured", lambda: True)
    monkeypatch.setattr(ghl, "send_via_ghl", _send)
    monkeypatch.setenv("PORTAL_BASE_URL", "https://myhealingoasis.com")
    return box


@pytest.fixture
def allow_all(monkeypatch):
    """Nobody is suppressed."""
    monkeypatch.setattr(es, "is_suppressed", lambda cx, email: False)


def _make_confirmed_ready(cx, email="client@example.com"):
    res = sr.create_request(cx, email, "Neuro Magnesium", "Acme", source="portal")
    rid = res["id"]
    sr.set_draft(cx, rid, REVIEW_TEXT)
    return rid


def test_confirm_sends_one_email_with_the_portal_link(cx, sent, allow_all):
    rid = _make_confirmed_ready(cx)

    out = sra._exec_confirm({"id": rid}, {"cx": cx, "actor": {"name": "Glen"}})

    assert out["status"] == "confirmed"
    assert out["notified"]["sent"] is True
    assert len(sent) == 1
    msg = sent[0]
    assert msg["to"] == "client@example.com"
    assert "https://myhealingoasis.com/portal/" in msg["text"]
    assert REVIEW_TEXT not in msg["text"]
    assert REVIEW_TEXT not in msg["html"]


def test_second_confirm_does_not_email_again(cx, sent, allow_all):
    rid = _make_confirmed_ready(cx)
    sra._exec_confirm({"id": rid}, {"cx": cx, "actor": {}})
    sra._exec_confirm({"id": rid}, {"cx": cx, "actor": {}})
    assert len(sent) == 1


def test_suppressed_address_is_not_emailed(cx, sent, monkeypatch):
    monkeypatch.setattr(es, "is_suppressed", lambda cx, email: True)
    rid = _make_confirmed_ready(cx)
    out = sra._exec_confirm({"id": rid}, {"cx": cx, "actor": {}})
    assert out["status"] == "confirmed"
    assert out["notified"] == {"sent": False, "reason": "suppressed"}
    assert sent == []


def test_unreadable_suppression_list_fails_closed(cx, sent, monkeypatch):
    def _boom(cx_, email):
        raise RuntimeError("suppression table missing")

    monkeypatch.setattr(es, "is_suppressed", _boom)
    rid = _make_confirmed_ready(cx)
    out = sra._exec_confirm({"id": rid}, {"cx": cx, "actor": {}})
    assert out["notified"]["reason"] == "suppression-unreadable"
    assert sent == [], "an unreadable suppression list is not permission to send"


def test_send_failure_does_not_undo_the_confirm(cx, allow_all, monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("GHL down")

    monkeypatch.setattr(ghl, "is_configured", lambda: True)
    monkeypatch.setattr(ghl, "send_via_ghl", _boom)
    monkeypatch.setenv("PORTAL_BASE_URL", "https://myhealingoasis.com")
    rid = _make_confirmed_ready(cx)

    out = sra._exec_confirm({"id": rid}, {"cx": cx, "actor": {}})

    assert out["status"] == "confirmed"
    assert out["notified"]["sent"] is False
    assert sr.get(cx, rid)["status"] == "confirmed"


def test_an_unconfirmed_review_is_never_announced(cx, sent, allow_all):
    res = sr.create_request(cx, "client@example.com", "Probe", "Acme")
    out = srn.notify_confirmed(cx, res["id"])
    assert out == {"sent": False, "reason": "not-confirmed"}
    assert sent == []


def test_copy_has_no_em_dash_and_no_shouting():
    text, html = srn.body_for("Neuro Magnesium", "Acme",
                              "https://myhealingoasis.com/portal/abc")
    for body in (text, html, srn.SUBJECT):
        assert "—" not in body
        words = [w for w in body.replace("<", " ").split() if w.isalpha() and len(w) > 3]
        assert not [w for w in words if w.isupper()], f"shouting in: {body[:60]}"
    assert "Neuro Magnesium by Acme" in text
