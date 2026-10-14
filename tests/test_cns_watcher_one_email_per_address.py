"""Boxes from one Click-N-Ship checkout to one address go out as ONE tracking email.

Glen, 2026-10-14: "Please consolidate tracking number emails when going to the same
address the same day." He chose "same checkout": no added wait. Fukushima's 6 boxes that
day became 6 separate drafts, and none reached the customer, because the label's business
line was read as part of the street. Names and addresses here are made up.
"""
import sqlite3

import pytest

from cns_tracking_watcher import handle_confirmation
from dashboard import tracking_recipient as tr
from dashboard.tracking import build_tracking_email, init_tracking_schema, shipment_exists


def _block(tracking, *lines):
    ps = "".join(f'<p class="pt-5">{ln}</p>' for ln in lines)
    return ('<td class="item-contents-column"><table><tr><td>'
            '<p class="bold">Priority Mail&#174;</p>'
            f'<a href="x?tLabelsB0852245249{tracking}"> 420852245249{tracking}</a>'
            f'<p class="bold">Shipped To:</p>{ps}'
            '</td></tr></table></td><td class="item-total-column"><p>$11.99</p></td>')


def _confirmation(*blocks):
    return ('<a href="x?orderUUID=019e3cbe-bd74-7a99-8876-2269d7f83860">order</a>'
            + "".join(blocks))


ANN = ("Ann & Bea Kalani", "Kalani Wellness Center", "77 PALM CT", "HILO HI 96720-1234 US")
N = ["9405530109355600000011", "9405530109355600000028", "9405530109355600000035"]
OTHER = "9405530109355600000042"


@pytest.fixture
def cx(tmp_path):
    conn = sqlite3.connect(str(tmp_path / "chat_log.db"))
    init_tracking_schema(conn)
    yield conn
    conn.close()


def _rec(prefix):
    calls = []
    def fn(to, subject, html, text):
        calls.append({"to": to, "html": html, "text": text})
        return f"{prefix}_{len(calls)}"
    fn.calls = calls
    return fn


def _run(cx, html, *, find=lambda name: None, addr=None, auto_send=True, dry_run=False):
    sends, drafts = _rec("msg"), _rec("draft")
    res = handle_confirmation(html, "m1", cx, find, drafts, send_fn=sends, auto_send=auto_send,
                              dry_run=dry_run, address_resolve=addr)
    return res, sends.calls, drafts.calls


def _to(email, source="fmp"):
    return lambda s: {"email": email, "source": source, "conflict": False}


def test_three_boxes_to_one_address_send_one_email_with_every_number(cx):
    html = _confirmation(*[_block(n, *ANN) for n in N])
    res, sends, drafts = _run(cx, html, addr=_to("ann@example.com"))
    assert len(sends) == 1 and drafts == []
    for i, n in enumerate(N, 1):
        assert f"Box {i}: {n}" in sends[0]["text"]
        assert f'<h3>Box {i}: <a href="' in sends[0]["html"] and n in sends[0]["html"]
    assert {r["status"] for r in res} == {"sent"}
    assert {r["draft_id"] for r in res} == {"msg_1"}          # one message, three rows
    assert all(shipment_exists(cx, n) for n in N)


def test_another_address_in_the_same_checkout_gets_its_own_email(cx):
    other = _block(OTHER, "Cal Lee", "9 MAUKA RD", "HILO HI 96720-1234 US")
    html = _confirmation(_block(N[0], *ANN), other, _block(N[1], *ANN))
    emails = {"Ann & Bea Kalani": "ann@example.com", "Cal Lee": "cal@example.com"}
    res, sends, _ = _run(cx, html, addr=lambda s: {"email": emails[s["recipient_name"]],
                                                  "source": "board", "conflict": False})
    assert sorted(c["to"] for c in sends) == ["ann@example.com", "cal@example.com"]
    ann = next(c for c in sends if c["to"] == "ann@example.com")
    assert N[0] in ann["text"] and N[1] in ann["text"] and OTHER not in ann["text"]


