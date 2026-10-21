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


# --- drop-ship clients ------------------------------------------------------------------
# Glen, 2026-10-21: the watcher never auto-sends to a drop-ship client unless that
# practitioner's switch (practitioner_settings.client_review_emails) is on.

import json  # noqa: E402

CLIENT = "cyndi@client.example"
PRAC = "ashley@prac.example"


def _with_dropship(cx, *, switch=False, street="9 Other Rd", zip5="96720"):
    from dashboard import orders as _orders
    from dashboard import practitioner_settings as _ps
    _orders.init_orders_table(cx)
    _ps.init_settings_table(cx)
    cx.execute("INSERT INTO orders (created_at, source, external_ref, email, name, status, "
               "practitioner_id, recipient_email, address_json) VALUES (?,?,?,?,?,?,?,?,?)",
               ("2026-10-01", "dropship", "d1", PRAC, "Ashley King", "packed", "p1", CLIENT,
                json.dumps({"street": street, "zip": zip5})))
    if switch:
        _ps.set_client_review_emails(cx, "p1", True)
    cx.commit()


NO_ADDR = {"email": None, "source": None, "conflict": False}


def test_switch_off_a_ghl_name_match_to_the_client_is_not_sent(cx):
    _with_dropship(cx)
    r, sends, drafts = _run(cx, _match(CLIENT, "high"), NO_ADDR)
    assert sends == [] and drafts == [None]
    assert r["status"] == "needs_review" and r["confidence"] == "dropship-held"
    assert PRAC in r["review_reason"]


def test_switch_off_a_filemaker_match_to_the_client_is_not_sent(cx):
    _with_dropship(cx)
    r, sends, _ = _run(cx, NONE, {"email": CLIENT, "source": "fmp", "conflict": False})
    assert sends == [] and r["confidence"] == "dropship-held"


def test_switch_off_a_harvest_of_the_client_is_not_sent_or_saved(cx):
    _with_dropship(cx)
    saved = []
    sends, drafts = _sends(), _drafts()
    res = handle_confirmation(ONE_SHIPMENT, "m1", cx, NONE, drafts,
                              harvest_fn=lambda n: {"email": CLIENT},
                              persist_contact=lambda *a: saved.append(a) or {},
                              send_fn=sends, auto_send=True, dry_run=False,
                              address_resolve=lambda s: NO_ADDR)
    assert sends.calls == [] and saved == []
    assert res[0]["confidence"] == "dropship-held"


def test_switch_on_the_client_gets_tracking_as_before(cx):
    _with_dropship(cx, switch=True)
    r, sends, _ = _run(cx, _match(CLIENT, "high"), NO_ADDR)
    assert r["status"] == "sent" and sends == [CLIENT]


def test_the_guard_drafts_a_board_match_to_a_held_client(cx):
    """Whatever path chose the address, a held drop-ship client is drafted, never sent."""
    _with_dropship(cx)
    r, sends, drafts = _run(cx, NONE, {"email": CLIENT, "source": "board",
                                       "conflict": False})
    assert sends == [] and drafts == [CLIENT] and r["status"] == "drafted"


def test_an_unreadable_dropship_list_drafts_everything(cx, monkeypatch):
    from dashboard import tracking_recipient as tr
    monkeypatch.setattr(tr, "dropship_recipients_held", lambda _cx: None)
    r, sends, drafts = _run(cx, NONE, {"email": "someone@board.example",
                                       "source": "board", "conflict": False})
    assert sends == [] and drafts == ["someone@board.example"]


def test_a_dropship_order_at_the_label_address_sends_to_the_practitioner(cx):
    """End to end through the real address step: Ashley's parcel goes to Ashley."""
    from dashboard import tracking_recipient as tr
    _with_dropship(cx, street="1016 W Chicago Ct", zip5="85224")
    sends, drafts = _sends(), _drafts()
    res = handle_confirmation(ONE_SHIPMENT, "m1", cx, _match(CLIENT, "high"), drafts,
                              send_fn=sends, auto_send=True, dry_run=False,
                              address_resolve=lambda s: tr.resolve_by_address(cx, s))
    assert sends.calls == [PRAC] and res[0]["confidence"] == "address-board"
