# tests/test_portal_order_tracking.py
"""An order's tracking number shows in the portal's Orders & Invoices, and #orders
links there (Glen, 2026-10-21, via the primary).

Three things are pinned: the API field on each order row, the line the page renders
from it, and the #orders hash route an email can link to.
"""
import json
import re
import shutil
import sqlite3
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PAGE = (ROOT / "static" / "client-portal.html").read_text()
USPS = "9405511899223197428490"
USPS_URL = "https://tools.usps.com/go/TrackConfirmAction?tLabels=" + USPS


@pytest.fixture
def client(monkeypatch, tmp_path):
    import app as appmod
    monkeypatch.setattr(appmod, "LOG_DB", str(tmp_path / "chat_log.db"))
    appmod._init_auth_tables()
    appmod.app.config["TESTING"] = True
    return appmod.app.test_client(), appmod


def _seed(appmod, email):
    from dashboard import client_portal as cp
    from dashboard import orders as _orders
    cx = sqlite3.connect(appmod.LOG_DB)
    _orders.init_orders_table(cx)
    for col, ddl in (("portal_published", "INTEGER NOT NULL DEFAULT 0"), ("invoice_token", "TEXT")):
        try:
            cx.execute(f"ALTER TABLE orders ADD COLUMN {col} {ddl}")
        except Exception:
            pass
    ins = ("INSERT INTO orders (source,external_ref,name,email,status,pay_status,total_cents,"
           "items_json,address_json,created_at,portal_published,invoice_token,paid_at,"
           "tracking_number) VALUES ('t',?,'H',?,?,?,?,'[]','{}',?,?,?,?,?)")
    # Paid, USPS tracking, still 'new' (order 230's shape: tracking set, status not moved).
    cx.execute(ins, ("USPS", email, "new", "paid", 47300, "2026-10-10", 0, "", "2026-10-10", USPS))
    # Paid, a number no carrier in the code recognises.
    cx.execute(ins, ("OTHER", email, "done", "paid", 2000, "2026-09-10", 0, "", "2026-09-10",
                     "1Z999AA10123456784"))
    # Paid, no tracking.
    cx.execute(ins, ("NONE", email, "done", "paid", 3000, "2026-08-10", 0, "", "2026-08-10", None))
    # Unpaid and published: the live invoice card carries it too.
    cx.execute(ins, ("OPEN", email, "shipped", "unpaid", 5000, "2026-10-12", 1, "tok-open", None, USPS))
    cx.commit()
    cp.init_client_portal_table(cx)
    token, _ = cp.upsert_portal(cx, email, "H", {"biofield_status": "confirmed", "layers": []})
    cx.close()
    return token


# ── The API field ────────────────────────────────────────────────────────────

def test_portal_order_rows_carry_tracking(client):
    c, appmod = client
    tok = _seed(appmod, "track@example.com")
    j = c.get(f"/api/portal/{tok}").get_json()
    past = {p["amount_dollars"]: p for p in j["past_invoices"]}
    assert past["473.00"]["tracking_number"] == USPS
    assert past["473.00"]["tracking_url"] == USPS_URL
    assert past["20.00"]["tracking_number"] == "1Z999AA10123456784"
    assert past["20.00"]["tracking_url"] == ""          # unknown carrier: number, no link
    assert past["30.00"]["tracking_number"] == ""
    assert past["30.00"]["tracking_url"] == ""
    live = {i["token"]: i for i in j["invoices"]}
    assert live["tok-open"]["tracking_number"] == USPS
    assert live["tok-open"]["tracking_url"] == USPS_URL


def test_only_a_usps_shaped_number_gets_a_link():
    import app as appmod
    f = appmod._order_tracking_fields
    assert f(USPS) == {"tracking_number": USPS, "tracking_url": USPS_URL}
    assert f(" " + USPS + " ")["tracking_url"] == USPS_URL
    assert f("8405511899223197428490")["tracking_url"] == ""   # 22 digits, not 9-led
    assert f(USPS + "1")["tracking_url"] == ""                  # 23 digits
    assert f(None) == {"tracking_number": "", "tracking_url": ""}