def test_unresolved_boxes_make_one_review_draft(cx):
    html = _confirmation(*[_block(n, *ANN) for n in N])
    res, sends, drafts = _run(cx, html)
    assert sends == [] and len(drafts) == 1 and drafts[0]["to"] is None
    assert all(n in drafts[0]["text"] for n in N)
    assert {r["status"] for r in res} == {"needs_review"}


def test_two_names_at_one_address_with_one_email_get_one_email(cx):
    html = _confirmation(_block(N[0], "Ann Kalani", "77 PALM CT", "HILO HI 96720 US"),
                         _block(N[1], "Bea Kalani", "77 PALM CT", "HILO HI 96720 US"))
    res, sends, _ = _run(cx, html, addr=_to("kalani@example.com"))
    assert len(sends) == 1 and N[0] in sends[0]["text"] and N[1] in sends[0]["text"]


def test_two_people_at_one_address_with_their_own_emails_each_get_theirs(cx):
    html = _confirmation(_block(N[0], "Ann Kalani", "77 PALM CT", "HILO HI 96720 US"),
                         _block(N[1], "Bea Kalani", "77 PALM CT", "HILO HI 96720 US"))
    emails = {"Ann Kalani": "ann@example.com", "Bea Kalani": "bea@example.com"}
    res, sends, _ = _run(cx, html, addr=lambda s: {"email": emails[s["recipient_name"]],
                                                  "source": "fmp", "conflict": False})
    assert sorted(c["to"] for c in sends) == ["ann@example.com", "bea@example.com"]
    assert all(len([n for n in N[:2] if n in c["text"]]) == 1 for c in sends)


def test_the_recipient_is_looked_up_once_per_name_and_address(cx):
    calls = []
    def addr(s):
        calls.append(s["tracking"])
        return {"email": "ann@example.com", "source": "fmp", "conflict": False}
    _run(cx, _confirmation(*[_block(n, *ANN) for n in N]), addr=addr)
    assert len(calls) == 1


def test_a_rerun_sends_nothing_more(cx):
    html = _confirmation(*[_block(n, *ANN) for n in N])
    _run(cx, html, addr=_to("ann@example.com"))
    res, sends, drafts = _run(cx, html, addr=_to("ann@example.com"))
    assert sends == [] and drafts == []
    assert {r["action"] for r in res} == {"skipped (already processed)"}


def test_a_dry_run_sends_and_records_nothing(cx):
    res, sends, drafts = _run(cx, _confirmation(*[_block(n, *ANN) for n in N]),
                              addr=_to("ann@example.com"), dry_run=True)
    assert sends == [] and drafts == [] and not any(shipment_exists(cx, n) for n in N)
    assert all("one email for 3 boxes" in r["action"] for r in res)


def test_a_failed_send_drafts_once_for_the_group(cx):
    def boom(**k):
        raise RuntimeError("ghl down")
    drafts = _rec("draft")
    res = handle_confirmation(_confirmation(*[_block(n, *ANN) for n in N]), "m1", cx,
                              lambda n: None, drafts, send_fn=boom, auto_send=True,
                              dry_run=False, address_resolve=_to("ann@example.com"))
    assert len(drafts.calls) == 1 and {r["status"] for r in res} == {"send_failed"}


def test_one_box_reads_exactly_as_before():
    from dashboard import tracking as T
    url = T.tracking_url(N[0])
    one = build_tracking_email(N[0], "Ann Kalani", resolved_email="a@example.com")
    assert one["subject"] == "tracking number"
    assert one["text"] == ("Aloha Ann,\n\nYour order is on its way. Here is your USPS "
                           f"tracking number:\n\n{N[0]}\n{url}" + T.SIGNATURE_TEXT)
    assert one["html"] == ('<div dir="ltr"><p>Aloha Ann,</p><p>Your order is on its way. '
                           'Here is your USPS tracking number:</p>'
                           f'<h3><a href="{url}">{N[0]}</a></h3>' + T.SIGNATURE_HTML + "</div>")
    assert one == build_tracking_email([N[0]], "Ann Kalani", resolved_email="a@example.com")


def test_the_review_note_survives_in_a_grouped_draft():
    em = build_tracking_email(N, "Ann Kalani", resolved_email=None)
    assert em["text"].startswith("[No email on file for Ann Kalani")


# --- the business line that hid Fukushima's address ------------------------------------

