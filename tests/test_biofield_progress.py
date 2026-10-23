"""Progress columns in the local Biofield app's "Your authored tests" list.

Glen, 2026-10-23. Plan: 00 System/pillar-inboxes/platform/
2026-10-23-authored-tests-progress-columns.md. Covers each column's source, the stored
link beating the email fallback, the one-call fetch, grey when every step is done,
the confirm on every button that acts, and the backfill writing nothing without --apply.
"""
import hashlib
import json
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from biofield_local_app import create_app  # noqa: E402
from dashboard import biofield_progress as bp  # noqa: E402
from dashboard.biofield_authoring import create_test, init_auth_tables  # noqa: E402
from dashboard.biofield_narrative import save_narrative  # noqa: E402
from dashboard.biofield_report_html import render_list_html  # noqa: E402
from dashboard import biofield_test_notified as btn  # noqa: E402


@pytest.fixture(autouse=True)
def _no_gate(monkeypatch):
    monkeypatch.delenv("CONSOLE_SECRET", raising=False)
    import dashboard as _d
    monkeypatch.setattr(_d, "CONSOLE_SECRET", "", raising=False)


def _order(oid, email="d@x.com", **kw):
    o = {"id": oid, "email": email, "created_at": "2026-10-18T20:00:00+00:00",
         "status": "confirmed", "total_cents": 50000, "portal_published": False,
         "invoice_sent_at": "", "pay_status": "unpaid", "paid_cents": 0, "paid_at": "",
         "biofield": True, "superseded_by_order_id": None}
    o.update(kw)
    return o


def _publish(cx, tid):
    cx.execute("CREATE TABLE IF NOT EXISTS biofield_portal_published("
               "test_id TEXT PRIMARY KEY, special_price_cents INTEGER, updated_at TEXT)")
    cx.execute("INSERT INTO biofield_portal_published VALUES (?,0,'2026-10-21T20:00:00')", (tid,))
    cx.commit()


def _done_test(cx, name="Donna Banks", email="d@x.com"):
    tid = create_test(cx, name=name, email=email, date="2026-10-17")
    save_narrative(cx, tid, "A saved narrative.")
    _publish(cx, tid)
    btn.mark(cx, tid)
    return tid


def _full_order(oid, email="d@x.com"):
    return _order(oid, email, portal_published=True,
                  invoice_sent_at="2026-10-19T01:00:00+00:00", pay_status="paid",
                  paid_cents=50000, paid_at="2026-10-20T02:00:00+00:00")


def _app(db, orders=(), err=None, calls=None, **kw):
    calls = calls if calls is not None else []

    def fetch(ids):
        calls.append(list(ids))
        return list(orders), err
    app = create_app(db_path=db, progress_orders=fetch, **kw)
    app.testing = True
    return app.test_client(), calls


def _row(html, tid):
    m = re.search(r"<tr[^>]*>(?:(?!</tr>).)*?/author/" + tid + r"'.*?</tr>", html, re.S)
    assert m, f"no row for {tid}"
    return m.group(0)


# ── Each column's source ─────────────────────────────────────────────────────

def test_report_columns_come_from_the_local_tables(tmp_path):
    db = str(tmp_path / "t.db")
    with sqlite3.connect(db) as cx:
        init_auth_tables(cx)
        t1 = create_test(cx, name="A", email="a@x.com", date="2026-10-17")
        t2 = create_test(cx, name="B", email="b@x.com", date="2026-10-17")
        save_narrative(cx, t1, "Saved narrative text")
        save_narrative(cx, t2, "   ")                 # saved but empty: not done
        _publish(cx, t1)
        btn.mark(cx, t1)
        local = bp.local_steps(cx)
    assert local[t1]["narrative_at"] and local[t1]["uploaded_at"] and local[t1]["emailed_at"]
    assert "narrative_at" not in local.get(t2, {})
    assert "uploaded_at" not in local.get(t2, {}) and "emailed_at" not in local.get(t2, {})


def test_invoice_columns_come_from_the_order(tmp_path):
    authored = [{"test_id": "a1", "email": "d@x.com"}]
    links = {"a1": 5}
    o = _order(5, total_cents=21150, portal_published=True,
               invoice_sent_at="2026-10-23T01:00:00+00:00", pay_status="unpaid", paid_cents=0)
    p = bp.build(authored, {}, links, [o])[0]
    assert p["amount_cents"] == 21150
    assert p["invoice_uploaded"] is True
    assert p["invoice_emailed"] == "2026-10-23T01:00:00+00:00"
    assert p["paid"] == "open"
    # Each flag from its own column: one set without the other.
    up = bp.build(authored, {}, links, [_order(5, portal_published=True, invoice_sent_at="")])[0]
    assert up["invoice_uploaded"] is True and up["invoice_emailed"] == ""
    em = bp.build(authored, {}, links, [_order(5, portal_published=False,
                                               invoice_sent_at="2026-10-23T01:00:00+00:00")])[0]
    assert em["invoice_uploaded"] is False and em["invoice_emailed"]


