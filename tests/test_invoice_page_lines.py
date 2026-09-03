"""The client invoice page's own line logic, EXECUTED rather than pattern-matched.

Asserting on the source text of a page proves only that a string is present; a
guard once passed because the assertion matched the COMMENT above the deleted
code. So the functions are extracted from static/invoice.html and run under node.
"""
import json
import pathlib
import re
import shutil
import subprocess

import pytest

PAGE = pathlib.Path(__file__).resolve().parent.parent / "static" / "invoice.html"
node = shutil.which("node")
pytestmark = pytest.mark.skipif(node is None, reason="node not installed")


def _fn(name):
    """Pull one top-level `function name(...)` out of the page by brace balance."""
    src = PAGE.read_text()
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


def _run(js):
    r = subprocess.run([node, "-e", js], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def test_a_zeroed_line_is_still_sent_to_the_server():
    """The defect: the page filtered qty>0, so zeroing a line deleted it.

    Glen's zero-qty lines exist so a client can see what to reorder next month.
    """
    payload = re.search(r"const lines = LINES\.map\([^;]+;", PAGE.read_text()).group(0)
    out = _run("""
      const LINES = [{slug:'a', qty:0, format:'bottle'}, {slug:'b', qty:2}];
      %s
      console.log(JSON.stringify(lines));
    """ % payload)
    assert out == [{"slug": "a", "qty": 0, "format": "bottle"},
                   {"slug": "b", "qty": 2, "format": "bottle"}]


def test_removing_a_line_drops_it_and_asks_first():
    js = _fn("removeLine")
    out = _run("""
      let asked = null, pushed = 0;
      const confirm = (m) => { asked = m; return true; };
      const pushUpdate = () => { pushed++; };
      let LINES = [{slug:'a', name:'Alpha', qty:1}, {slug:'b', name:'Beta', qty:2}];
      %s
      removeLine(0);
      console.log(JSON.stringify({left: LINES.map(l=>l.slug), asked, pushed}));
    """ % js)
    assert out["left"] == ["b"]
    assert "Alpha" in out["asked"]
    assert out["pushed"] == 1


def test_declining_the_confirm_changes_nothing():
    js = _fn("removeLine")
    out = _run("""
      const confirm = () => false;
      let pushed = 0; const pushUpdate = () => { pushed++; };
      let LINES = [{slug:'a', name:'Alpha', qty:1}];
      %s
      removeLine(0);
      console.log(JSON.stringify({left: LINES.map(l=>l.slug), pushed}));
    """ % js)
    assert out["left"] == ["a"] and out["pushed"] == 0


def test_removing_a_line_that_is_not_there_is_harmless():
    js = _fn("removeLine")
    out = _run("""
      let pushed = 0; const pushUpdate = () => { pushed++; };
      const confirm = () => true;
      let LINES = [{slug:'a', name:'Alpha', qty:1}];
      %s
      removeLine(9);
      console.log(JSON.stringify({left: LINES.map(l=>l.slug), pushed}));
    """ % js)
    assert out["left"] == ["a"] and out["pushed"] == 0


def test_the_line_builder_carries_kind_so_membership_keeps_its_identity():
    """Every rebuild site must keep `kind`.

    Two of the four dropped it. A membership line without `kind` renders with the
    quantity stepper and, now, a remove button -- the one line the offer card owns.
    """
    js = _fn("linesFromOrder")
    out = _run("""
      const ORDER = {lines:[{slug:'membership:pro', name:'M', qty:1, unit_cents:100,
                             kind:'membership', tier:'pro'},
                            {slug:'x', name:'X', qty:1, unit_cents:1, service:true}]};
      %s
      console.log(JSON.stringify(linesFromOrder()));
    """ % js)
    assert out[0]["kind"] == "membership" and out[0]["tier"] == "pro"
    assert out[1]["service"] is True


def test_every_rebuild_site_uses_the_one_builder():
    # Drift between four hand-rolled copies is what dropped `kind` in the first place.
    src = PAGE.read_text()
    assert src.count("ORDER.lines.map") == 1, "a second hand-rolled line builder came back"
    assert "function linesFromOrder()" in src


def test_delete_and_stepper_are_offered_on_exactly_the_same_lines():
    # A control that removes a line must not appear where the stepper is withheld.
    src = PAGE.read_text()
    guard = "(!l.service && l.kind!=='membership' && ORDER.editable)"
    assert src.count(guard) == 2, "packaging, delete: both must share the products-only guard"


# --- the pay button: shown when money is owed, not when the order is editable ---

def _setup_pay(order):
    """Run setupPay() against a stubbed DOM and report what the customer sees."""
    js = _fn("setupPay")
    return _run("""
      const els = {};
      const el = id => els[id] || (els[id] = {style:{}, textContent:'', innerHTML:'', disabled:false});
      const $ = el;
      const money = c => '$' + (c/100).toFixed(2);
      const document = { querySelectorAll: () => [] };
      const ORDER = %s;
      %s
      setupPay();
      console.log(JSON.stringify({
        cardHidden: els['pay-card'] ? els['pay-card'].style.display === 'none' : false,
        btn: els['pay-btn'] ? els['pay-btn'].textContent : null,
        disabled: els['pay-btn'] ? els['pay-btn'].disabled : null,
      }));
    """ % (json.dumps(order), js))


def test_a_shipped_but_unpaid_order_still_shows_its_pay_button():
    """Ashley King's #115/#116: shipped, unpaid a month, and the pay button was
    hidden because `editable` is false for anything past `confirmed`."""
    out = _setup_pay({"pay_status": "unpaid", "editable": False, "payable": True,
                      "total_cents": 30300, "paylink_enabled": True})
    assert out["cardHidden"] is False
    assert "303.00" in out["btn"]


def test_a_paid_order_hides_the_pay_button():
    out = _setup_pay({"pay_status": "paid", "editable": False, "payable": False,
                      "total_cents": 30300, "paylink_enabled": True})
    assert out["cardHidden"] is True


def test_a_cancelled_order_hides_the_pay_button():
    out = _setup_pay({"pay_status": "unpaid", "editable": False, "payable": False,
                      "total_cents": 9300, "paylink_enabled": True})
    assert out["cardHidden"] is True


def test_the_pay_button_does_not_consult_editable_any_more():
    # An editable invoice that is already settled must still not offer payment;
    # this fails if the gate slips back to `editable`.
    out = _setup_pay({"pay_status": "paid", "editable": True, "payable": False,
                      "total_cents": 9300, "paylink_enabled": True})
    assert out["cardHidden"] is True


def test_payment_not_enabled_yet_leaves_the_card_up_but_the_button_off():
    out = _setup_pay({"pay_status": "unpaid", "editable": False, "payable": True,
                      "total_cents": 9300, "paylink_enabled": False})
    assert out["cardHidden"] is False and out["disabled"] is True
