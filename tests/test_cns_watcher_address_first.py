"""The watcher resolves a recipient by SHIP-TO ADDRESS before any name-only match.

Glen, 2026-09-22 (relayed by fulfillment): tracking emails send as soon as the number
exists; a partial name match auto-sends only when the ship-to address also matches.
"""
import sqlite3

import pytest

from dashboard.tracking import init_tracking_schema
from cns_tracking_watcher import handle_confirmation
from tests.test_cns_watcher import ONE_SHIPMENT   # Cyndi O'Brien, 1016 W Chicago Ct 85224


def _match(email, conf):
    return lambda name: {"email": email, "contact_id": "c1", "name": name,
                         "confidence": conf}


NONE = lambda name: None


@pytest.fixture
def cx(tmp_path):
    conn = sqlite3.connect(str(tmp_path / "chat_log.db"))
    init_tracking_schema(conn)
    yield conn
    conn.close()


def _sends():
    calls = []
    def fn(to, subject, html, text):
        calls.append(to)
        return f"msg_{len(calls)}"
    fn.calls = calls
    return fn


def _drafts():
    calls = []
    def fn(to, subject, html, text):
        calls.append(to)
        return f"draft_{len(calls)}"
    fn.calls = calls
    return fn


def _run(cx, find, addr, harvest=None):
    sends, drafts = _sends(), _drafts()
    res = handle_confirmation(ONE_SHIPMENT, "m1", cx, find, drafts, harvest_fn=harvest,
                              send_fn=sends, auto_send=True, dry_run=False,
                              address_resolve=lambda s: addr)
    return res[0], sends.calls, drafts.calls


def test_an_address_match_sends_with_no_name_match_at_all(cx):
    r, sends, drafts = _run(cx, NONE, {"email": "cyndi@board.example", "source": "board",
                                       "conflict": False})
    assert r["status"] == "sent" and r["confidence"] == "address-board"
    assert sends == ["cyndi@board.example"] and drafts == []


def test_a_medium_name_match_sends_when_the_address_agrees(cx):
    """The medium-plus-address rule: the partial name match now auto-sends."""
    r, sends, _ = _run(cx, _match("cyndi@x.example", "medium"),
                       {"email": "cyndi@x.example", "source": "fmp", "conflict": False})
    assert r["status"] == "sent" and sends == ["cyndi@x.example"]


def test_filemaker_against_ghl_is_a_conflict_for_review(cx):
    """The Desiree case: equal standing, different emails. Nothing is sent."""
    r, sends, drafts = _run(cx, _match("other@ghl.example", "medium"),
                            {"email": "cyndi@fmp.example", "source": "fmp",
                             "conflict": False})
    assert r["status"] == "needs_review" and r["confidence"] == "conflict"
    assert sends == [] and drafts == [None]
    assert "cyndi@fmp.example" in r["review_reason"]


def test_the_board_outranks_a_different_ghl_email(cx):
    r, sends, _ = _run(cx, _match("other@ghl.example", "high"),
                       {"email": "cyndi@board.example", "source": "board",
                        "conflict": False})
    assert sends == ["cyndi@board.example"]


def test_an_ambiguous_address_is_not_settled_by_harvest(cx):
    """Harvest is name-only; after an address conflict it would just pick a side."""
    harvested = []
    def harvest(name):
        harvested.append(name)
        return {"email": "guess@example.com"}
    r, sends, _ = _run(cx, NONE, {"email": None, "source": None, "conflict": True,
                                  "reason": "several agreeing FileMaker clients"},
                       harvest=harvest)
    assert r["status"] == "needs_review" and sends == [] and harvested == []


def test_no_address_answer_keeps_todays_name_path(cx):
    r, sends, _ = _run(cx, _match("cyndi@x.example", "high"),
                       {"email": None, "source": None, "conflict": False})
    assert r["confidence"] == "high" and sends == ["cyndi@x.example"]


def test_a_medium_name_match_alone_still_only_drafts(cx):
    r, sends, drafts = _run(cx, _match("cyndi@x.example", "medium"),
                            {"email": None, "source": None, "conflict": False})
    assert r["status"] == "drafted" and sends == [] and drafts == ["cyndi@x.example"]