@pytest.mark.parametrize("pay_status, paid, total, want", [
    ("paid", 50000, 50000, "full"),
    ("paid", 0, 50000, "full"),            # paid before paid_cents was recorded
    ("paid", 15000, 50000, "full"),        # prod says paid: that is paid in full
    ("unpaid", 15000, 50000, "part"),
    ("claimed", 0, 50000, "claimed"),
    ("unpaid", 0, 50000, "open"),
])
def test_paid_comes_from_pay_status_and_paid_cents(pay_status, paid, total, want):
    assert bp.paid_state({"pay_status": pay_status, "paid_cents": paid,
                          "total_cents": total})[0] == want


@pytest.mark.parametrize("ledger, paid_cents, want", [
    (15000, 0, ("part", 15000)),           # the ledger has a part payment
    (15000, 50000, ("part", 15000)),       # the ledger wins over paid_cents
    (0, 15000, ("open", 0)),               # a refunded ledger is not part paid
    (None, 15000, ("part", 15000)),        # no ledger figure: paid_cents
])
def test_part_paid_comes_from_the_ledger(ledger, paid_cents, want):
    assert bp.paid_state({"pay_status": "unpaid", "paid_cents": paid_cents,
                          "ledger_paid_cents": ledger, "total_cents": 50000}) == want


def test_paid_shows_a_tick_in_full_and_the_amount_when_part_paid():
    t = {"test_id": "a1", "name": "D", "email": "d@x.com", "date": "2026-10-17"}
    full = dict(t, progress=bp.build([t], {}, {"a1": 5}, [_full_order(5)])[0])
    part = dict(t, progress=bp.build([t], {}, {"a1": 5},
                                     [_order(5, paid_cents=15000)])[0])
    rf = _row(render_list_html([], "", [full]), "a1")
    rp = _row(render_list_html([], "", [part]), "a1")
    assert "$150.00 paid" in rp
    assert "$150.00 paid" not in rf
    # the paid cell of the full order is a tick with the paid date (HST)
    cells = re.findall(r"<td[^>]*>.*?</td>", rf, re.S)
    assert "&#10003;" in cells[-1] and "19 Oct" in cells[-1]


def test_amount_cell_shows_the_order_total():
    t = {"test_id": "a1", "name": "D", "email": "d@x.com"}
    t["progress"] = bp.build([t], {}, {"a1": 5}, [_order(5, total_cents=101200)])[0]
    assert "$1,012.00" in _row(render_list_html([], "", [t]), "a1")


# ── The stored link beats the email fallback ─────────────────────────────────

def test_a_stored_link_beats_the_newest_order_by_email():
    t = {"test_id": "a1", "email": "d@x.com"}
    older, newer = _order(10, total_cents=21150), _order(20, total_cents=31150)
    o, basis = bp.match_order(t, 10, [older, newer])
    assert (o["id"], basis) == (10, "link")
    o, basis = bp.match_order(t, None, [older, newer])
    assert (o["id"], basis) == (20, "email")


def test_the_email_fallback_is_marked_approximate_and_cannot_act():
    t = {"test_id": "a1", "name": "D", "email": "d@x.com"}
    t["progress"] = bp.build([t], {}, {}, [_order(20)])[0]
    row = _row(render_list_html([], "", [t]), "a1")
    assert "class=approx" in row and "&asymp;" in row
    assert "stepAct" not in row                     # nothing acts on a guessed order
    linked = dict(t, progress=bp.build([t], {}, {"a1": 20}, [_order(20)])[0])
    lrow = _row(render_list_html([], "", [linked]), "a1")
    assert "class=approx" not in lrow and "&asymp;" not in lrow
    assert "stepAct" in lrow


def test_a_replaced_linked_order_shows_its_replacement():
    t = {"test_id": "a1", "email": "d@x.com"}
    o, basis = bp.match_order(t, 10, [_order(10, superseded_by_order_id=11), _order(11)])
    assert (o["id"], basis) == (11, "link")


def test_a_linked_order_prod_did_not_return_is_not_guessed():
    t = {"test_id": "a1", "email": "d@x.com"}
    o, basis = bp.match_order(t, 99, [_order(20)])
    assert o == {"id": 99, "missing": True} and basis == "link"


def test_making_an_invoice_stores_the_link(tmp_path):
    db = str(tmp_path / "t.db")
    with sqlite3.connect(db) as cx:
        init_auth_tables(cx)
        tid = create_test(cx, name="D", email="d@x.com", date="2026-10-17")
        from dashboard.biofield_authoring import add_chain_row
        add_chain_row(cx, tid, layer=1, head="", most_affected="", remedy="Liver Support")
    app = create_app(
        db_path=db, invoice_fetch_catalog=lambda: [{"slug": "liver-support", "name": "Liver Support"}],
        invoice_create=lambda *a, **k: {"ok": True, "order_id": 233, "total_cents": 100,
                                        "accepted_slugs": []},
        invoice_link=lambda oid: {"ok": True, "print_url": "x"},
        invoice_paid_check=lambda e: {"paid": False},
        invoice_latest=lambda e: {"ok": False})
    app.testing = True
    assert app.test_client().post(f"/author/{tid}/invoice").get_json()["ok"]
    with sqlite3.connect(db) as cx:
        assert bp.stored_links(cx) == {tid: 233}


