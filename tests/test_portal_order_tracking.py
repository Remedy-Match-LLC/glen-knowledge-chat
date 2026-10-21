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
    assert 'alt:"history-receipts"' in line.group(1)
    assert 'id="history-receipts"' in PAGE


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


def test_legacy_history_card_shows_tracking_too():
    # Without the tabs or the hub there is no orders panel: the same rows render in the
    # History & receipts card inside render(), which #orders reaches through `alt`.
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    d = {"scan_history_enabled": False, "past_invoices": [
        {"amount_dollars": "473.00", "paid": True, "when": "2026-10-10", "link": "",
         "tracking_number": USPS, "tracking_url": USPS_URL}]}
    js = "\n".join([
        "const h = require('./tests/lib/portal-render-harness.js');",
        "const src = h.read();",
        "function fn(n){const a=src.indexOf('\\nfunction '+n+'(');return src.slice(a, src.indexOf('\\n}', a)+2);}",
        "(0, eval)(src.match(/^const esc = .*$/m)[0].replace('const esc', 'globalThis.esc'));",
        "(0, eval)(fn('invoiceTrackingHtml') + fn('pastInvoiceItemsHtml'));",
        "process.stdout.write(h.render(src, {d: " + json.dumps(d) + "}));"])
    out = subprocess.run([node, "-e", js], capture_output=True, text=True, timeout=60, cwd=ROOT)
    assert out.returncode == 0, out.stderr[-2000:]
    card = out.stdout[out.stdout.index('id="history-receipts"'):]
    assert f'Tracking: <a href="{USPS_URL}"' in card
