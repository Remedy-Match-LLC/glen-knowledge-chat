"""Picking a customer must load their saved special prices into the panel.

`pickPerson` and `loadOrder` set #c-email from script, and a scripted .value fires
neither onchange nor onblur -- the only two events wired to loadClientPrices().  So
the client's FF flat rate silently priced their lines (refreshPreview IS called)
while the "Special price for ALL this client's FFs" box stayed empty, which reads as
"they have no special price."  Rebecca Navo hit exactly this at $60/FF.

These execute the real function out of the page rather than grepping the source, so
a comment mentioning loadClientPrices cannot satisfy them.
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
const calls = [];
const rec = (n) => (...a) => { calls.push(n); };
const el = () => new Proxy({value:"", max:"", textContent:"", style:{}, classList:{add:()=>{}}},
                           {get:(t,k)=> (k in t ? t[k] : ""), set:(t,k,v)=>{t[k]=v; return true;}});
const nodes = {};
const $ = (id) => (nodes[id] = nodes[id] || el());
let POINTS_BAL = 0, LINES = [];
const saveCustomerDraft = rec("saveCustomerDraft");
const refreshPreview    = rec("refreshPreview");
const loadClientPrices  = rec("loadClientPrices");
const loadPickupDefault = rec("loadPickupDefault");
const recalc            = rec("recalc");
const dollars = (c) => "$" + (c/100).toFixed(2);
__FN__
__CALL__
console.log(JSON.stringify({calls, email: nodes["c-email"] ? nodes["c-email"].value : null}));
"""


def _extract(name):
    """The named top-level function, brace-balanced from its `function name(` line."""
    start = PAGE.index("function %s(" % name)
    depth, i = 0, PAGE.index("{", start)
    while True:
        if PAGE[i] == "{":
            depth += 1
        elif PAGE[i] == "}":
            depth -= 1
            if depth == 0:
                return PAGE[start:i + 1]
        i += 1


def _run(fn_name, call_js):
    src = HARNESS.replace("__FN__", _extract(fn_name)).replace("__CALL__", call_js)
    out = subprocess.run([NODE, "-e", src], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


@pytest.mark.skipif(NODE is None, reason="node not available")
def test_picking_a_customer_loads_their_saved_prices():
    res = _run("pickPerson",
               'pickPerson({id: 7, name: "Rebecca Navo", email: "rebeccanavo@gmail.com"});')
    assert res["email"] == "rebeccanavo@gmail.com"
    assert "loadClientPrices" in res["calls"], (
        "the FF flat rate panel never loads for a picked customer: %s" % res["calls"])
    # It must load BEFORE the re-price, so the panel and the lines agree on screen.
    assert res["calls"].index("loadClientPrices") < res["calls"].index("refreshPreview")


def test_the_email_field_still_loads_prices_when_typed_by_hand():
    """The two events that already worked must stay wired."""
    field = re.search(r'<input id="c-email"[^>]*>', PAGE).group(0)
    assert "onchange=\"loadClientPrices()\"" in field
    assert "onblur=\"loadClientPrices()\"" in field