# ── One call for the whole list ──────────────────────────────────────────────

def test_the_list_fetches_orders_once_for_every_line(tmp_path):
    db = str(tmp_path / "t.db")
    with sqlite3.connect(db) as cx:
        init_auth_tables(cx)
        tids = [create_test(cx, name=f"C{i}", email=f"c{i}@x.com", date="2026-10-17")
                for i in range(4)]
        bp.link_order(cx, tids[0], 41)
        bp.link_order(cx, tids[1], 42)
    client, calls = _app(db, orders=[_order(41, "c0@x.com"), _order(42, "c1@x.com")])
    r = client.get("/")
    assert r.status_code == 200
    assert len(calls) == 1, calls
    assert calls[0] == [41, 42]


def test_fetch_orders_is_a_single_get(monkeypatch):
    monkeypatch.setenv("CONSOLE_SECRET", "k")
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://prod.example")
    seen = []

    class _R:
        def __init__(self, body):
            self.body = body

        def read(self):
            return self.body

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=None):
        seen.append((req.get_method(), req.full_url, req.headers))
        return _R(json.dumps({"ok": True, "orders": [_order(1)]}).encode())
    monkeypatch.setattr(bp.urllib.request, "urlopen", fake_urlopen)
    orders, err = bp.fetch_orders([3, 1, 2, 2])
    assert err is None and [o["id"] for o in orders] == [1]
    assert len(seen) == 1
    method, url, headers = seen[0]
    assert method == "GET"
    assert url == "https://prod.example/api/console/biofield-progress-orders?ids=1%2C2%2C3"
    assert "k" not in url                              # key in the header only


def test_the_list_renders_when_orders_are_unavailable(tmp_path):
    db = str(tmp_path / "t.db")
    with sqlite3.connect(db) as cx:
        init_auth_tables(cx)
        create_test(cx, name="C", email="c@x.com", date="2026-10-17")
    client, _ = _app(db, err="the order lookup timed out")
    html = client.get("/").get_data(as_text=True)
    assert "Invoice columns unavailable: the order lookup timed out" in html


# ── Grey when every step is done ─────────────────────────────────────────────

def test_a_line_with_every_step_done_is_grey_and_stays_listed(tmp_path):
    db = str(tmp_path / "t.db")
    with sqlite3.connect(db) as cx:
        init_auth_tables(cx)
        tid = _done_test(cx)
        bp.link_order(cx, tid, 5)
    client, _ = _app(db, orders=[_full_order(5)])
    row = _row(client.get("/").get_data(as_text=True), tid)
    assert row.startswith("<tr class=finished>")


def _all_done():
    return {"report_done": "x", "report_uploaded": "x", "report_emailed": "x", "basis": "link",
            "order_id": 5, "invoice_uploaded": True, "invoice_emailed": "x", "paid": "full"}


@pytest.mark.parametrize("undo", ["report_done", "report_uploaded", "report_emailed",
                                  "invoice_uploaded", "paid", "no_order", "cancelled"])
def test_any_open_step_keeps_the_line_live(undo):
    p = _all_done()
    assert bp.is_done(p)
    if undo == "no_order":
        p["order_id"] = None
    elif undo == "paid":
        p["paid"] = "part"
    elif undo == "cancelled":
        p["cancelled"] = True
    else:
        p[undo] = "" if undo != "invoice_uploaded" else False
    assert not bp.is_done(p)


def test_an_unpaid_order_needs_the_invoice_email_to_grey():
    p = dict(_all_done(), paid="open", invoice_emailed="")
    assert not bp.is_done(p)


def test_a_paid_order_greys_without_an_invoice_email():
    assert bp.is_done(dict(_all_done(), invoice_emailed=""))


@pytest.mark.parametrize("basis", ["email", "shared"])
def test_a_guessed_or_shared_order_never_greys_a_line(basis):
    assert not bp.is_done(dict(_all_done(), basis=basis))
    t = {"test_id": "a1", "name": "D", "email": "d@x.com"}
    links = {} if basis == "email" else {"a1": 5, "a2": 5}
    t["progress"] = bp.build([t], {"a1": {"narrative_at": "x", "uploaded_at": "x",
                                          "emailed_at": "x"}}, links, [_full_order(5)])[0]
    assert t["progress"]["basis"] == basis
    assert "class=finished" not in render_list_html([], "", [t])


def test_a_paid_order_hides_the_email_button():
    t = {"test_id": "a1", "name": "D", "email": "d@x.com"}
    t["progress"] = bp.build([t], {}, {"a1": 5}, [_order(5, pay_status="paid")])[0]
    row = _row(render_list_html([], "", [t]), "a1")
    assert "invoice-email" not in row and "invoice-publish" in row


def test_no_charge_with_no_invoice_needs_no_invoice_steps():
    p = {"report_done": "x", "report_uploaded": "x", "report_emailed": "x",
         "order_id": None, "no_charge": True}
    assert bp.is_done(p)


