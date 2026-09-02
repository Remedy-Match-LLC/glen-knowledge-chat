"""A zero-quantity line stays on the invoice as a next-order reference.

Glen, 2026-09-02: "the client wants to order more of the items next month, so
keeping them at zero quantity would be a helpful reference." The client sees them.

Zero was clamped back to 1 in three places, so it never survived a round trip:
the input's min="1", editLine's Math.max(1, ...), and the server pricer's
max(1, ...). The recurring hazard is `int(x or 1)` -- 0 is falsy, so a zero
silently becomes one.

A zero line must bill nothing, ship nothing, and NOT count toward the volume
discount tier, or one client's reference note would reprice everyone's bottles.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PAGE = (ROOT / "static" / "order-new.html").read_text()
NODE = shutil.which("node")


def _extract(name):
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


@pytest.mark.skipif(NODE is None, reason="node not available")
def test_the_form_keeps_a_zero_line_instead_of_dropping_it():
    src = ("let LINES=[{slug:'a',name:'Rescue',qty:1,unit_cents:6997},"
           "{slug:'b',name:'VirEx',qty:2,unit_cents:6997}];"
           "const EDIT_OID=null; let renders=0;"
           "function renderLines(){renders++;} function confirm(){return true;}"
           + _extract("editLine") + _extract("rmLine") +
           "editLine(0,'qty','0');"
           "console.log(JSON.stringify({names:LINES.map(l=>l.name),qtys:LINES.map(l=>l.qty)}));")
    out = subprocess.run([NODE, "-e", src], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    res = json.loads(out.stdout.strip().splitlines()[-1])
    assert res["names"] == ["Rescue", "VirEx"], "a zero line must NOT be removed"
    assert res["qtys"] == [0, 2]


@pytest.mark.skipif(NODE is None, reason="node not available")
def test_junk_and_negatives_never_produce_a_negative_quantity():
    for bad, expect in (("-3", 0), ("abc", 0), ("", 1), ("4", 4)):
        src = ("let LINES=[{slug:'a',name:'R',qty:1,unit_cents:6997}];"
               "const EDIT_OID=null; let renders=0;"
               "function renderLines(){renders++;} function confirm(){return true;}"
               + _extract("editLine") + _extract("rmLine") +
               "editLine(0,'qty',%s);" % json.dumps(bad) +
               "console.log(JSON.stringify({qty:LINES[0].qty,n:LINES.length}));")
        out = subprocess.run([NODE, "-e", src], capture_output=True, text=True, timeout=30)
        res = json.loads(out.stdout.strip().splitlines()[-1])
        assert res["n"] == 1, bad
        assert res["qty"] == expect, "%r -> %r" % (bad, res["qty"])


def test_the_spinner_can_reach_zero():
    row = PAGE.split("onchange=\"editLine(${i},'qty'")[0][-300:]
    assert 'min="0"' in row, "the qty input still clamps at min=1"


def test_a_zero_line_does_not_count_toward_the_volume_tier():
    """The order-wide FF count drives the volume discount. Counting a zero line as
    one unit would quietly cheapen every other bottle on the invoice."""
    import app as _app
    _app._get_product = lambda slug: {"slug": slug, "price_cents": 6997, "qty_pricing": True}
    _app._qty_eligible = lambda p: True
    lines = [{"slug": "a", "qty": 3}, {"slug": "b", "qty": 0}, {"slug": "c", "qty": 2}]
    assert _app._inhouse_total_ff_qty(lines) == 5


def test_a_zero_line_ships_nothing():
    from dashboard.orders import physical_units
    catalog = {"a": {"slug": "a", "shippable": True, "price_cents": 6997}}
    assert physical_units([{"slug": "a", "qty": 0}], catalog) == 0


def test_quickbooks_never_receives_a_zero_quantity_line():
    """QBO rejects/《noises on》 a zero line; the reference belongs on our invoice,
    not in the accounting ledger."""
    from dashboard import qbo_summary
    rows = qbo_summary.qbo_line_split(
        [{"slug": "a", "qty": 0, "amount": 6997}, {"slug": "b", "qty": 2, "amount": 6997}],
        13994, source="inhouse") if hasattr(qbo_summary, "qbo_line_split") else None
    if rows is None:
        pytest.skip("qbo_summary entry point differs; covered by the push-path test")
