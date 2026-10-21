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
# practitioner's switch (practitioner_settings.client_review_emails) is on. When it
# cannot prove a parcel is not a held client's, it drafts, addressed to the practitioner.
# ONE_SHIPMENT is Cyndi O'Brien at 1016 W Chicago Ct 85224.

import json  # noqa: E402

CLIENT = "cyndi@client.example"
PRAC = "ashley@prac.example"
NO_ADDR = {"email": None, "source": None, "conflict": False}


def _with_dropship(cx, *, switch=False, street="9 Other Rd", zip5="96720",
                   client=CLIENT, ship_name="Cyndi O'Brien", status="packed"):
    from dashboard import orders as _orders
    from dashboard import practitioner_settings as _ps
    _orders.init_orders_table(cx)
    _ps.init_settings_table(cx)
    cx.execute("INSERT INTO orders (created_at, source, external_ref, email, name, status, "
               "practitioner_id, recipient_email, address_json) VALUES (?,?,?,?,?,?,?,?,?)",
               ("2026-10-01", "dropship", "d1", PRAC, "Ashley King", status, "p1", client,
                json.dumps({"name": ship_name, "street": street, "zip": zip5})))
    if switch:
        _ps.set_client_review_emails(cx, "p1", True)
    cx.commit()


def _held(r, sends, drafts):
    assert sends == [] and drafts == [PRAC]
    assert r["status"] == "drafted" and r["confidence"] == "dropship-held"
    assert PRAC in r["review_reason"]


def test_switch_off_a_blank_client_email_is_held_by_address(cx):
    """Round 1 F1: no client email typed, order delivered, GHL knows Cyndi."""
    _with_dropship(cx, client=None, street="1016 W Chicago Ct", zip5="85224",
                   status="delivered")
    _held(*_run(cx, _match(CLIENT, "high"), NO_ADDR))


def test_switch_off_a_second_client_email_is_held_by_name(cx):
    """Round 1 F2: Ashley typed a work address; GHL has another; different street."""
    _with_dropship(cx, client="cyndi@work.example", status="done")
    _held(*_run(cx, _match("cyndi@gmail.example", "high"), NO_ADDR))


def test_switch_off_a_filemaker_match_to_the_client_is_held(cx):
    _with_dropship(cx)
    _held(*_run(cx, NONE, {"email": CLIENT, "source": "fmp", "conflict": False}))


def test_switch_off_a_harvest_of_the_client_is_held_and_not_saved(cx):
    _with_dropship(cx)
    saved = []
    sends, drafts = _sends(), _drafts()
    res = handle_confirmation(ONE_SHIPMENT, "m1", cx, NONE, drafts,
                              harvest_fn=lambda n: {"email": CLIENT},
                              persist_contact=lambda *a: saved.append(a) or {},
                              send_fn=sends, auto_send=True, dry_run=False,
                              address_resolve=lambda s: NO_ADDR)
    _held(res[0], sends.calls, drafts.calls)
    assert saved == []


def test_switch_off_a_board_match_without_its_own_name_is_held(cx):
    _with_dropship(cx)
    _held(*_run(cx, NONE, {"email": CLIENT, "source": "board", "conflict": False}))


def test_switch_on_the_client_gets_tracking_as_before(cx):
    _with_dropship(cx, switch=True)
    r, sends, _ = _run(cx, _match(CLIENT, "high"), NO_ADDR)
    assert r["status"] == "sent" and sends == [CLIENT]


def _real(cx, find):
    from dashboard import tracking_recipient as tr
    sends, drafts = _sends(), _drafts()
    res = handle_confirmation(ONE_SHIPMENT, "m1", cx, find, drafts,
                              send_fn=sends, auto_send=True, dry_run=False,
                              address_resolve=lambda s: tr.resolve_by_address(cx, s))
    return res[0], sends.calls, drafts.calls


def _own(cx, email, name, status="packed"):
    cx.execute("INSERT INTO orders (created_at, source, external_ref, email, name, status, "
               "address_json) VALUES (?,?,?,?,?,?,?)",
               ("2026-10-02", "storefront", "s1", email, name, status,
                json.dumps({"street": "1016 W Chicago Ct", "zip": "85224"})))
    cx.commit()