# ── Every button that sends or publishes asks first ──────────────────────────

def _open_linked_row(**kw):
    t = {"test_id": "a7", "name": "Donna Banks", "email": "d@x.com"}
    o = _order(5, total_cents=21150, **kw)
    t["progress"] = bp.build([t], {}, {"a7": 5}, [o])[0]
    return render_list_html([], "", [t])


def test_the_confirm_names_the_addressee_and_total_and_the_post_carries_them():
    html = _open_linked_row(email="payer@x.com", recipient_email="client@x.com")
    for b in re.findall(r"<button[^>]*class=step[^>]*>", html):
        assert 'data-amount="21150"' in b and 'data-recipient="payer@x.com"' in b
        assert "payer@x.com" in b.split("data-confirm=")[1] and "$211.50" in b
        assert "client@x.com" in b.split("data-confirm=")[1]   # named, not the addressee


def test_every_acting_button_carries_a_confirm():
    html = _open_linked_row()
    buttons = re.findall(r"<button[^>]*class=step[^>]*>", html)
    assert len(buttons) == 2                                   # invoice publish + email
    for b in buttons:
        assert 'onclick="stepAct(this)"' in b
        assert re.search(r'data-confirm="[^"]{20,}"', b), b
    assert any("invoice-publish" in b and "portal" in b for b in buttons)
    assert any("invoice-email" in b and "real email" in b for b in buttons)