# ── The rendered line ────────────────────────────────────────────────────────

def _fn(name):
    at = PAGE.index("\nfunction " + name + "(")
    return PAGE[at:PAGE.index("\n}", at) + 2]


def _render_orders(d):
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    esc = re.search(r"^const esc = .*$", PAGE, re.M).group(0)
    js = "\n".join([esc, _fn("foldSlug"), _fn("invoiceTrackingHtml"),
                    _fn("pastInvoiceItemsHtml"), _fn("buildOrdersHtml"),
                    "process.stdout.write(buildOrdersHtml(" + json.dumps(d) + "));"])
    out = subprocess.run([node, "-e", js], capture_output=True, text=True, timeout=20)
    assert out.returncode == 0, out.stderr
    return out.stdout


def test_orders_panel_shows_tracking_line_with_usps_link():
    html = _render_orders({"past_invoices": [
        {"amount_dollars": "473.00", "paid": True, "when": "2026-10-10", "link": "",
         "tracking_number": USPS, "tracking_url": USPS_URL}]})
    assert (f'Tracking: <a href="{USPS_URL}" target="_blank" rel="noopener">{USPS} ↗</a>'
            in html)


def test_orders_panel_tracking_without_link_and_absent_when_none():
    html = _render_orders({
        "invoices": [{"amount_dollars": "50.00", "link": "/invoice/x",
                      "tracking_number": "1Z999AA10123456784", "tracking_url": ""}],
        "past_invoices": [{"amount_dollars": "30.00", "paid": True, "when": "2026-08-10",
                           "link": "", "tracking_number": "", "tracking_url": ""}]})
    assert "Tracking: 1Z999AA10123456784</p>" in html
    assert html.count("Tracking:") == 1


def test_orders_rows_sit_inside_the_orders_target():
    html = _render_orders({"past_invoices": [
        {"amount_dollars": "9.00", "paid": True, "when": "2026-01-01", "link": ""}]})
    picker = html.index('id="orders-product-picker"')
    start = html.index('<div id="orders-invoices">')
    assert picker < start < html.index("Past invoices")
    assert html.rstrip().endswith("</div></div>")


# ── The #orders hash route ───────────────────────────────────────────────────

def test_orders_hash_route_points_at_the_orders_rows():
    table = re.search(r"const PORTAL_HASH_ROUTES = \{(.*?)\n\};", PAGE, re.S).group(1)
    line = re.search(r"^\s*orders:\s*\{(.*)\},?$", table, re.M)
    assert line, "#orders is not a portal hash route"
    assert 'panel:"orders"' in line.group(1)
    assert 'target:"orders-invoices"' in line.group(1)
    assert 'alt:"orders-legacy"' in line.group(1)
    assert "openCard:true" in line.group(1)


def test_orders_survives_sign_in():
    from dashboard import portal_return as ret
    assert ret.portal_me("orders") == "/portal/me#orders"


def test_every_hash_route_lands_in_a_panel_that_reveals_it():
    # The node check that places each route's target in a door. It is not run by CI on
    # its own, so it is run here.
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    out = subprocess.run([node, str(ROOT / "tests" / "test_portal_hash_routes.js")],
                         capture_output=True, text=True, timeout=60, cwd=ROOT)
    assert out.returncode == 0, out.stderr[-2000:]
    assert "test_portal_hash_routes: ok" in out.stdout


def _node(js):
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    out = subprocess.run([node, "-e", js], capture_output=True, text=True, timeout=60, cwd=ROOT)
    assert out.returncode == 0, out.stderr[-2000:]
    return out.stdout


def _render_legacy(d):
    """render() with no tabs and no hub: the layout with no orders panel."""
    return _node("\n".join([
        "const h = require('./tests/lib/portal-render-harness.js');",
        "process.stdout.write(h.render(h.read(), {d: " + json.dumps(d) + "}));"]))


PAST = {"amount_dollars": "473.00", "paid": True, "when": "2026-10-10", "link": "",
        "tracking_number": USPS, "tracking_url": USPS_URL}
OPEN = {"amount_dollars": "50.00", "link": "/invoice/x", "url": "x",
        "tracking_number": USPS, "tracking_url": USPS_URL}