def _addr_db():
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.execute("CREATE TABLE orders (id INTEGER PRIMARY KEY, email TEXT, name TEXT, "
               "status TEXT, address_json TEXT)")
    db.execute("CREATE TABLE fmp_clients (id_pk TEXT, name_first TEXT, name_last TEXT, "
               "email TEXT)")
    db.execute("CREATE TABLE fmp_client_addresses (id_pk TEXT, id_fk_client TEXT, "
               "street TEXT, postal_code TEXT)")
    return db


def _label(name, *street_lines, csz="HILO HI 96720 US"):
    from dashboard.tracking import parse_cns_confirmation
    return parse_cns_confirmation(_confirmation(_block(N[0], name, *street_lines, csz)))["shipments"][0]


def _board(db, email, name, street):
    import json
    db.execute("INSERT INTO orders (email, name, status, address_json) VALUES (?,?,?,?)",
               (email, name, "packed", json.dumps({"street": street, "zip": "96720"})))


def test_the_fukushima_shaped_label_resolves_by_filemaker_address(cx):
    db = _addr_db()
    db.execute("INSERT INTO fmp_clients VALUES ('1','Ann','Kalani','ann@example.com')")
    db.execute("INSERT INTO fmp_client_addresses VALUES ('a1','1','77 Palm Court','96720')")
    html = _confirmation(*[_block(n, *ANN) for n in N])
    res, sends, drafts = _run(cx, html, addr=lambda s: tr.resolve_by_address(db, s))
    assert [c["to"] for c in sends] == ["ann@example.com"] and drafts == []
    assert {r["confidence"] for r in res} == {"address-fmp"}


def test_a_board_order_needs_the_name_when_the_business_line_is_dropped():
    db = _addr_db()
    _board(db, "someone@example.com", "Cal Lee", "77 Palm Ct")
    r = tr.resolve_by_address(db, _label("Ann Kalani", "Kalani Wellness Center", "77 PALM CT"))
    assert r["email"] is None
    _board(db, "ann@example.com", "Ann Kalani", "77 Palm Ct")
    r = tr.resolve_by_address(db, _label("Ann Kalani", "Kalani Wellness Center", "77 PALM CT"))
    assert r["email"] == "ann@example.com"


def test_the_whole_street_still_wins_with_no_name_check_as_before():
    db = _addr_db()
    _board(db, "someone@example.com", "Cal Lee", "77 Palm Ct")
    assert tr.resolve_by_address(db, _label("Ann Kalani", "77 PALM CT"))["email"] == \
        "someone@example.com"


def test_different_streets_that_share_a_number_never_match():
    db = _addr_db()
    _board(db, "old@example.com", "Ann Kalani", "Old Mamalahoa Hwy 11")
    _board(db, "box@example.com", "Ann Kalani", "PO Box 5")
    for lines in (("Mamalahoa Hwy 11",), ("Kinoole St Apt 5",), ("Kalani Farm", "Box 5")):
        assert tr.resolve_by_address(db, _label("Ann Kalani", *lines))["email"] is None, lines


def test_a_second_line_that_is_a_unit_is_never_used_alone():
    db = _addr_db()
    _board(db, "apt@example.com", "Ann Kalani", "Apt 3")
    assert tr.resolve_by_address(db, _label("Ann Kalani", "12 Main St", "Apt 3"))["email"] is None


def test_a_failed_row_write_still_records_the_other_boxes(cx, monkeypatch):
    import cns_tracking_watcher as W
    real, seen = W.record_shipment, []
    def flaky(cx_, **f):
        seen.append(f["tracking_number"])
        if f["tracking_number"] == N[1]:
            raise RuntimeError("db hiccup")
        return real(cx_, **f)
    monkeypatch.setattr(W, "record_shipment", flaky)
    res, sends, _ = _run(cx, _confirmation(*[_block(n, *ANN) for n in N]), addr=_to("ann@example.com"))
    assert seen == N and len(sends) == 1
    assert shipment_exists(cx, N[0]) and shipment_exists(cx, N[2]) and not shipment_exists(cx, N[1])
    assert next(r for r in res if r["tracking"] == N[1])["record_error"].startswith("RuntimeError")