def test_report_uploaded_only_opens_publish_to_portal():
    html = _open_linked_row()
    row = _row(html, "a7")
    cells = re.findall(r"<td[^>]*>.*?</td>", row, re.S)
    assert 'href="/test/a7#publish"' in cells[3] and "<button" not in cells[3]
    assert 'href="/test/a7#publish"' in cells[4] and "<button" not in cells[4]
    assert "publish-portal" not in html                       # never publishes directly
    assert 'href="/author/a7#narrative"' in cells[2]


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
@pytest.mark.parametrize("answer, posts", [(False, 0), (True, 1)])
def test_steps_js_asks_before_it_posts(answer, posts):
    html = _open_linked_row()
    js = [b for b in re.findall(r"<script>(.*?)</script>", html, re.S) if "stepAct" in b][0]
    body = js + """
let calls = [];
globalThis.confirm = () => %s;
globalThis.alert = () => {};
globalThis.fetch = async (u, o) => { calls.push([u, JSON.parse(o.body)]);
  return {json: async () => ({ok: true})}; };
const btn = {dataset: {url: '/author/a7/progress/invoice-email', order: '5', confirm: 'Sure?',
  amount: '21150', recipient: 'd@x.com'}, textContent: 'Email', disabled: false, parentNode: {}};
stepAct(btn).then(() => console.log(JSON.stringify(calls)));
""" % ("true" if answer else "false")
    with tempfile.TemporaryDirectory() as d:
        f = Path(d) / "s.js"
        f.write_text(body)
        r = subprocess.run(["node", str(f)], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    calls = json.loads(r.stdout.strip())
    assert len(calls) == posts
    if posts:
        assert calls[0][1] == {"order_id": 5, "confirm": True, "amount_cents": 21150,
                               "recipient": "d@x.com"}


def _run_js(replies, confirms):
    html = _open_linked_row()
    js = [b for b in re.findall(r"<script>(.*?)</script>", html, re.S) if "stepAct" in b][0]
    body = js + """
let calls = [], asked = [];
const replies = %s, answers = %s;
globalThis.confirm = (m) => { asked.push(m); return answers.shift(); };
globalThis.alert = () => {};
globalThis.fetch = async (u, o) => { calls.push(JSON.parse(o.body)); const r = replies.shift();
  if (r === 'throw') throw new Error('net'); return {json: async () => r}; };
const btn = {dataset: {url: '/x', order: '5', confirm: 'Sure?', amount: '21150',
  recipient: 'd@x.com'}, textContent: 'Email', disabled: false, parentNode: {}};
stepAct(btn).then(() => console.log(JSON.stringify({calls, asked, label: btn.textContent,
  disabled: btn.disabled})));
""" % (json.dumps(replies), json.dumps(confirms))
    with tempfile.TemporaryDirectory() as d:
        f = Path(d) / "s.js"
        f.write_text(body)
        r = subprocess.run(["node", str(f)], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout.strip())


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
@pytest.mark.parametrize("reply", [{"ok": False, "unknown": True}, "throw"])
def test_an_unknown_send_leaves_the_button_disabled(reply):
    out = _run_js([reply], [True])
    assert out["disabled"] is True
    assert out["label"] == "result unknown, reload before retrying"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
@pytest.mark.parametrize("second, posts", [(False, 1), (True, 2)])
def test_a_recent_send_needs_a_second_explicit_confirm(second, posts):
    out = _run_js([{"ok": False, "recent": True, "sent_at": "now"}, {"ok": True}], [True, second])
    assert len(out["asked"]) == 2 and "again" in out["asked"][1]
    assert len(out["calls"]) == posts
    assert "resend" not in out["calls"][0]
    if posts == 2:
        assert out["calls"][1]["resend"] is True


class _Prod:
    """Prod's orders as the click-time re-read sees them."""
    def __init__(self):
        self.orders = {5: _order(5)}
        self.reads = []

    def fetch(self, oid):
        self.reads.append(oid)
        return list(self.orders.values()), None


@pytest.fixture
def acting(tmp_path):
    db = str(tmp_path / "t.db")
    with sqlite3.connect(db) as cx:
        init_auth_tables(cx)
        tid = create_test(cx, name="D", email="d@x.com", date="2026-10-17")
        bp.link_order(cx, tid, 5)
    sent, prod = [], _Prod()
    client, _ = _app(db, invoice_publish=lambda oid: sent.append(("publish", oid)) or {"ok": True},
                     invoice_send=lambda oid: sent.append(("email", oid)) or {"ok": True},
                     order_fetch=prod.fetch)
    client._db, client._prod = db, prod
    return client, tid, sent


def _ok_body(oid=5, **kw):
    return dict({"order_id": oid, "amount_cents": 50000, "recipient": "d@x.com",
                 "confirm": True}, **kw)


@pytest.mark.parametrize("path", ["invoice-publish", "invoice-email"])
def test_the_server_refuses_without_a_confirm(acting, path):
    client, tid, sent = acting
    for c in (None, "yes", 1):
        body = _ok_body()
        if c is None:
            body.pop("confirm")
        else:
            body["confirm"] = c
        r = client.post(f"/author/{tid}/progress/{path}", json=body)
        assert r.status_code == 400
    assert sent == []


@pytest.mark.parametrize("path, kind", [("invoice-publish", "publish"), ("invoice-email", "email")])
def test_a_confirmed_click_acts_on_the_stored_order_only(acting, path, kind):
    client, tid, sent = acting
    r = client.post(f"/author/{tid}/progress/{path}", json=_ok_body(6))
    assert r.status_code == 409 and sent == []
    r = client.post(f"/author/{tid}/progress/{path}", json=_ok_body())
    assert r.get_json()["ok"] and sent == [(kind, 5)]
    assert client._prod.reads == [5, 5]                    # re-read at each click


@pytest.mark.parametrize("path", ["invoice-publish", "invoice-email"])
@pytest.mark.parametrize("change", [{"total_cents": 60000}, {"email": "other@x.com"}])
def test_a_changed_order_is_refused(acting, path, change):
    client, tid, sent = acting
    client._prod.orders[5].update(change)
    r = client.post(f"/author/{tid}/progress/{path}", json=_ok_body())
    assert r.status_code == 409 and r.get_json()["changed"] and sent == []


@pytest.mark.parametrize("path", ["invoice-publish", "invoice-email"])
def test_a_cancelled_order_is_refused(acting, path):
    client, tid, sent = acting
    client._prod.orders[5]["status"] = "cancelled"
    r = client.post(f"/author/{tid}/progress/{path}", json=_ok_body())
    assert r.status_code == 409 and "cancelled" in r.get_json()["error"] and sent == []


def test_a_cancelled_order_shows_on_the_page_with_no_action():
    html = _open_linked_row(status="cancelled")
    row = _row(html, "a7")
    assert "#5 cancelled" in row and "stepAct" not in row


@pytest.mark.parametrize("minutes, resend, sends", [
    (5, False, 0), (5, True, 1), (20, False, 1)])
def test_a_recent_invoice_email_needs_resend(acting, minutes, resend, sends):
    import datetime as dt
    client, tid, sent = acting
    at = dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=minutes)
    client._prod.orders[5]["invoice_sent_at"] = at.isoformat()
    body = _ok_body(resend=True) if resend else _ok_body()
    r = client.post(f"/author/{tid}/progress/invoice-email", json=body)
    assert len(sent) == sends
    if not sends:
        assert r.status_code == 409 and r.get_json()["recent"] is True


def test_a_shared_order_is_refused_and_shown_approximate(acting):
    client, tid, sent = acting
    with sqlite3.connect(client._db) as cx:     # an old row from before the refusal
        bp.ensure_schema(cx)
        cx.execute("INSERT INTO biofield_test_orders VALUES ('a99', 5, 'x', 'x')")
        cx.commit()
    for path in ("invoice-publish", "invoice-email"):
        r = client.post(f"/author/{tid}/progress/{path}", json=_ok_body())
        assert r.status_code == 409 and "a99" in r.get_json()["error"]
    assert sent == []
    t = {"test_id": tid, "name": "D", "email": "d@x.com"}
    t["progress"] = bp.build([t], {}, {tid: 5, "a99": 5}, [_order(5)])[0]
    row = _row(render_list_html([], "", [t]), tid)
    assert "&asymp;" in row and "stepAct" not in row


