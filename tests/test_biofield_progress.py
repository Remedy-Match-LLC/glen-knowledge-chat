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
    ("unpaid", 15000, 50000, "part"),
    ("paid", 15000, 50000, "part"),
    ("claimed", 0, 50000, "claimed"),
    ("unpaid", 0, 50000, "open"),
])
def test_paid_comes_from_pay_status_and_paid_cents(pay_status, paid, total, want):
    assert bp.paid_state({"pay_status": pay_status, "paid_cents": paid,
                          "total_cents": total})[0] == want


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


@pytest.mark.parametrize("undo", ["report_done", "report_uploaded", "report_emailed",
                                  "invoice_uploaded", "invoice_emailed", "paid", "no_order"])
def test_any_open_step_keeps_the_line_live(undo):
    p = {"report_done": "x", "report_uploaded": "x", "report_emailed": "x",
         "order_id": 5, "invoice_uploaded": True, "invoice_emailed": "x", "paid": "full"}
    assert bp.is_done(p)
    if undo == "no_order":
        p["order_id"] = None
    elif undo == "paid":
        p["paid"] = "part"
    else:
        p[undo] = "" if undo != "invoice_uploaded" else False
    assert not bp.is_done(p)


def test_no_charge_with_no_invoice_needs_no_invoice_steps():
    p = {"report_done": "x", "report_uploaded": "x", "report_emailed": "x",
         "order_id": None, "no_charge": True}
    assert bp.is_done(p)


# ── Every button that sends or publishes asks first ──────────────────────────

def _open_linked_row():
    t = {"test_id": "a7", "name": "Donna Banks", "email": "d@x.com"}
    t["progress"] = bp.build([t], {}, {"a7": 5}, [_order(5, total_cents=21150)])[0]
    return render_list_html([], "", [t])


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
const btn = {dataset: {url: '/author/a7/progress/invoice-email', order: '5', confirm: 'Sure?'},
  textContent: 'Email', disabled: false, parentNode: {}};
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
        assert calls[0][1] == {"order_id": 5, "confirm": True}


@pytest.fixture
def acting(tmp_path):
    db = str(tmp_path / "t.db")
    with sqlite3.connect(db) as cx:
        init_auth_tables(cx)
        tid = create_test(cx, name="D", email="d@x.com", date="2026-10-17")
        bp.link_order(cx, tid, 5)
    sent = []
    client, _ = _app(db, invoice_publish=lambda oid: sent.append(("publish", oid)) or {"ok": True},
                     invoice_send=lambda oid: sent.append(("email", oid)) or {"ok": True})
    return client, tid, sent


@pytest.mark.parametrize("path", ["invoice-publish", "invoice-email"])
def test_the_server_refuses_without_a_confirm(acting, path):
    client, tid, sent = acting
    for body in ({"order_id": 5}, {"order_id": 5, "confirm": "yes"}, {"order_id": 5, "confirm": 1}):
        r = client.post(f"/author/{tid}/progress/{path}", json=body)
        assert r.status_code == 400
    assert sent == []


@pytest.mark.parametrize("path, kind", [("invoice-publish", "publish"), ("invoice-email", "email")])
def test_a_confirmed_click_acts_on_the_stored_order_only(acting, path, kind):
    client, tid, sent = acting
    r = client.post(f"/author/{tid}/progress/{path}", json={"order_id": 6, "confirm": True})
    assert r.status_code == 409 and sent == []
    r = client.post(f"/author/{tid}/progress/{path}", json={"order_id": 5, "confirm": True})
    assert r.get_json()["ok"] and sent == [(kind, 5)]


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
    with sqlite3.connect(db) as cx:
        O.init_orders_table(cx)
        ids = {}
        for ref, email, items in (("A", "a@x.com", bio), ("B", "b@x.com", [{"slug": "zinc", "qty": 1}]),
                                  ("C", "c@x.com", [{"slug": "zinc", "qty": 1, "source": "biofield"}]),
                                  ("D", "d@x.com", [{"slug": "zinc", "qty": 1}])):
            ids[ref] = O.upsert_order(cx, source="manual", external_ref=ref, email=email,
                                      items=items, total_cents=1000)
        cx.execute("UPDATE orders SET superseded_by_order_id=? WHERE id=?", (ids["D"], ids["B"]))
        cx.commit()
    import app as appmod
    importlib.reload(appmod)
    return appmod, db, ids


def _orders_dump(db):
    with sqlite3.connect(db) as cx:
        return cx.execute("SELECT * FROM orders ORDER BY id").fetchall()


def test_the_prod_endpoint_returns_biofield_orders_and_asked_ids(tmp_path, monkeypatch):
    appmod, db, ids = _prod(tmp_path, monkeypatch)
    monkeypatch.setattr(appmod, "_bos_actor", lambda: {"role": "owner"})
    c = appmod.app.test_client()
    before = _orders_dump(db)
    r = c.get(f"/api/console/biofield-progress-orders?ids={ids['B']}")
    assert r.status_code == 200
    got = {o["id"]: o for o in r.get_json()["orders"]}
    # A and C carry a Biofield line; B was asked for; D replaced B
    assert set(got) == {ids["A"], ids["B"], ids["C"], ids["D"]}
    assert got[ids["A"]]["biofield"] and not got[ids["B"]]["biofield"]
    assert set(got[ids["A"]]) >= {"total_cents", "portal_published", "invoice_sent_at",
                                  "pay_status", "paid_cents"}
    assert _orders_dump(db) == before                         # read-only


def test_the_prod_endpoint_needs_the_console_key(tmp_path, monkeypatch):
    appmod, _, _ = _prod(tmp_path, monkeypatch)
    monkeypatch.setattr(appmod, "_bos_actor", lambda: None)
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
