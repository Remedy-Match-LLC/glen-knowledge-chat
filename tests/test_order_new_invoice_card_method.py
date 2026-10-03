"""The invoice card's payment method follows what was recorded (Glen, 2026-10-03).

The card's "Payment method:" line was a snapshot of the dropdown when the card was
drawn. Mary's $1,020 Zelle payment was recorded and the dropdown read Zelle, but the
card still said "Credit card". Record payment also read the method silently from the
page; it now opens a form with its own method dropdown.

These run the page's own functions under node against stubs.
"""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PAGE = (ROOT / "static" / "order-new.html").read_text()
NODE = shutil.which("node")

HARNESS = r"""
const nodes = {};
const mk = () => ({value:"", textContent:"", innerHTML:"", style:{}, focus(){}});
const $ = (id) => (nodes[id] = nodes[id] || mk());
let PAY_ROWS = {};
let RECORDED_METHOD = "";
let LAST_ORDER = {order_id: 7};
const lifecalls = [];
async function lifecycle(key, p){ lifecalls.push([key, p]); return true; }
function setMethod(m){ $("o-method").value = m; }
__FNS__
(async () => {
__CALL__
console.log(JSON.stringify({card: $("inv-method").textContent, life: lifecalls,
                            om: $("o-method").value}));
})();
"""


def _extract(name):
    start = PAGE.index("function %s(" % name)
    if PAGE[max(0, start - 6):start] == "async ":
        start -= 6
    depth, i = 0, PAGE.index("{", start)
    while True:
        if PAGE[i] == "{":
            depth += 1
        elif PAGE[i] == "}":
            depth -= 1
            if depth == 0:
                return PAGE[start:i + 1]
        i += 1


def _run(call_js):
    fns = "\n".join(_extract(n) for n in
                    ("refreshInvMethod", "recordPayment", "submitRecordPayment"))
    fns = fns.replace("let RECORDED_METHOD", "RECORDED_METHOD")
    src = HARNESS.replace("__FNS__", fns).replace("__CALL__", call_js)
    out = subprocess.run([NODE, "-e", src], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


pytestmark = pytest.mark.skipif(not NODE, reason="node not installed")


def test_a_recorded_zelle_payment_shows_on_the_card():
    got = _run('$("o-method").value = "Credit card (Stripe)";'
               'PAY_ROWS = {1: {kind: "payment", status: "ok", method: "Zelle"}};'
               'refreshInvMethod();')
    assert got["card"] == "Payment method: Zelle (recorded)"


def test_a_voided_payment_does_not_count():
    got = _run('$("o-method").value = "Check";'
               'PAY_ROWS = {1: {kind: "payment", status: "void", method: "Zelle"}};'
               'refreshInvMethod();')
    assert got["card"] == "Payment method: Check"


def test_record_payment_sends_the_method_picked_in_its_own_dropdown():
    got = _run('$("o-method").value = "Credit card (Stripe)";'
               'recordPayment();'
               'if ($("rp-method").value !== "Credit card (Stripe)") throw new Error("default");'
               '$("rp-method").value = "Zelle"; $("rp-amt").value = "1020";'
               'await submitRecordPayment();')
    assert got["life"] == [["orders.record_payment", {"method": "Zelle", "amount_cents": 102000}]]
    assert got["card"] == "Payment method: Zelle (recorded)"
    assert got["om"] == "Zelle"


def test_the_card_no_longer_bakes_the_method_into_the_snapshot():
    body = _extract("showInvoice")
    assert 'Payment method: ${$("o-method").value}`;' not in body
    assert 'id="inv-method"' in body and "refreshInvMethod();" in body