def test_a_replaced_stored_order_acts_on_its_replacement(acting):
    client, tid, sent = acting
    client._prod.orders[5]["superseded_by_order_id"] = 8
    client._prod.orders[8] = _order(8, total_cents=42000)
    # The page shows the replacement, and its button carries the replacement's id.
    t = {"test_id": tid, "name": "D", "email": "d@x.com"}
    t["progress"] = bp.build([t], {}, {tid: 5}, list(client._prod.orders.values()))[0]
    html = render_list_html([], "", [t])
    btn = re.search(r'<button[^>]*invoice-publish[^>]*>', html).group(0)
    oid = int(re.search(r'data-order="(\d+)"', btn).group(1))
    amt = int(re.search(r'data-amount="(\d+)"', btn).group(1))
    assert (oid, amt) == (8, 42000)
    r = client.post(f"/author/{tid}/progress/invoice-publish",
                    json=_ok_body(oid, amount_cents=amt))
    assert r.get_json()["ok"] and sent == [("publish", 8)]
    with sqlite3.connect(client._db) as cx:
        assert bp.stored_links(cx) == {tid: 8}             # the link moved


def test_a_second_test_cannot_store_an_order_another_test_holds(tmp_path):
    db = str(tmp_path / "t.db")
    with sqlite3.connect(db) as cx:
        assert bp.link_order(cx, "a1", 5) == (True, "")
        ok, why = bp.link_order(cx, "a2", 5)
        assert not ok and "a1" in why
        assert bp.link_order(cx, "a1", 5)[0]                # the holder may re-raise
        assert bp.stored_links(cx) == {"a1": 5}


def test_a_household_raise_does_not_link_the_second_test(tmp_path):
    db = str(tmp_path / "t.db")
    with sqlite3.connect(db) as cx:
        init_auth_tables(cx)
        from dashboard.biofield_authoring import add_chain_row
        tids = []
        for nm in ("A", "B"):
            t = create_test(cx, name=nm, email=f"{nm.lower()}@x.com", date="2026-10-17")
            add_chain_row(cx, t, layer=1, head="", most_affected="", remedy="Liver Support")
            tids.append(t)
    app = create_app(
        db_path=db, invoice_fetch_catalog=lambda: [{"slug": "liver-support", "name": "Liver Support"}],
        invoice_create=lambda *a, **k: {"ok": True, "order_id": 233, "total_cents": 100,
                                        "accepted_slugs": []},
        invoice_link=lambda oid: {"ok": True, "print_url": "x"},
        invoice_paid_check=lambda e: {"paid": False},
        invoice_latest=lambda e: {"ok": False})
    app.testing = True
    c = app.test_client()
    assert c.post(f"/author/{tids[0]}/invoice").get_json()["ok"]
    j = c.post(f"/author/{tids[1]}/invoice").get_json()
    assert j["ok"] and "Not stored against this test" in j["warning"]
    with sqlite3.connect(db) as cx:
        assert bp.stored_links(cx) == {tids[0]: 233}


def test_an_order_page_link_keeps_the_key_out_of_the_list(tmp_path, monkeypatch):
    from dashboard import biofield_invoice as bi
    monkeypatch.setattr(bi, "default_orders_link", lambda oid: f"https://prod/console/orders?order={oid}&key=SECRET")
    db = str(tmp_path / "t.db")
    with sqlite3.connect(db) as cx:
        init_auth_tables(cx)
        tid = create_test(cx, name="D", email="d@x.com", date="2026-10-17")
        bp.link_order(cx, tid, 5)
    client, _ = _app(db, orders=[_order(5)])
    html = client.get("/").get_data(as_text=True)
    assert "SECRET" not in html and "/progress/order/5" in html
    r = client.get("/progress/order/5")
    assert r.status_code == 302 and "order=5" in r.headers["Location"]


# ── The backfill writes nothing without --apply ──────────────────────────────

def _seed_backfill(db):
    with sqlite3.connect(db) as cx:
        init_auth_tables(cx)
        a = create_test(cx, name="A", email="a@x.com", date="2026-10-17")
        b = create_test(cx, name="B", email="b@x.com", date="2026-10-17")
        c = create_test(cx, name="C", email="c@x.com", date="2026-10-17")
    return a, b, c


def _fake_fetch(email):
    return {
        "a@x.com": [_order(31, "a@x.com")],
        "b@x.com": [_order(32, "b@x.com"), _order(33, "b@x.com")],
        "c@x.com": [],
    }[email], None


def _digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def test_the_backfill_dry_run_writes_nothing(tmp_path):
    sys.path.insert(0, str(ROOT / "scripts"))
    import backfill_biofield_test_orders as bf
    db = str(tmp_path / "t.db")
    a, b, c = _seed_backfill(db)
    before = _digest(db)
    lines = []
    props, n = bf.run(db, apply=False, fetch=_fake_fetch, out=lines.append)
    assert n == 0 and _digest(db) == before
    with sqlite3.connect(db) as cx:
        assert not bp._has_table(cx, bp.LINK_TABLE)
    status = {p["test_id"]: p["status"] for p in props}
    assert status == {a: "propose", b: "ambiguous", c: "none"}
    assert any("DRY RUN" in ln for ln in lines)
    assert not any(name in "\n".join(lines) for name in ("a@x.com", "b@x.com"))