def test_legacy_history_card_shows_tracking_too():
    html = _render_legacy({"scan_history_enabled": False, "past_invoices": [PAST]})
    card = html[html.index('id="orders-legacy"'):]
    assert card.startswith('id="orders-legacy" data-fold-id="scans-history-receipts"')
    assert f'Tracking: <a href="{USPS_URL}"' in card


def test_legacy_unpaid_shipped_order_shows_tracking_and_is_the_destination():
    # Round 2: an unpaid-only client with no history still has somewhere to land.
    html = _render_legacy({"scan_history_enabled": False, "invoices": [OPEN]})
    card = html[html.index('id="orders-legacy"'):]
    assert card.startswith('id="orders-legacy" data-fold-id="scans-invoice-x"')
    assert f'Tracking: <a href="{USPS_URL}"' in card.split("</div>")[0]
    # With both and no paid tracking, only the first order card carries the anchor.
    both = _render_legacy({"scan_history_enabled": False, "invoices": [OPEN],
                           "past_invoices": [PAST_NO_TRACK]})
    assert both.count('id="orders-legacy"') == 1
    assert both.index('id="orders-legacy"') < both.index("Your invoice")


# ── F1: the link opens the folded card it lands on ───────────────────────────

def _route_open_state(d, layout, route="orders", html=None):
    """First visit: which fold id a route opens, and the door's resolved folds.

    The page's own portalRouteFoldId, _foldIdOf and PortalFolds run against a stand-in
    for the DOM that answers querySelector/querySelectorAll/closest from the markup.
    `html` skips rendering and uses the markup given."""
    js = r"""
const h = require('./tests/lib/portal-render-harness.js');
const F = require('./static/js/portal-folds.js');
const src = h.read();
function fn(n){const a=src.indexOf('\nfunction '+n+'(');return src.slice(a, src.indexOf('\n}', a)+2);}
const D = __D__;
let html = __HTML__;
if (html === null && __LAYOUT__ === 'panel') {
  (0, eval)(src.match(/^const esc = .*$/m)[0].replace('const esc', 'globalThis.esc'));
  (0, eval)(fn('foldSlug') + fn('invoiceTrackingHtml') + fn('pastInvoiceItemsHtml') + fn('buildOrdersHtml'));
  html = buildOrdersHtml(D);
} else if (html === null) {
  html = h.render(src, {d: D});
}
// Every card, in page order, as the fold glue sees them. A card's body runs to the
// next card, which is how these flat card lists are laid out.
const cards = [];
const re = /<div class="card[^"]*"([^>]*)>/g; let m;
while ((m = re.exec(html))) {
  const a = m[1], id = /\sid="([^"]+)"/.exec(a), fid = /data-fold-id="([^"]+)"/.exec(a);
  cards.push({at: m.index, id: id ? id[1] : '', dataset: {foldId: fid ? fid[1] : undefined,
              foldOpen: /data-fold-open/.test(a) ? '1' : undefined}});
}
cards.forEach((c, i) => {
  const body = html.slice(c.at, i + 1 < cards.length ? cards[i + 1].at : html.length);
  c.querySelector = sel => (sel === '.invoice-tracking' && body.indexOf('invoice-tracking') !== -1) ? {} : null;
});
function El(id, at){
  const card = cards.find(c => c.at === at);
  const inside = cards.filter(c => c.at > at && c.dataset.foldId);
  return {id: id, dataset: card ? card.dataset : {},
    matches(){ return !!card; },
    querySelector(){ return inside[0] || null; },
    querySelectorAll(sel){ return sel.indexOf(':not([data-fold-open])') !== -1
                                 ? inside.filter(c => !c.dataset.foldOpen) : inside; },
    closest(){ return card || null; }};
}
globalThis.document = {getElementById(id){
  const i = html.indexOf('id="' + id + '"'); if (i === -1) return null;
  return El(id, html.lastIndexOf('<div', i));
}};
(0, eval)(fn('_foldIdOf') + fn('portalRouteFoldId'));
const ROUTES = (0, eval)('(' + src.slice(src.indexOf('{', src.indexOf('const PORTAL_HASH_ROUTES = {')),
                                       src.indexOf('\n};', src.indexOf('const PORTAL_HASH_ROUTES = {')) + 2) + ')');
const foldId = portalRouteFoldId(ROUTES[__ROUTE__]);
const ids = cards.map(c => c.dataset.foldId || c.id);
const openIds = cards.filter(c => c.dataset.foldOpen).map(c => c.dataset.foldId || c.id);
const before = F.resolveDoor(F.emptyState(), ids, 'billing', openIds);
const after = F.resolveDoor(F.setCard(F.emptyState(), foldId, false), ids, 'billing', openIds);
process.stdout.write(JSON.stringify({foldId, ids, before, after}));
""".replace("__D__", json.dumps(d)).replace("__LAYOUT__", json.dumps(layout)) \
       .replace("__ROUTE__", json.dumps(route)).replace("__HTML__", json.dumps(html))
    return json.loads(_node(js))


