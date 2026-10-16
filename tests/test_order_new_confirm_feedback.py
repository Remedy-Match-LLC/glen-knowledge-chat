"""The invoice card's Confirm button says what happened (Glen, 2026-10-16).

"On new order/edit invoice: the 'Confirm' button shows no change when clicked." In prod,
order 230 was a paid card order opened in Edit Invoice. Confirm was clicked twice and the
server refused both times ("order #230 is 'new', not a proposed invoice"). The page said so
only in a two-second toast in the corner, and a success changed nothing on the card either.

Now the card shows the order's status, offers Confirm only on a proposed invoice, and keeps
each action's outcome on the card. These run the page's own functions under node.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PAGE = (ROOT / "static" / "order-new.html").read_text()
NODE = shutil.which("node")

HARNESS = r"""
const nodes = {};
const mk = () => ({value:"", textContent:"", innerHTML:"", className:"", style:{display:""}});
const $ = (id) => (nodes[id] = nodes[id] || mk());
const HEADERS = {};
let LAST_ORDER = {order_id: 230};
let INV_STATUS = null, INV_PAY = null;
const toasts = [], posts = [];
function toast(m, k){ toasts.push([m, k || "ok"]); }
let REPLY = null;
async function fetch(url, opts){
  posts.push([url, JSON.parse(opts.body)]);
  if (REPLY === "throw") throw new Error("network down");
  return {json: async () => REPLY};
}
__FNS__
(async () => {
__CALL__
console.log(JSON.stringify({status: $("inv-status").textContent, msg: $("inv-msg").textContent,
  msgClass: $("inv-msg").className, btn: $("inv-confirm-btn").style.display,
  toasts, posts, st: INV_STATUS, pay: INV_PAY}));
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


def _label_table():
    start = PAGE.index("const INV_STATUS_LABEL")
    end = PAGE.index("const INV_STATUS_ORDER", start)
    return PAGE[start:PAGE.index(";", end) + 1]


def _run(call_js):
    fns = "\n".join([_label_table()] + [_extract(n) for n in
                                        ("refreshInvStatus", "invMsg", "lifecycle")])
    src = HARNESS.replace("__FNS__", fns).replace("__CALL__", call_js)
    out = subprocess.run([NODE, "-e", src], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


pytestmark = pytest.mark.skipif(not NODE, reason="node not installed")


def test_a_paid_order_hides_confirm_and_says_why():
    got = _run('INV_STATUS = "new"; INV_PAY = "paid"; refreshInvStatus();')
    assert got["btn"] == "none"
    assert got["status"] == "Order status: In fulfillment, paid."


def test_a_proposed_invoice_keeps_confirm():
    got = _run('INV_STATUS = "proposed"; INV_PAY = "unpaid"; refreshInvStatus();')
    assert got["btn"] == ""
    assert got["status"] == "Order status: Proposed invoice, unpaid."


def test_an_unknown_status_keeps_confirm_and_prints_no_status():
    got = _run('refreshInvStatus();')
    assert got["btn"] == "" and got["status"] == ""


def test_a_refused_confirm_stays_on_the_card_in_red():
    err = "order #230 is 'new', not a proposed invoice"
    got = _run('REPLY = {status: "failed", error: %s}; await lifecycle("orders.confirm", {});'
               % json.dumps(err))
    assert got["msg"] == "Action failed: " + err
    assert got["msgClass"] == "inv-msg error"
    assert got["toasts"] == [["Action failed: " + err, "error"]]
    assert got["posts"] == [["/api/action/orders.confirm", {"order_id": 230}]]


def test_a_successful_confirm_updates_the_card_and_hides_the_button():
    got = _run('INV_STATUS = "proposed"; INV_PAY = "unpaid";'
               'REPLY = {status: "done", result: {order_id: 230, status: "confirmed",'
               ' message: "Proposed invoice #230 confirmed"}};'
               'const ok = await lifecycle("orders.confirm", {}); if (ok !== true) throw new Error("ok");')
    assert got["st"] == "confirmed" and got["btn"] == "none"
    assert got["status"] == "Order status: Confirmed, awaiting payment, unpaid."
    assert got["msg"] == "Proposed invoice #230 confirmed" and got["msgClass"] == "inv-msg"


def test_recording_payment_moves_the_status_to_paid():
    got = _run('INV_STATUS = "proposed"; INV_PAY = "unpaid";'
               'REPLY = {status: "done", result: {status: "new", pay_status: "paid", message: "Paid"}};'
               'await lifecycle("orders.record_payment", {method: "Zelle"});')
    assert (got["st"], got["pay"], got["btn"]) == ("new", "paid", "none")


def test_no_reply_is_reported_as_unknown_not_as_failed():
    got = _run('REPLY = "throw"; const ok = await lifecycle("orders.confirm", {});'
               'if (ok !== false) throw new Error("should be false");')
    assert got["msg"].startswith("No reply from the server, so this may or may not have gone")
    assert "failed" not in got["msg"] and got["msgClass"] == "inv-msg error"


def test_a_late_reply_for_another_order_leaves_the_card_alone():
    got = _run('INV_STATUS = "proposed"; INV_PAY = "unpaid";'
               'REPLY = {status: "done", result: {status: "confirmed", message: "A confirmed"}};'
               'const p = lifecycle("orders.confirm", {}); LAST_ORDER = {order_id: 231}; await p;')
    assert got["posts"] == [["/api/action/orders.confirm", {"order_id": 230}]]
    assert (got["st"], got["msg"], got["btn"]) == ("proposed", "", "")
    assert got["toasts"] == [["A confirmed", "ok"]]


def test_an_out_of_order_reply_never_moves_the_status_back():
    got = _run('INV_STATUS = "new"; INV_PAY = "paid";'
               'REPLY = {status: "done", result: {status: "confirmed", message: "late"}};'
               'await lifecycle("orders.confirm", {});')
    assert (got["st"], got["pay"]) == ("new", "paid")
    assert got["status"] == "Order status: In fulfillment, paid."


def test_a_denied_action_names_the_reason():
    got = _run('REPLY = {status: "denied", reason: "permission"}; await lifecycle("orders.confirm", {});')
    assert got["msg"] == "Action failed: permission"


def test_the_card_carries_the_status_line_and_the_button_id():
    body = _extract("showInvoice")
    assert 'id="inv-confirm-btn"' in body
    assert 'id="inv-status"' in body and 'id="inv-msg"' in body
    assert "refreshInvStatus();" in body


def test_edit_mode_reads_the_orders_status_and_a_new_order_starts_proposed():
    load = _extract("loadOrderForEdit")
    assert "INV_STATUS = o.status || null; INV_PAY = o.pay_status || null;" in load
    create = _extract("createInvoice")
    assert 'INV_STATUS = j.idempotent_repeat ? null : "proposed"' in create


def test_a_ledger_payment_updates_the_status_from_the_server():
    fn = _extract("reloadPayments")
    got = _run('const EDIT_OID = "230"; let PAY_SEEN = null;'
               'function renderPayments(rows, bal){ PAY_SEEN = bal; }'
               'INV_STATUS = "proposed"; INV_PAY = "unpaid";'
               'REPLY = {ok: true, rows: [], balance: {}, order: {status: "new", pay_status: "paid"}};'
               + fn.replace("{headers:HEADERS}", "{headers:HEADERS, body:'{}'}") +
               'await reloadPayments();')
    assert (got["st"], got["pay"], got["btn"]) == ("new", "paid", "none")


def test_the_payments_list_route_returns_the_orders_status(tmp_path, monkeypatch):
    import importlib
    import sqlite3
    import sys
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    from dashboard import orders as O
    from dashboard import order_payments as OP
    from dashboard import rbac
    with sqlite3.connect(str(tmp_path / "chat_log.db")) as cx:
        O.init_orders_table(cx)
        OP.ensure_table(cx)
        O.upsert_order(cx, source="in-house", external_ref="INH-T", email="t@e.com",
                       total_cents=5000, status="proposed")
        cx.commit()
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    try:
        import app as appmod
        importlib.reload(appmod)
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"app not importable: {e}")
    monkeypatch.setattr(appmod, "_bos_actor", lambda: rbac.Actor(role="owner", name="owner"))
    appmod.app.config["TESTING"] = True
    g = appmod.app.test_client().get("/api/orders/1/payments").get_json()
    assert g["order"] == {"status": "proposed", "pay_status": "unpaid"}