def test_the_backfill_cli_without_apply_writes_nothing(tmp_path, monkeypatch):
    sys.path.insert(0, str(ROOT / "scripts"))
    import backfill_biofield_test_orders as bf
    from dashboard import biofield_invoice as bi
    monkeypatch.setattr(bi, "client_orders_with_status", _fake_fetch)
    db = str(tmp_path / "t.db")
    _seed_backfill(db)
    before = _digest(db)
    bf.main(["--db", db])
    assert _digest(db) == before


def test_the_backfill_with_apply_writes_only_single_candidates(tmp_path):
    sys.path.insert(0, str(ROOT / "scripts"))
    import backfill_biofield_test_orders as bf
    db = str(tmp_path / "t.db")
    a, b, c = _seed_backfill(db)
    _, n = bf.run(db, apply=True, fetch=_fake_fetch, out=lambda s: None)
    assert n == 1
    with sqlite3.connect(db) as cx:
        assert bp.stored_links(cx) == {a: 31}


def test_the_backfill_never_overwrites_a_link_made_after_the_dry_run(tmp_path):
    sys.path.insert(0, str(ROOT / "scripts"))
    import backfill_biofield_test_orders as bf
    db = str(tmp_path / "t.db")
    a, b, c = _seed_backfill(db)
    props, _ = bf.run(db, apply=False, fetch=_fake_fetch, out=lambda s: None)
    assert [p for p in props if p["status"] == "propose"][0]["test_id"] == a
    with sqlite3.connect(db) as cx:          # Glen raises a's invoice in between
        bp.link_order(cx, a, 777)
        lines = []
        assert bp.apply_links(cx, props, out=lines.append) == 0
        assert bp.stored_links(cx) == {a: 777}
    assert any("already has order #777" in ln for ln in lines)


def test_the_backfill_skips_an_order_another_test_gained(tmp_path):
    db = str(tmp_path / "t.db")
    props = [{"test_id": "a1", "status": "propose", "order_id": 31, "candidates": [31]}]
    with sqlite3.connect(db) as cx:
        bp.link_order(cx, "a9", 31)
        assert bp.apply_links(cx, props, out=lambda s: None) == 0
        assert bp.stored_links(cx) == {"a9": 31}


def test_backfill_windows_end_at_the_next_test_and_conflicts_are_not_written():
    authored = [{"test_id": "a1", "email": "x@x.com", "date": "2026-09-30"},
                {"test_id": "a2", "email": "x@x.com", "date": "2026-10-20"},
                {"test_id": "a3", "email": "y@x.com", "date": "2026-10-01"}]
    orders = {"x@x.com": [_order(5, "x@x.com", created_at="2026-10-01T01:00:00+00:00"),
                          _order(9, "x@x.com", created_at="2026-10-21T01:00:00+00:00")],
              "y@x.com": [_order(9, "y@x.com", created_at="2026-10-02T01:00:00+00:00")]}
    props = {p["test_id"]: p for p in bp.propose_links(authored, {}, orders)}
    assert props["a1"]["status"] == "propose" and props["a1"]["order_id"] == 5
    # a2 and a3 both claim order 9 (only possible with bad data): neither is written
    assert props["a2"]["status"] == "conflict" and props["a3"]["status"] == "conflict"


def test_backfill_allows_an_invoice_raised_the_evening_before_the_test_date():
    """Measured 2026-10-23: order 233 was created 2026-10-16 14:26 HST for a test dated
    the 17th, while the same client's earlier test ran up to that date."""
    authored = [{"test_id": "a25", "email": "s@x.com", "date": "2026-08-10"},
                {"test_id": "a52", "email": "s@x.com", "date": "2026-10-17"}]
    orders = {"s@x.com": [_order(122, "s@x.com", created_at="2026-08-10T22:00:00+00:00"),
                          _order(233, "s@x.com", created_at="2026-10-17T00:26:25+00:00")]}
    props = {p["test_id"]: p for p in bp.propose_links(authored, {}, orders)}
    assert props["a25"]["order_id"] == 122 and props["a52"]["order_id"] == 233


# ── The prod endpoint: one read-only call ────────────────────────────────────

def _prod(tmp_path, monkeypatch):
    import importlib
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    db = str(tmp_path / "chat_log.db")
    from dashboard import orders as O
    bio = [{"slug": "biofield-analysis", "qty": 1}]
    zinc = [{"slug": "zinc", "qty": 1}]
    with sqlite3.connect(db) as cx:
        O.init_orders_table(cx)
        ids = {}
        for ref, email, items in (("A", "a@x.com", bio), ("B", "b@x.com", zinc),
                                  ("C", "c@x.com", [{"slug": "zinc", "qty": 1, "source": "biofield"}]),
                                  ("D", "d@x.com", zinc), ("E", "e@x.com", bio), ("F", "e@x.com", zinc)):
            ids[ref] = O.upsert_order(cx, source="manual", external_ref=ref, email=email,
                                      items=items, total_cents=1000)
        # B (no Biofield line) was replaced by D; E (Biofield) was replaced by F.
        cx.execute("UPDATE orders SET superseded_by_order_id=? WHERE id=?", (ids["D"], ids["B"]))
        cx.execute("UPDATE orders SET superseded_by_order_id=? WHERE id=?", (ids["F"], ids["E"]))
        cx.commit()
    import app as appmod
    importlib.reload(appmod)
    return appmod, db, ids


