"""The panel shown after "Create proposed invoice" has a Print invoice button.

Glen, 2026-10-02: "I don't see a print invoice button on Sell: New Order." Printing
existed only in Edit Invoice. Both now share printInvoiceFor(oid), which asks
/api/console/order/<id>/invoice-link for the ?print=1 page and opens it.

The handler is RUN in node against a stubbed page, as in
test_order_new_send_invoice_feedback.py. A string match alone proves nothing.
"""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

node = shutil.which("node")
PAGE = (Path(__file__).resolve().parent.parent / "static" / "order-new.html").read_text()


def _func(name):
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


def _panel_onclick():
    m = re.search(r'<button class="ghost" onclick="([^"]*)">Print invoice</button>', PAGE)
    assert m, "no Print invoice button in the created-invoice panel"
    return m.group(1)


def test_the_created_panel_offers_print_invoice():
    panel = _func("showInvoice")
    assert ">Print invoice</button>" in panel


@pytest.mark.skipif(node is None, reason="node not installed")
@pytest.mark.parametrize("reply,expect", [
    ({"ok": True, "link": "https://x.test/invoice/tok?print=1"}, "https://x.test/invoice/tok?print=1"),
    ({"ok": False, "error": "no such order"}, None),
])
def test_clicking_print_opens_that_orders_print_page(reply, expect):
    js = """
let fetched = [], went = [], toasts = [];
const HEADERS = {}; let EDIT_OID = null;
let LAST_ORDER = {order_id: 207};
function toast(m, k){ toasts.push([m, k]); }
const window = {location: {assign: u => went.push(u)}};
async function fetch(url){ fetched.push(url);
  return {ok: %s, json: async () => (%s)}; }
%s
(async () => { await (async () => { %s })(); await new Promise(r => setTimeout(r, 0));
  console.log(JSON.stringify({fetched, went, toasts})); })();
""" % ("true" if reply.get("ok") else "false", json.dumps(reply),
       _func("printInvoiceFor"), "return " + _panel_onclick())
    out = json.loads(subprocess.run([node, "-e", js], capture_output=True, text=True,
                                    check=True).stdout)
    assert out["fetched"] == ["/api/console/order/207/invoice-link"]
    if expect:
        assert out["went"] == [expect] and not out["toasts"]
    else:
        assert out["went"] == [] and out["toasts"][0][1] == "error"


def test_edit_invoice_print_uses_the_same_path():
    assert "printInvoiceFor(EDIT_OID)" in _func("printInvoiceEdit")