def test_a_dropship_order_at_the_label_address_sends_to_the_practitioner(cx):
    _with_dropship(cx, street="1016 W Chicago Ct", zip5="85224")
    r, sends, _ = _real(cx, _match(CLIENT, "high"))
    assert sends == [PRAC] and r["confidence"] == "address-board"


def test_a_former_dropship_client_s_own_order_still_sends(cx):
    """Round 1 F5: Cyndi's own order, under her own email, after a delivered drop-ship."""
    _with_dropship(cx, street="1016 W Chicago Ct", zip5="85224", status="delivered")
    _own(cx, CLIENT, "Cyndi O'Brien")
    r, sends, _ = _real(cx, NONE)
    assert sends == [CLIENT] and r["status"] == "sent"


def test_a_housemate_at_a_dropship_address_gets_her_own_tracking(cx):
    """Round 1 F3: the drop-ship is to Tony; Cyndi's own parcel is not sent to Ashley."""
    _with_dropship(cx, street="1016 W Chicago Ct", zip5="85224", client="tony@x.example",
                   ship_name="Tony Client")
    _own(cx, "cyndi@own.example", "Cyndi O'Brien")
    r, sends, _ = _real(cx, NONE)
    assert sends == ["cyndi@own.example"]


def test_an_unreadable_holds_list_drafts_with_no_address(cx, monkeypatch):
    from dashboard import tracking_recipient as tr
    monkeypatch.setattr(tr, "dropship_holds", lambda _cx: None)
    r, sends, drafts = _run(cx, NONE, {"email": "someone@board.example",
                                       "source": "board", "conflict": False})
    assert sends == [] and drafts == [None] and r["status"] == "needs_review"


def test_a_failed_address_lookup_drafts(cx):
    """Round 1 F8: the lookup raising must not hand the box to the name match."""
    def boom(s):
        raise RuntimeError("db down")
    sends, drafts = _sends(), _drafts()
    res = handle_confirmation(ONE_SHIPMENT, "m1", cx, _match(CLIENT, "high"), drafts,
                              send_fn=sends, auto_send=True, dry_run=False,
                              address_resolve=boom)
    assert sends.calls == [] and drafts.calls == [None]


def test_the_switch_is_read_again_before_each_send(cx):
    """Round 2 High 2: turned off after the first email, it holds the second."""
    from dashboard import practitioner_settings as _ps
    from tests.test_cns_watcher_one_email_per_address import _block, _confirmation
    _with_dropship(cx, switch=True)
    html = _confirmation(
        _block("9405530109355600000011", "Cal Lee", "9 MAUKA RD", "HILO HI 96720-1234 US"),
        _block("9405530109355600000028", "Cyndi O'Brien", "5 KAI ST", "HILO HI 96720-1234 US"))
    emails = {"Cal Lee": "cal@x.example", "Cyndi O'Brien": CLIENT}
    sent = []
    def send(to, subject, html, text):
        sent.append(to)
        _ps.set_client_review_emails(cx, "p1", False)
        return "msg"
    drafts = _drafts()
    handle_confirmation(html, "m1", cx, lambda n: {"email": emails[n], "contact_id": "c",
                                                    "name": n, "confidence": "high"},
                        drafts, send_fn=send, auto_send=True, dry_run=False,
                        address_resolve=lambda s: NO_ADDR)
    assert sent == ["cal@x.example"] and drafts.calls == [PRAC]


def test_a_switch_turned_off_after_resolving_holds_the_client(cx):
    """Resolved to the client while the switch was on; off by send time, it drafts."""
    from dashboard import practitioner_settings as _ps
    from dashboard import tracking_recipient as tr
    _with_dropship(cx, switch=True, street="1016 W Chicago Ct", zip5="85224")
    def resolve_then_switch_off(s):
        r = tr.resolve_by_address(cx, s)
        _ps.set_client_review_emails(cx, "p1", False)
        return r
    sends, drafts = _sends(), _drafts()
    handle_confirmation(ONE_SHIPMENT, "m1", cx, NONE, drafts, send_fn=sends,
                        auto_send=True, dry_run=False,
                        address_resolve=resolve_then_switch_off)
    assert sends.calls == [] and drafts.calls == [PRAC]