def _orders_dump(db):
    with sqlite3.connect(db) as cx:
        return cx.execute("SELECT * FROM orders ORDER BY id").fetchall()


def _owner(appmod, monkeypatch, role="owner"):
    from types import SimpleNamespace
    monkeypatch.setattr(appmod, "_bos_actor", lambda: SimpleNamespace(role=role, name=role))


def test_the_prod_endpoint_returns_biofield_orders_and_asked_ids(tmp_path, monkeypatch):
    appmod, db, ids = _prod(tmp_path, monkeypatch)
    _owner(appmod, monkeypatch)
    c = appmod.app.test_client()
    before = _orders_dump(db)
    r = c.get(f"/api/console/biofield-progress-orders?ids={ids['B']},{ids['E']}")
    assert r.status_code == 200
    got = {o["id"]: o for o in r.get_json()["orders"]}
    # A and C carry a Biofield line. E was asked for and carries one; F replaced it.
    # B was asked for but holds no Biofield line, so neither it nor D comes back.
    assert set(got) == {ids["A"], ids["C"], ids["E"], ids["F"]}
    assert got[ids["A"]]["biofield"] and not got[ids["F"]]["biofield"]
    assert set(got[ids["A"]]) >= {"total_cents", "portal_published", "invoice_sent_at",
                                  "pay_status", "paid_cents", "recipient_email",
                                  "ledger_paid_cents"}
    assert _orders_dump(db) == before                         # read-only


def test_the_prod_endpoint_only_mode_returns_just_the_named_order(tmp_path, monkeypatch):
    appmod, _, ids = _prod(tmp_path, monkeypatch)
    _owner(appmod, monkeypatch)
    r = appmod.app.test_client().get(
        f"/api/console/biofield-progress-orders?only=1&ids={ids['E']}")
    assert {o["id"] for o in r.get_json()["orders"]} == {ids["E"], ids["F"]}


def test_the_prod_endpoint_reports_ledger_payments(tmp_path, monkeypatch):
    appmod, db, ids = _prod(tmp_path, monkeypatch)
    from dashboard import order_payments as OP
    with sqlite3.connect(db) as cx:
        OP.ensure_table(cx)
        for kind, amt in (("payment", 400), ("refund", 100)):
            cx.execute("INSERT INTO order_payments (order_id, kind, amount_cents, status, "
                       "created_at) VALUES (?,?,?,?,?)", (ids["A"], kind, amt, "active", "2026-10-23"))
        cx.commit()
    _owner(appmod, monkeypatch)
    got = {o["id"]: o for o in appmod.app.test_client().get(
        "/api/console/biofield-progress-orders").get_json()["orders"]}
    assert got[ids["A"]]["ledger_paid_cents"] == 300
    assert got[ids["C"]]["ledger_paid_cents"] is None


@pytest.mark.parametrize("actor", [None, "va", "ops"])
def test_the_prod_endpoint_is_owner_only(tmp_path, monkeypatch, actor):
    appmod, _, _ = _prod(tmp_path, monkeypatch)
    if actor is None:
        monkeypatch.setattr(appmod, "_bos_actor", lambda: None)
    else:
        _owner(appmod, monkeypatch, actor)
    r = appmod.app.test_client().get("/api/console/biofield-progress-orders")
    assert r.status_code == 401


@pytest.mark.parametrize("reply, ok", [
    ({"status": "done", "result": {}}, True),
    ({"status": "failed", "result": {"error": "invoice pay-link is disabled"}}, False),
    ({"status": "needs_confirmation"}, False),
])
def test_the_invoice_email_runs_prods_send_action(monkeypatch, reply, ok):
    from dashboard import biofield_invoice as bi
    monkeypatch.setenv("CONSOLE_SECRET", "k")
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://prod.example")
    seen = []

    class _R:
        def read(self):
            return json.dumps(reply).encode()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=None):
        seen.append((req.get_method(), req.full_url, json.loads(req.data), req.headers))
        return _R()
    monkeypatch.setattr(bi.urllib.request, "urlopen", fake_urlopen)
    res = bi.default_send_invoice(233)
    assert res["ok"] is ok
    method, url, body, headers = seen[0]
    assert (method, url, body) == ("POST", "https://prod.example/api/action/orders.send_invoice",
                                   {"order_id": 233})
    assert headers.get("X-console-key") == "k" and "k" not in url.split("/")[-1]


def test_no_reply_from_the_send_is_unknown_not_failed(monkeypatch):
    from dashboard import biofield_invoice as bi
    monkeypatch.setenv("CONSOLE_SECRET", "k")

    def boom(req, timeout=None):
        raise TimeoutError("timed out")
    monkeypatch.setattr(bi.urllib.request, "urlopen", boom)
    res = bi.default_send_invoice(233)
    assert res["ok"] is False and res["unknown"] is True