def test_orders_link_opens_the_folded_past_invoices_card_on_first_visit():
    r = _route_open_state({"past_invoices": [PAST]}, "panel")
    assert r["ids"][0] == "orders-product-picker"
    assert r["foldId"] == "billing-past-invoices"
    assert r["before"]["billing-past-invoices"] is True      # folded without the link
    assert r["after"]["billing-past-invoices"] is False       # the link opens it


def test_orders_link_opens_the_legacy_history_card_on_first_visit():
    r = _route_open_state({"scan_history_enabled": False, "past_invoices": [PAST]}, "legacy")
    assert r["foldId"] == "scans-history-receipts"
    assert r["after"]["scans-history-receipts"] is False


def test_portal_route_fold_id_keeps_a_plain_card_target():
    # #calendar and #chat target a card with no fold name: its id stays the fold id.
    fn = PAGE[PAGE.index("\nfunction portalRouteFoldId("):]
    fn = fn[:fn.index("\n}") + 2]
    assert 'el.matches("[data-fold-id], .card") ? el' in fn
    assert "return (card && _foldIdOf(card)) || route.target;" in fn


# ── F2: a superseded order shows no tracking ────────────────────────────────

def test_superseded_order_keeps_its_row_but_not_its_tracking(client):
    c, appmod = client
    from dashboard import orders as _orders
    email = "sup@example.com"
    tok = _seed(appmod, email)
    cx = sqlite3.connect(appmod.LOG_DB)
    ids = dict(cx.execute("SELECT external_ref, id FROM orders WHERE email=?", (email,)).fetchall())
    _orders.supersede_order(cx, ids["USPS"], ids["NONE"])
    _orders.supersede_order(cx, ids["OPEN"], ids["NONE"])
    cx.close()
    j = c.get(f"/api/portal/{tok}").get_json()
    past = {p["amount_dollars"]: p for p in j["past_invoices"]}
    assert past["473.00"]["tracking_number"] == "" and past["473.00"]["tracking_url"] == ""
    assert past["20.00"]["tracking_number"] == "1Z999AA10123456784"   # others untouched
    assert {i["token"]: i for i in j["invoices"]}["tok-open"]["tracking_number"] == ""


# ── F4: a spaced or IMpb USPS number still gets its link ─────────────────────

def test_spaced_and_impb_usps_numbers_link_to_the_normalised_number():
    import app as appmod
    f = appmod._order_tracking_fields
    spaced = "9405 5118 9922 3197 4284 90"
    assert f(spaced) == {"tracking_number": spaced, "tracking_url": USPS_URL}
    assert f("42096720" + USPS)["tracking_url"] == USPS_URL


# ── F6: markup in a tracking number is shown as text ─────────────────────────

def test_markup_in_a_tracking_number_is_escaped_and_unlinked():
    import app as appmod
    bad = "<img src=x onerror=alert(1)>"
    assert appmod._order_tracking_fields(bad) == {"tracking_number": bad, "tracking_url": ""}
    html = _render_orders({"past_invoices": [dict(PAST, tracking_number=bad, tracking_url="")]})
    assert "Tracking: &lt;img src=x onerror=alert(1)&gt;</p>" in html
    assert "<img" not in html


