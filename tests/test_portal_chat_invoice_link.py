"""Agnes Verches, 2026-11-12: the portal chat sent her to a dead, unclickable
"truly.vip/help" for her invoices. The concierge prompt now carries the real route,
written in full, and the chat bubbles make links clickable (tests/test_portal_chat_links.js)."""
import pathlib
import re
import shutil
import subprocess

import pytest

from dashboard import portal_concierge as pcz
from dashboard import portal_return

ROOT = pathlib.Path(__file__).resolve().parents[1]


def test_orders_url_is_the_portal_orders_route():
    assert pcz.PORTAL_ORDERS_URL == "https://myhealingoasis.com/portal/me#orders"
    # The hash must be a route the portal and sign-in both know.
    page = (ROOT / "static" / "client-portal.html").read_text()
    routes = page[page.index("const PORTAL_HASH_ROUTES = {"):]
    routes = routes[:routes.index("\n};")]
    assert re.search(r"^\s*orders:\s*\{", routes, re.M)
    assert "orders" in portal_return.ROUTE_KEYS


def test_prompt_gives_the_invoice_link_in_full():
    sys = pcz.system_prompt({})
    line = next(l for l in sys.splitlines() if l.startswith("- Invoices"))
    assert line.endswith(": https://myhealingoasis.com/portal/me#orders")
    assert "Never send them anywhere else to find or pay an invoice." in line
    assert "- Write every link in full, starting with https://." in sys
    assert "truly.vip/help" not in sys


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
def test_portal_chat_links_node():
    r = subprocess.run(["node", str(ROOT / "tests" / "test_portal_chat_links.js")],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, (r.stdout or "") + (r.stderr or "")
    assert "OK" in r.stdout
