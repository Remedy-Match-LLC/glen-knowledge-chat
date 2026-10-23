"""Choosing Zelle on an invoice shows who to pay, the balance owed and the memo.

Glen, 2026-10-23: "Zelle instructions should be included on our invoice when the
client selects Zelle". The printed invoice carries one Zelle line too.

The server half is checked through the routes. The page half is extracted from
static/invoice.html and EXECUTED under node, so a comment cannot pass for code.
"""
import importlib
import json
import pathlib
import re
import shutil
import sqlite3
import subprocess
import sys

import pytest

repo = pathlib.Path(__file__).resolve().parent.parent
if str(repo) not in sys.path:
    sys.path.insert(0, str(repo))

PAGE = repo / "static" / "invoice.html"
node = shutil.which("node")
TO = "payee@example.com"


@pytest.fixture
def env(tmp_path, monkeypatch):
    app_module = importlib.import_module("app")
    db = str(tmp_path / "zelle.db")
    monkeypatch.setattr(app_module, "LOG_DB", db)
    from dashboard import practitioner_portal as PP
    monkeypatch.setattr(PP, "_LOG_DB", pathlib.Path(db))
    monkeypatch.setattr(app_module, "INVOICE_PAYLINK_ENABLED", True)
    monkeypatch.setattr(app_module, "_STRIPE_ACTIVE", True)
    monkeypatch.setitem(app_module._ALT_PAY["zelle"], "to", TO)
    monkeypatch.setitem(app_module._ALT_PAY["zelle"], "pay_link", "")
    from dashboard import inbox
    sent = []
    monkeypatch.setattr(inbox, "send_email", lambda *a, **k: sent.append((a, k)))
    from dashboard import orders as O, order_payments as OP
    cx = sqlite3.connect(db)
    cx.row_factory = sqlite3.Row
    O.init_orders_table(cx)
    OP.ensure_table(cx)

    def order(**kw):
        oid = O.upsert_order(cx, source="dropship", external_ref=kw.pop("ref"),
                             email="t@example.com", total_cents=kw.pop("total"),
                             status=kw.pop("status", "new"))
        sets = ", ".join(f"{k}=?" for k in kw)
        if sets:
            cx.execute(f"UPDATE orders SET {sets} WHERE id=?", (*kw.values(), oid))
        cx.commit()
        return oid, PP.create_order_invoice_token(oid, db_path=db)

    return dict(app=app_module, c=app_module.app.test_client(), cx=cx, OP=OP,
                order=order, mp=monkeypatch, sent=sent, db=db)


def _part_paid(env):
    """$473 invoice, $433 already paid: $40 is owed."""
    oid, tok = env["order"](ref="DS-230", total=47300)
    env["OP"].add_payment(env["cx"], oid, 43300, "Zelle", source="manual")
    return oid, tok


# ── the server ──────────────────────────────────────────────────────────────

def test_page_data_carries_payee_balance_and_memo(env):
    _oid, tok = _part_paid(env)
    o = env["c"].get(f"/api/invoice/{tok}").get_json()["order"]
    z = o["zelle_pay"]
    assert z["name"] == "Susan Luscombe"
    assert z["to"] == TO
    assert z["amount_cents"] == 4000          # the balance, not the $473 total
    assert z["memo"] == "Invoice DS-230"


def test_choosing_zelle_returns_the_same_instructions(env):
    _oid, tok = _part_paid(env)
    j = env["c"].post(f"/api/invoice/{tok}/pay", json={"method": "zelle"}).get_json()
    z = j["pay_instructions"]
    assert (z["name"], z["to"], z["amount_cents"], z["memo"]) == (
        "Susan Luscombe", TO, 4000, "Invoice DS-230")
    for part in ("Susan Luscombe", TO, "$40.00", "Invoice DS-230"):
        assert part in j["instructions"]
    assert "$473" not in j["instructions"]


def test_a_paid_invoice_carries_no_zelle_details(env):
    oid, tok = env["order"](ref="DS-231", total=5000)
    env["OP"].add_payment(env["cx"], oid, 5000, "Zelle", source="manual")
    o = env["c"].get(f"/api/invoice/{tok}").get_json()["order"]
    assert o["zelle_pay"] is None


