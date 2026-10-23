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
    monkeypatch.setattr(inbox, "send_email", lambda *a, **k: None)
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
                order=order, mp=monkeypatch)


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
      let curMethod = 'card', calls = [];
      function pay(){ calls.push(curMethod); }
      %s
      radios[1].onchange(); radios[0].onchange(); radios[2].onchange();
      console.log(JSON.stringify(calls));
    """ % stmt)
    assert out == ["zelle"]
