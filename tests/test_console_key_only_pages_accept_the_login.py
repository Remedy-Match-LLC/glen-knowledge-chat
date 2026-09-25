"""Five more console pages refused a signed-in owner who had no stored key.

2026-09-25: Rae clicked Edit on Order #196; /orders/new showed "Missing or invalid console
key" because init() returned on `!KEY` before asking the server, which accepts her owner
cookie. Same flaw as #1807, on pages it did not cover. Each page now asks the server first
and shows the warning only on a 401. Shaira's workspace keeps its gate: she signs in with a
VA token, not an owner login.
"""
import re
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parent.parent / "static"
PAGES = ["order-new", "admin-shipping", "console-inbox", "admin-ingredients", "console-settings"]


def _code(page):
    src = (STATIC / f"{page}.html").read_text()
    return re.sub(r"(?m)^\s*//.*$", "", src)


@pytest.mark.parametrize("page", PAGES)
def test_no_page_gates_on_a_stored_key(page):
    js = _code(page)
    assert not re.search(r"if\s*\(\s*!\s*KEY\s*\)", js), page


@pytest.mark.parametrize("page", PAGES)
def test_a_401_still_shows_the_warning(page):
    js = _code(page)
    assert re.search(r"status\s*===\s*401\s*\)\s*\{?\s*[^}]*auth-warn[^}]*display\s*=\s*['\"]block", js), page


def test_the_edit_invoice_link_carries_no_key():
    js = _code("console-orders")
    m = re.search(r"function editInvoice\(id\)\{([^}]*)\}", js)
    assert m and "key" not in m.group(1)


def test_shairas_workspace_keeps_its_gate():
    assert re.search(r"if\s*\(\s*!\s*KEY\s*\)", (STATIC / "shaira-workspace.html").read_text())