# ── Round 3 ──────────────────────────────────────────────────────────────────

PAST_NO_TRACK = dict(PAST, tracking_number="", tracking_url="")


def test_orders_link_opens_the_paid_card_over_an_open_invoice_card():
    # An unpaid invoice comes first and is open anyway; the paid order with tracking
    # sits in the Past invoices card, which would fold. That card is the one opened.
    r = _route_open_state({"invoices": [OPEN], "past_invoices": [PAST]}, "panel")
    assert r["foldId"] == "billing-past-invoices"
    assert r["before"]["billing-past-invoices"] is True
    assert r["after"]["billing-past-invoices"] is False


def test_orders_link_falls_back_to_the_invoice_card_without_paid_tracking():
    r = _route_open_state({"invoices": [OPEN], "past_invoices": [PAST_NO_TRACK]}, "panel")
    assert r["foldId"] == "billing-invoice-x"


def test_legacy_orders_link_opens_the_history_card_when_a_paid_order_is_tracked():
    d = {"scan_history_enabled": False, "invoices": [OPEN], "past_invoices": [PAST]}
    r = _route_open_state(d, "legacy")
    assert r["foldId"] == "scans-history-receipts"
    assert r["after"]["scans-history-receipts"] is False
    html = _render_legacy(d)
    assert html.count('id="orders-legacy"') == 1
    assert html.index('id="orders-legacy"') > html.index("Your invoice")


def test_legacy_orders_link_falls_back_to_the_invoice_card():
    d = {"scan_history_enabled": False, "invoices": [OPEN], "past_invoices": [PAST_NO_TRACK]}
    assert _route_open_state(d, "legacy")["foldId"] == "scans-invoice-x"


def test_calendar_under_the_hub_still_opens_the_full_calendar():
    # The hub renders both the summary (alt) and the full card (target). The target wins.
    html = ('<div class="card calendar-summary" data-fold-skip="1" id="calendar-summary">'
            '<h2>Upcoming Live Events</h2></div>'
            '<div class="card" id="calendar-card"><h2>Live Calendar</h2></div>')
    assert _route_open_state({}, "panel", route="calendar", html=html)["foldId"] == "calendar-card"


def test_a_fedex_96_number_gets_no_usps_link():
    import app as appmod
    f = appmod._order_tracking_fields
    assert f("9612019123456789012345")["tracking_url"] == ""
    assert f("9205590164917312751089")["tracking_url"].endswith("9205590164917312751089")
    assert f("9361289698090314734271")["tracking_url"].endswith("9361289698090314734271")
    assert f("9500110000000000000000")["tracking_url"].endswith("9500110000000000000000")


def test_markup_around_a_linked_number_is_escaped():
    bad = "<b>" + USPS + "</b>"
    html = _render_orders({"past_invoices": [dict(PAST, tracking_number=bad)]})
    assert "<b>" not in html
    assert f"&lt;b&gt;{USPS}&lt;/b&gt; ↗</a>" in html


def test_another_clients_tracking_never_reaches_this_portal(client):
    c, appmod = client
    tok = _seed(appmod, "mine@example.com")
    other = "9405500000000000000077"
    cx = sqlite3.connect(appmod.LOG_DB)
    for ref, pay, status, pub, itok in (("THEIRS", "paid", "done", 0, ""),
                                        ("THEIRS-OPEN", "unpaid", "shipped", 1, "tok-theirs")):
        cx.execute("INSERT INTO orders (source,external_ref,name,email,status,pay_status,"
                   "total_cents,items_json,address_json,created_at,portal_published,"
                   "invoice_token,paid_at,tracking_number) "
                   "VALUES ('t',?,'O','other@example.com',?,?,7700,'[]','{}','2026-10-11',?,?,"
                   "'2026-10-11',?)", (ref, status, pay, pub, itok, other))
    cx.commit(); cx.close()
    body = c.get(f"/api/portal/{tok}").get_data(as_text=True)
    assert USPS in body                      # this client's own tracking is there
    assert other not in body and "77.00" not in body
