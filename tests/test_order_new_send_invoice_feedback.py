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
# The dispatcher's real failure shape (dashboard/dispatch.py): the error is top level.
REFUSED = {"status": "failed",
           "error": "invoice pay-link is disabled (INVOICE_PAYLINK_ENABLED off)"}
# A failure from a step after send_email (mark_invoice_sent, the event log): the email went.
AFTER_SEND = {"status": "failed", "error": "database is locked"}
UNAUTHORIZED = {"ok": False, "error": "unauthorized"}
DENIED = {"status": "denied", "reason": "permission"}
HTML, OFFLINE = "html", "offline"   # a 502 page that is not JSON; a fetch that rejects


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


def _run(order_id=196, *, replies=(OK,), sent_at=None, confirm=True):
    """Click the first of the page's two send buttons once per entry in `replies`.
    Each entry is a JSON reply, HTML or OFFLINE. Returns what the page shows after."""
    body = """
let calls = 0, asked = [], toasts = [], inflight = [];
const REPLIES = %s;
const HEADERS = {};
function toast(m, k){ toasts.push([m, k || 'ok']); }
function confirm(m){ asked.push(m); return %s; }
async function fetch(){
  inflight.push([btn.disabled, btn2.disabled]);
  const r = REPLIES[calls++];
  if (r === "offline") throw new TypeError('Failed to fetch');
  if (r === "html") return {status: 502, json: async () => { throw new SyntaxError('Unexpected token <'); }};
  return {status: 200, json: async () => r};
}
function makeEl(){
  const el = { textContent: '', className: '', disabled: false, nextElementSibling: null,
    insertAdjacentElement(pos, x){ this.nextElementSibling = x; return x; } };
  el.classList = { contains: c => el.className.split(' ').includes(c),
                   add: c => { if (!el.classList.contains(c)) el.className = (el.className + ' ' + c).trim(); } };
  return el;
}
const btn = makeEl(); btn.textContent = 'Send invoice'; btn.className = 'ghost inv-send-btn';
const btn2 = makeEl(); btn2.textContent = 'Send invoice to customer'; btn2.className = 'ghost inv-send-btn';
const document = { createElement: makeEl, querySelectorAll: () => [btn, btn2] };
let INV_SENT_AT = %s;
""" % (json.dumps(list(replies)), "true" if confirm else "false", json.dumps(sent_at))
    body += re.search(r"(?m)^let INV_UNSURE = .*$", PAGE).group(0) + "\n"
    body += re.search(r"(?m)^const INV_PRE_SEND = .*$", PAGE).group(0) + "\n"
    body += "".join(_func(n) for n in ("invSentStr", "fmtSentTime", "invSendStatus",
                                       "invRefusedBeforeEmail", "sendInvoice"))
    body += """
(async () => { let ret;
  for (let i = 0; i < REPLIES.length; i++) ret = await sendInvoice(btn, %s);
  const s = btn.nextElementSibling;
  console.log(JSON.stringify({ ret, calls, asked, toasts, inflight, label: btn.textContent,
    label2: btn2.textContent, disabled: btn.disabled || btn2.disabled,
    status: s ? s.textContent : null, statusClass: s ? s.className : null,
    sentAt: INV_SENT_AT, unsure: INV_UNSURE }));
})().catch(e => console.log(JSON.stringify({ uncaught: String(e) })));
""" % json.dumps(order_id)
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "send_invoice.js"
        path.write_text(body)
        r = subprocess.run([node, str(path)], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr[:800]
    out = json.loads(r.stdout.strip().splitlines()[-1])
    assert "uncaught" not in out, out
    return out


def test_a_send_leaves_a_line_saying_who_and_when():
    out = _run()
    assert out["calls"] == 1
    assert out["status"].startswith("Invoice #196 emailed to a@example.com at ")
    assert out["statusClass"] == "inv-send-status"
    assert out["ret"] is True and out["sentAt"] and out["disabled"] is False


def test_both_buttons_lock_during_a_send_and_both_show_sent():
    out = _run()
    assert out["inflight"] == [[True, True]]
    assert out["label"].startswith("Invoice Sent ") and out["label2"].startswith("Invoice Sent ")


@pytest.mark.parametrize("reply, reason", [
    (REFUSED, "invoice pay-link is disabled (INVOICE_PAYLINK_ENABLED off)"),
    ({"status": "failed", "error": "order #196 has no customer email"}, "order #196 has no customer email"),
    ({"status": "failed", "error": "order #196 is 'done' \u2014 an invoice can only be sent"}, "order #196 is 'done'"),
    (UNAUTHORIZED, "access key was refused"),
    (DENIED, "not allowed to send invoices"),
])
def test_a_refusal_before_any_email_says_not_sent(reply, reason):
    out = _run(replies=(reply,))
    assert out["status"].startswith("Not sent: ") and reason in out["status"]
    assert "error" in out["statusClass"].split()
    assert out["unsure"] is False and out["sentAt"] is None
    assert out["label"] == "Send invoice" and out["disabled"] is False


def test_a_failure_after_the_email_never_says_not_sent():
    out = _run(replies=(AFTER_SEND,))
    assert "Not sent" not in out["status"]
    assert "database is locked" in out["status"] and "may still have gone" in out["status"]
    assert "Check order #196" in out["status"]
    assert "unknown" in out["statusClass"].split() and out["unsure"] is True


@pytest.mark.parametrize("lost", [HTML, OFFLINE])
def test_a_lost_reply_says_unknown_never_failed(lost):
    # The request may have reached the server and sent the email.
    out = _run(replies=(lost,))
    assert out["status"].startswith("Not known whether it sent")
    assert "Not sent" not in out["status"] and "Check order #196" in out["status"]
    assert "unknown" in out["statusClass"].split()
    assert out["label"] == "Send invoice" and out["disabled"] is False


@pytest.mark.parametrize("first", [HTML, OFFLINE, AFTER_SEND])
def test_a_retry_after_an_unclear_attempt_asks_first(first):
    out = _run(replies=(first, OK), confirm=False)
    assert out["calls"] == 1 and len(out["asked"]) == 1
    assert "may have gone out" in out["asked"][0]


def test_a_retry_after_a_clean_refusal_does_not_ask():
    out = _run(replies=(REFUSED, OK), confirm=False)
    assert out["asked"] == [] and out["calls"] == 2 and out["ret"] is True


def test_a_second_send_asks_first_and_no_means_no_email():
    out = _run(sent_at="2026-09-24T18:45:29Z", confirm=False)
    assert out["calls"] == 0 and "already sent" in out["asked"][0]
    assert out["ret"] is False


def test_a_second_send_goes_when_confirmed():
    out = _run(sent_at="2026-09-24T18:45:29Z", confirm=True)
    assert len(out["asked"]) == 1 and out["calls"] == 1 and out["ret"] is True


def test_a_first_send_does_not_ask():
    assert _run()["asked"] == []


def test_no_saved_order_sends_nothing():
    out = _run(order_id=None)
    assert out["calls"] == 0 and "error" in out["statusClass"].split()


def test_both_send_buttons_use_it_and_edit_mode_knows_the_last_send():
    assert 'onclick="sendInvoice(this, EDIT_OID)"' in PAGE
    assert 'onclick="sendInvoice(this, LAST_ORDER&&LAST_ORDER.order_id)"' in PAGE
    assert "lifecycle('orders.send_invoice'" not in PAGE
    assert "INV_SENT_AT = o.invoice_sent_at || null;" in _func("loadOrderForEdit")


def test_the_pre_send_list_matches_the_servers_checks_and_nothing_after():
    """Every error _send_invoice_exec raises before send_email must be recognised as
    'not sent', and the send failure itself must not be. Reads the server source, so a
    reworded server check fails here instead of turning into a false 'may have gone'."""
    src = (Path(__file__).resolve().parent.parent / "dashboard" / "orders.py").read_text()
    body = src[src.index("def _send_invoice_exec("):]
    body = body[:body.index("\ndef ", 1)]
    before, after = body.split("_inbox.send_email(", 1)
    pattern = re.compile(re.search(r"(?m)^const INV_PRE_SEND = /(.*)/;$", PAGE).group(1))
    raised = re.findall(r'raise ValueError\(f?"([^"]*)"', before)
    assert len(raised) == 5, raised
    for msg in raised:
        example = re.sub(r"\{oid\}", "196", msg)
        example = re.sub(r"\{[^}]*\}", "done", example)
        assert pattern.match(example), f"server check not recognised as pre-send: {msg}"
    for msg in re.findall(r'raise ValueError\(f?"([^"]*)"', after):
        assert not pattern.match(re.sub(r"\{[^}]*\}", "x", msg)), msg
