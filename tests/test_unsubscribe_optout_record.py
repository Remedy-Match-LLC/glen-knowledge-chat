"""Recipient opt-outs land in email_suppression so every existing sender honors them.

The distinguishing column is bounce_type: a hard bounce means the address is dead,
an optout means the person asked us to stop. Conflating them would let a later
bounce-list cleanup silently re-subscribe someone.
"""
from dashboard import db, email_suppression as es


def _cx():
    cx = db.connect(":memory:")
    es.init_table(cx)
    return cx


def test_optout_is_suppressed_afterwards():
    cx = _cx()
    assert es.is_suppressed(cx, "a@b.com") is False
    es.add_optout(cx, "a@b.com", "unsubscribe-link")
    assert es.is_suppressed(cx, "a@b.com") is True


def test_optout_is_recorded_as_optout_not_a_bounce():
    cx = _cx()
    es.add_optout(cx, "a@b.com", "unsubscribe-link")
    row = cx.execute("SELECT bounce_type FROM email_suppression "
                     "WHERE email='a@b.com'").fetchone()
    assert row[0] == "optout"


def test_optout_is_idempotent_and_case_insensitive():
    cx = _cx()
    es.add_optout(cx, "A@B.com", "unsubscribe-link")
    es.add_optout(cx, "a@b.com", "unsubscribe-link")
    n = cx.execute("SELECT COUNT(*) FROM email_suppression").fetchone()[0]
    assert n == 1


def test_optout_does_not_downgrade_an_existing_hard_bounce():
    cx = _cx()
    es.add(cx, "a@b.com", "hard", "550 no such user", "bounce-scanner")
    es.add_optout(cx, "a@b.com", "unsubscribe-link")
    row = cx.execute("SELECT bounce_type FROM email_suppression "
                     "WHERE email='a@b.com'").fetchone()
    assert row[0] == "hard"


def test_blank_address_is_a_no_op():
    cx = _cx()
    es.add_optout(cx, "", "unsubscribe-link")
    n = cx.execute("SELECT COUNT(*) FROM email_suppression").fetchone()[0]
    assert n == 0