def test_only_an_https_pay_link_is_passed_on(env):
    _oid, tok = _part_paid(env)
    env["mp"].setitem(env["app"]._ALT_PAY["zelle"], "pay_link", "javascript:alert(1)")
    o = env["c"].get(f"/api/invoice/{tok}").get_json()["order"]
    assert o["zelle_pay"]["pay_link"] == ""
    env["mp"].setitem(env["app"]._ALT_PAY["zelle"], "pay_link", "https://zelle.example/qr")
    o = env["c"].get(f"/api/invoice/{tok}").get_json()["order"]
    assert o["zelle_pay"]["pay_link"] == "https://zelle.example/qr"


# ── the page, executed ──────────────────────────────────────────────────────

def _src():
    return PAGE.read_text()


def _fn(name):
    src = _src()
    start = src.index("function %s(" % name)
    depth, i = 0, src.index("{", start)
    while True:
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return src[start:i + 1]
        i += 1


def _const(name):
    m = re.search(r"^const %s = .*?;$" % name, _src(), re.M | re.S)
    return m.group(0)


def _run(js):
    r = subprocess.run([node, "-e", js], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def _text(html):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", html)).strip()


ZP = {"name": "Susan Luscombe", "to": TO, "amount_cents": 4000,
      "memo": "Invoice DS-230", "pay_link": "", "note": "n"}


@pytest.mark.skipif(node is None, reason="node not installed")
def test_screen_block_shows_payee_amount_and_memo():
    out = _run("%s\n%s\n%s\nconsole.log(JSON.stringify(zelleInstrHtml(%s)));" % (
        _const("money"), _fn("esc"), _fn("zelleInstrHtml"), json.dumps(ZP)))
    t = _text(out)
    assert "To: Susan Luscombe, payee@example.com" in t
    assert "Amount: $40.00" in t
    assert "Memo: Invoice DS-230" in t


@pytest.mark.skipif(node is None, reason="node not installed")
def test_screen_block_escapes_its_values():
    bad = dict(ZP, name="<img src=x onerror=alert(1)>", pay_link='https://x/"><b>')
    out = _run("%s\n%s\n%s\nconsole.log(JSON.stringify(zelleInstrHtml(%s)));" % (
        _const("money"), _fn("esc"), _fn("zelleInstrHtml"), json.dumps(bad)))
    assert "<img" not in out and '"><b>' not in out


@pytest.mark.skipif(node is None, reason="node not installed")
def test_printed_invoice_carries_the_zelle_line():
    deps = "\n".join([_const("money"), _fn("esc"), _fn("svcFirst"), _fn("totalSavings"),
                      _fn("svcAnchor"), _const("PRINT_HEADER"), _fn("zellePrintLine"),
                      _fn("buildInvoicePrintHtml")])
    o = {"lines": [], "total_cents": 47300, "balance_due_cents": 4000,
         "external_ref": "DS-230", "created_at": "2026-10-01", "zelle_pay": ZP}
    out = _run("%s\nconsole.log(JSON.stringify([buildInvoicePrintHtml(%s), "
               "buildInvoicePrintHtml(%s)]));" % (
                   deps, json.dumps(o), json.dumps(dict(o, zelle_pay=None))))
    assert ("Pay by Zelle to Susan Luscombe, payee@example.com, memo: Invoice DS-230"
            in _text(out[0]))
    assert "Pay by Zelle" not in out[1]


def test_loyalty_points_line_is_unchanged():
    assert ('<p class="muted fee-free-note" hidden style="margin:6px 0 10px">Pay by Zelle '
            'or Wise and 3.5% of your payment comes back to you as loyalty points, for use '
            'toward services. The points are added when your payment arrives.</p>') in _src()


@pytest.mark.skipif(node is None, reason="node not installed")
def test_selecting_zelle_fetches_the_instructions_at_once():
    stmt = re.search(r"document\.querySelectorAll\('input\[name=m\]'\)\.forEach\(.*?\}\);",
                     _src(), re.S).group(0)
    out = _run("""
      const els = {}; const $ = id => (els[id] = els[id] || {style:{}, hidden:false});
      const radios = ['card','zelle','wise'].map(v => ({value:v}));
      const document = {querySelectorAll: () => radios};
      let curMethod = 'card', calls = [], PAY_GEN = 0;
      function pay(){ calls.push(curMethod); }
      %s
      radios[1].onchange(); radios[0].onchange(); radios[2].onchange();
      console.log(JSON.stringify(calls));
    """ % stmt)
    assert out == ["zelle"]



# ── choosing Zelle records nothing and sends nothing (review round 2) ───────

def _snapshot(db):
    """Every row of every table in the test database."""
    cx = sqlite3.connect(db)
    try:
        names = [r[0] for r in cx.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        return {n: sorted(map(repr, cx.execute(f'SELECT * FROM "{n}"').fetchall()))
                for n in names}
    finally:
        cx.close()


def _assert_zelle_is_inert(env, tok):
    # The page load comes first: what the selection path starts from.
    env["c"].get(f"/api/invoice/{tok}")
    before = _snapshot(env["db"])
    assert before["orders"] and before["order_payments"]   # the snapshot sees the rows
    for _ in range(3):
        j = env["c"].post(f"/api/invoice/{tok}/pay", json={"method": "zelle"}).get_json()
        assert j["ok"] and j["pay_instructions"]
        env["c"].get(f"/api/invoice/{tok}")
    assert _snapshot(env["db"]) == before
    assert env["sent"] == []


def test_repeated_zelle_pay_calls_record_and_send_nothing(env):
    _oid, tok = _part_paid(env)
    _assert_zelle_is_inert(env, tok)


def test_zelle_on_an_unpaid_invoice_records_and_sends_nothing(env):
    """No claim row, no pay_status change: only "I've sent payment" does that."""
    _oid, tok = env["order"](ref="DS-232", total=12000)
    # A $1 payment so the ledger table has a row for the snapshot to watch.
    env["OP"].add_payment(env["cx"], _oid, 100, "Zelle", source="manual")
    _assert_zelle_is_inert(env, tok)


def test_the_inert_check_can_fail(env):
    """Guard on the guard: a claim does change the snapshot and does send mail."""
    _oid, tok = _part_paid(env)
    before = _snapshot(env["db"])
    env["c"].post(f"/api/invoice/{tok}/claim-paid", json={"method": "zelle"})
    assert env["sent"] != []
    oid2, tok2 = env["order"](ref="DS-233", total=9000)
    before2 = _snapshot(env["db"])
    env["c"].post(f"/api/invoice/{tok2}/claim-paid", json={"method": "zelle"})
    assert _snapshot(env["db"]) != before2


@pytest.mark.skipif(node is None, reason="node not installed")
def test_selecting_zelle_only_calls_pay_with_method_zelle():
    """The selection path's only network call is POST /pay {method: zelle}, which the
    tests above prove inert. Run the real onchange and the real pay() together."""
    stmt = re.search(r"document\.querySelectorAll\('input\[name=m\]'\)\.forEach\(.*?\}\);",
                     _src(), re.S).group(0)
    out = _run("""
      const els = {}; const $ = id => (els[id] = els[id] || {style:{}, hidden:false});
      const radios = ['card','zelle','wise'].map(v => ({value:v}));
      const document = {querySelectorAll: () => radios};
      const window = {}; const API = '/api/invoice/T';
      let curMethod = 'card', PAY_GEN = 0, posts = [];
      const fetch = (url, opt) => { posts.push([url, opt.method, opt.body]);
        return Promise.resolve({json: () => Promise.resolve({ok:true, method:'zelle',
          pay_instructions:{name:'n', to:'t', amount_cents:1, memo:'m'}})}); };
      %s
      %s
      %s
      %s
      %s
      radios[1].onchange(); radios[0].onchange(); radios[2].onchange();
      setTimeout(() => console.log(JSON.stringify(posts)), 20);
    """ % (_const("money"), _fn("esc"), _fn("zelleInstrHtml"), "async " + _fn("pay"), stmt))
    assert out == [["/api/invoice/T/pay", "POST", '{"method":"zelle"}']]


# ── overlapping Zelle replies (review round 2) ──────────────────────────────

def _overlap_js(script):
    stmt = re.search(r"document\.querySelectorAll\('input\[name=m\]'\)\.forEach\(.*?\}\);",
                     _src(), re.S).group(0)
    return """
      const els = {}; const $ = id => (els[id] = els[id] || {style:{}, hidden:false,
                                                              textContent:'', innerHTML:''});
      const radios = ['card','zelle','wise'].map(v => ({value:v}));
      const document = {querySelectorAll: () => radios};
      const window = {}; const API = '/api/invoice/T';
      let curMethod = 'card', PAY_GEN = 0;
      const pending = [];
      const fetch = () => new Promise(res => pending.push(res));
      const reply = cents => ({json: () => Promise.resolve({ok:true, method:'zelle',
        pay_instructions:{name:'Susan Luscombe', to:'p@x', amount_cents:cents,
                          memo:'Invoice DS-230'}})});
      const tick = () => new Promise(r => setTimeout(r, 5));
      %s
      %s
      %s
      %s
      %s
      (async () => { %s
        console.log(JSON.stringify(els['pay-instr'].innerHTML)); })();
    """ % (_const("money"), _fn("esc"), _fn("zelleInstrHtml"), "async " + _fn("pay"), stmt, script)


@pytest.mark.skipif(node is None, reason="node not installed")
def test_an_older_zelle_reply_arriving_last_is_dropped():
    """Zelle, card, Zelle again: the balance fell from $473 to $40 between the two
    requests. The newer reply lands first, the older one last. $40 must stay."""
    out = _run(_overlap_js("""
        radios[1].onchange(); radios[0].onchange(); radios[1].onchange();
        pending[1](reply(4000)); await tick();
        pending[0](reply(47300)); await tick();
    """))
    assert "$40.00" in out and "$473.00" not in out


@pytest.mark.skipif(node is None, reason="node not installed")
def test_pay_now_during_a_zelle_fetch_supersedes_it():
    """Zelle selected, then Pay now while the first fetch is in flight."""
    out = _run(_overlap_js("""
        curMethod = 'zelle'; radios[1].onchange(); pay();
        pending[1](reply(4000)); await tick();
        pending[0](reply(47300)); await tick();
    """))
    assert "$40.00" in out and "$473.00" not in out


@pytest.mark.skipif(node is None, reason="node not installed")
def test_a_zelle_reply_after_switching_to_card_paints_nothing():
    out = _run(_overlap_js("""
        radios[1].onchange(); radios[0].onchange();
        pending[0](reply(47300)); await tick();
    """))
    assert out == ""


# ── round 1/3: the memo for non-invoice refs ────────────────────────────────

@pytest.mark.parametrize("ref,memo", [
    ("DS-230", "Invoice DS-230"),
    ("INH-77", "Invoice INH-77"),
    ("FMP-1234", "Invoice FMP-1234"),
    ("0123456789abcdef0123456789abcdef", "Order 5"),   # a checkout uuid
    ("portal-9f2c", "Order 5"),
    ("", "Order 5"),
])
def test_memo_names_an_invoice_only_for_invoice_refs(ref, memo):
    app_module = importlib.import_module("app")
    assert app_module._invoice_zelle_memo({"id": 5}, ref) == memo


def test_a_checkout_ref_invoice_carries_the_order_memo(env):
    oid, tok = env["order"](ref="0123456789abcdef0123456789abcdef", total=5000)
    o = env["c"].get(f"/api/invoice/{tok}").get_json()["order"]
    assert o["zelle_pay"]["memo"] == f"Order {oid}"


# ── round 1/3: a changed order refreshes the Zelle block ────────────────────

def _harness(script):
    """Real setupPay, pay, applyPoints and zelleInstrHtml, with a fake DOM and a
    fetch whose /pay replies are released by hand."""
    return """
      const els = {};
      const $ = id => (els[id] = els[id] || {style:{}, hidden:false, textContent:'',
                                              innerHTML:'', value:'', disabled:false});
      const radios = ['card','zelle','wise'].map(v => ({value:v}));
      const document = {querySelectorAll: () => radios,
                        querySelector: () => ({hidden:false})};
      const window = {}; const API = '/api/invoice/T';
      let curMethod = 'card', PAY_GEN = 0;
      const order = cents => ({payable:true, paylink_enabled:true, pay_amount_cents:cents,
                               total_cents:47300});
      let ORDER = order(47300);
      function renderTotals(){}
      const payReplies = [];
      let pointsOrder = null;
      const fetch = (url) => url.endsWith('/pay')
        ? new Promise(res => payReplies.push(res))
        : Promise.resolve({json: () => Promise.resolve({ok:true, order: pointsOrder})});
      const zReply = cents => ({json: () => Promise.resolve({ok:true, method:'zelle',
        pay_instructions:{name:'Susan Luscombe', to:'p@x', amount_cents:cents,
                          memo:'Invoice DS-230'}})});
      const tick = () => new Promise(r => setTimeout(r, 5));
      const shown = () => els['pay-instr'].style.display === 'block'
                          ? els['pay-instr'].innerHTML : '';
      %s
      %s
      %s
      %s
      %s
      %s
      (async () => { const out = {}; %s console.log(JSON.stringify(out)); })();
    """ % (_const("money"), _fn("esc"), _fn("zelleInstrHtml"), _fn("setupPay"),
           "async " + _fn("pay"), "async " + _fn("applyPoints"), script)


@pytest.mark.skipif(node is None, reason="node not installed")
def test_points_applied_after_choosing_zelle_show_the_new_balance():
    out = _run(_harness("""
        setupPay(); radios[1].onchange(); curMethod = 'zelle';
        payReplies[0](zReply(47300)); await tick();
        out.first = shown();
        pointsOrder = order(40000); els['pts-in'] = {value:'73'};
        await applyPoints();
        out.during = shown(); out.claimDuring = els['claim-btn'].style.display;
        payReplies[payReplies.length-1](zReply(40000)); await tick();
        out.after = shown(); out.payBtn = els['pay-btn'].style.display;
    """))
    assert "$473.00" in out["first"]
    assert out["during"] == "" and out["claimDuring"] == "none"
    assert "$400.00" in out["after"] and "$473.00" not in out["after"]
    assert out["payBtn"] == "none"     # Pay never sits beside "I've sent payment"


@pytest.mark.skipif(node is None, reason="node not installed")
def test_an_old_balance_in_flight_when_points_apply_is_dropped():
    out = _run(_harness("""
        setupPay(); curMethod = 'zelle'; radios[1].onchange();
        pointsOrder = order(40000); els['pts-in'] = {value:'73'};
        await applyPoints();
        const n = payReplies.length;
        payReplies[n-1](zReply(40000)); await tick();
        for (let i = 0; i < n-1; i++) payReplies[i](zReply(47300));
        await tick();
        out.after = shown();
    """))
    assert "$400.00" in out["after"] and "$473.00" not in out["after"]


@pytest.mark.parametrize("fn,call", [
    ("applyPoints", "applyPoints()"),
    ("applyPlan", "applyPlan(false, null)"),
    ("pushUpdate", "pushUpdate()"),
    ("toggleInvoiceMembership", "toggleInvoiceMembership('add')"),
])
@pytest.mark.skipif(node is None, reason="node not installed")
def test_every_order_change_redoes_the_pay_card(fn, call):
    out = _run("""
      const $ = id => ({style:{}, value:'1', textContent:''});
      const API = '/api/invoice/T'; const event = undefined;
      let ORDER = {savings_offer:{cohort_key:'k'}, membership_offer:{tier:'month'}, lines:[]};
      let LINES = [], setups = 0;
      const fetch = () => Promise.resolve({json: () => Promise.resolve({ok:true,
                                            order:{lines:[]}})});
      function setupPay(){ setups++; }
      function renderTotals(){} function renderLines(){} function renderSavings(){}
      function renderPayments(){} function renderMembershipOffer(){}
      function renderFromOrder(){} function linesFromOrder(){ return []; }
      async %s
      (async () => { await %s; console.log(JSON.stringify(setups)); })();
    """ % (_fn(fn), call))
    assert out >= 1


# ── round 1/3: pay() survives a failed or non-JSON reply ────────────────────

@pytest.mark.parametrize("failure", [
    "() => Promise.reject(new TypeError('Failed to fetch'))",
    "() => Promise.resolve({json: () => Promise.reject(new SyntaxError('Unexpected token <'))})",
])
@pytest.mark.skipif(node is None, reason="node not installed")
def test_a_failed_pay_reply_shows_retry_and_keeps_pay(failure):
    out = _run("""
      const els = {};
      const $ = id => (els[id] = els[id] || {style:{display:'none'}, textContent:'', innerHTML:''});
      const window = {}; const API = '/api/invoice/T';
      let curMethod = 'zelle', PAY_GEN = 0;
      const fetch = %s;
      %s
      %s
      %s
      %s
      (async () => { await pay(); console.log(JSON.stringify({
          msg: els['pay-msg'].textContent, btn: els['pay-btn'].style.display})); })();
    """ % (failure, _const("money"), _fn("esc"), _fn("zelleInstrHtml"), "async " + _fn("pay")))
    assert out["msg"] == "We could not reach the payment service. Please try again."
    assert out["btn"] == ""


@pytest.mark.skipif(node is None, reason="node not installed")
def test_an_old_zelle_reply_cannot_paint_once_nothing_is_owed():
    """Points cover the rest while a Zelle fetch is loading: setupPay re-runs with
    nothing payable and fetches nothing. The old reply must still be dropped."""
    out = _run(_harness("""
        setupPay(); curMethod = 'zelle'; radios[1].onchange();
        pointsOrder = Object.assign(order(0), {payable:false}); els['pts-in'] = {value:'473'};
        await applyPoints();
        out.calls = payReplies.length;
        payReplies[0](zReply(47300)); await tick();
        out.after = shown(); out.claim = els['claim-btn'].style.display;
    """))
    assert out["calls"] == 1
    assert out["after"] == "" and out["claim"] == "none"
