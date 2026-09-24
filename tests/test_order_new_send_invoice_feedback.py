"""Send invoice on the order page must show its result beside the button, and stay.

Glen, 2026-09-24, on order 196: "clicking 'Send Invoice' shows no visible response, so I
can't tell if it sent or not." It had sent. The only feedback was a toast in the corner
that faded after 2.2 s, and every further click emails the customer again.

These tests RUN sendInvoice in node against a stubbed page, the same way
test_biofield_mine_buttons_status.py does. A string match on the page proves nothing
about what the handler shows.
"""
import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

node = shutil.which("node")
pytestmark = pytest.mark.skipif(node is None, reason="node not installed")

PAGE = (Path(__file__).resolve().parent.parent / "static" / "order-new.html").read_text()

OK = {"status": "done", "result": {"message": "Invoice #196 emailed to a@example.com."}}
REFUSED = {"status": "failed", "result": {
    "error": "invoice pay-link is disabled (INVOICE_PAYLINK_ENABLED off)"}}


def _func(name):
    """One top-level function, cut out by counting braces. The functions used here
    hold no brace inside a string, which is what makes the count safe."""
    m = re.search(rf"(?m)^(?:async )?function {name}\(", PAGE)
    assert m, f"{name} not found in order-new.html"
    depth, j = 0, PAGE.index("{", m.end())
    while True:
        if PAGE[j] == "{":
            depth += 1
        elif PAGE[j] == "}":
            depth -= 1
            if depth == 0:
                return PAGE[m.start():j + 1] + "\n"
        j += 1


def _run(order_id=196, *, reply=OK, sent_at=None, confirm=True, mode="json"):
    """Click Send once. mode: json (server replies `reply`), html (a 502 page that is
    not JSON), or offline (fetch rejects). Returns what the page ended up showing."""
    if mode == "json":
        fetch = f"async function fetch(){{ calls++; return {{status:200, json: async () => ({json.dumps(reply)})}}; }}"
    elif mode == "html":
        fetch = ("async function fetch(){ calls++; return {status:502, json: async () => {"
                 " throw new SyntaxError('Unexpected token <'); }}; }")
    else:
        fetch = "async function fetch(){ calls++; throw new TypeError('Failed to fetch'); }"
    body = """
let calls = 0, asked = null, toasts = [];
const HEADERS = {};
function toast(m, k){ toasts.push([m, k || 'ok']); }
function confirm(m){ asked = m; return %s; }
function makeEl(){
  const el = { textContent: '', className: '', disabled: false, nextElementSibling: null,
    insertAdjacentElement(pos, x){ this.nextElementSibling = x; return x; } };
  el.classList = { contains: c => el.className.split(' ').includes(c),
                   add: c => { if (!el.classList.contains(c)) el.className = (el.className + ' ' + c).trim(); } };
  return el;
}
const btn = makeEl(); btn.textContent = 'Send invoice'; btn.className = 'ghost inv-send-btn';
const document = { createElement: makeEl, querySelectorAll: () => [btn] };
%s
%s
""" % ("true" if confirm else "false", fetch, "let INV_SENT_AT = %s;" % json.dumps(sent_at))
    body += "".join(_func(n) for n in ("invSentStr", "fmtSentTime", "invSendStatus", "sendInvoice"))
    body += """
sendInvoice(btn, %s).then(ret => { const s = btn.nextElementSibling;
  console.log(JSON.stringify({ ret, calls, asked, toasts, label: btn.textContent,
    disabled: btn.disabled, status: s ? s.textContent : null,
    statusClass: s ? s.className : null, sentAt: INV_SENT_AT })); })
 .catch(e => console.log(JSON.stringify({ uncaught: String(e) })));
""" % json.dumps(order_id)
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as f:
        f.write(body)
        path = f.name
    r = subprocess.run([node, path], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr[:800]
    out = json.loads(r.stdout.strip().splitlines()[-1])
    assert "uncaught" not in out, out
    return out


def test_a_send_leaves_a_line_saying_who_and_when():
    out = _run()
    assert out["calls"] == 1
    assert out["status"].startswith("Invoice #196 emailed to a@example.com at ")
    assert out["statusClass"] == "inv-send-status"
    assert out["label"].startswith("Invoice Sent ") and out["sentAt"]
    assert out["disabled"] is False and out["ret"] is True


def test_a_refused_send_says_not_sent_and_why():
    out = _run(reply=REFUSED)
    assert out["status"] == "Not sent: invoice pay-link is disabled (INVOICE_PAYLINK_ENABLED off)"
    assert "error" in out["statusClass"].split()
    assert out["label"] == "Send invoice" and out["sentAt"] is None


@pytest.mark.parametrize("mode", ["html", "offline"])
def test_a_lost_reply_says_unknown_never_failed(mode):
    # The request may have reached the server and sent the email.
    out = _run(mode=mode)
    assert out["status"].startswith("Not known whether it sent")
    assert "Not sent" not in out["status"]
    assert "unknown" in out["statusClass"].split()
    assert out["label"] == "Send invoice" and out["disabled"] is False


def test_a_second_send_asks_first_and_no_means_no_email():
    out = _run(sent_at="2026-09-24T18:45:29Z", confirm=False)
    assert out["calls"] == 0 and out["asked"] and "already sent" in out["asked"]
    assert out["ret"] is False


def test_a_second_send_goes_when_confirmed():
    out = _run(sent_at="2026-09-24T18:45:29Z", confirm=True)
    assert out["asked"] and out["calls"] == 1 and out["ret"] is True


def test_a_first_send_does_not_ask():
    out = _run()
    assert out["asked"] is None


def test_no_saved_order_sends_nothing():
    out = _run(order_id=None)
    assert out["calls"] == 0 and "error" in out["statusClass"].split()


def test_both_send_buttons_use_it_and_edit_mode_knows_the_last_send():
    assert 'onclick="sendInvoice(this, EDIT_OID)"' in PAGE
    assert 'onclick="sendInvoice(this, LAST_ORDER&&LAST_ORDER.order_id)"' in PAGE
    assert "lifecycle('orders.send_invoice'" not in PAGE
    assert "INV_SENT_AT = o.invoice_sent_at || null;" in _func("loadOrderForEdit")
