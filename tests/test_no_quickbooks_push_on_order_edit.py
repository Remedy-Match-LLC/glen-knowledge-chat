"""Editing an order must never write to QuickBooks.

Glen, 2026-09-09: no invoices in QuickBooks, because they caused duplication and accounting
problems. Glen, 2026-09-21: "yes, stop it sending to QuickBooks", about this path.

`_push_invoice_edit_to_qbo` was the last live path that creates QuickBooks product items. It
was gated only by `external_ref.isdigit()`. New orders carry an `INH-` reference and skipped
it, but any order from before the cutover still holding a numeric QuickBooks invoice id
pushed through `qbo_billing.replace_invoice_lines`, which calls `find_or_create_item` for any
line with no item id. That is how the stray item "sleep-syntropy" (QBO item 94) was created
on 2026-07-27.

THE TRAP IN TESTING THIS. The old code already skipped any NON-numeric ref. A test using an
`INH-` reference would pass before and after the change and prove nothing. So every test here
uses a NUMERIC reference, which is the one case the old code pushed, and asserts the push was
not merely skipped but never reached QuickBooks at all.

The fake RECORDS calls rather than raising. A fake that raised would be caught by the
function's own try/except and read as "no push happened", which is exactly the false green
this is guarding against.
"""
import importlib
import sys
from pathlib import Path

import pytest

NUMERIC_REF = "10482"          # a real-looking pre-cutover QuickBooks invoice id


def _app():
    repo = Path(__file__).resolve().parent.parent
    if str(repo) not in sys.path:
        sys.path.insert(0, str(repo))
    try:
        return importlib.import_module("app")
    except Exception as e:
        pytest.skip(f"app not importable: {e}")


def _priced():
    """Shaped exactly like what the edit routes pass, so the OLD code would have reached
    replace_invoice_lines rather than failing earlier and passing for the wrong reason."""
    # A slug with NO catalog product, so it has no qbo_item_id and the old code's
    # item-resolution loop falls through to find_or_create_item. sleep-syntropy would not
    # do: it carries qbo_item_id "48" today and never reaches the item-minting call.
    return {"items_rec": [{"slug": "a-line-quickbooks-has-never-seen",
                           "name": "A Line QuickBooks Has Never Seen",
                           "unit_cents": 6997, "qty": 1}],
            "shipping_cents": 0, "discount_cents": 0, "points_redeemed_cents": 0,
            "adjustment_cents": 0}


@pytest.fixture
def qbo_calls(monkeypatch):
    """Record every QuickBooks NETWORK edge, and let the real code above them run.

    The first version of this fixture stubbed replace_invoice_lines itself. That made
    test_no_stray_quickbooks_item_can_be_created pass against the OLD code, because the
    real replace_invoice_lines, which is what calls find_or_create_item, never ran. So
    only the edges are faked here: get_invoice, find_or_create_item and _post. The real
    item-resolution loop in between is exercised, which is the code that minted QBO
    item 94."""
    from dashboard import qbo_billing
    calls = []
    monkeypatch.setattr(qbo_billing, "get_invoice",
                        lambda *a, **k: calls.append(("get_invoice", a, k))
                        or {"Id": NUMERIC_REF, "SyncToken": "3"})
    monkeypatch.setattr(qbo_billing, "find_or_create_item",
                        lambda *a, **k: calls.append(("find_or_create_item", a, k)) or {"Id": "94"})
    monkeypatch.setattr(qbo_billing, "_post",
                        lambda *a, **k: calls.append(("_post", a, k)) or {"Invoice": {}})
    return calls


def test_an_order_with_a_numeric_quickbooks_id_is_not_pushed(qbo_calls):
    """The defect, stated as a test. This is the case the old code DID push."""
    appmod = _app()
    appmod._push_invoice_edit_to_qbo(NUMERIC_REF, _priced())
    assert qbo_calls == [], f"QuickBooks was written to: {qbo_calls}"


def test_the_skip_keeps_the_contract_the_callers_rely_on(qbo_calls):
    """All four callers read `pushed` and pass the dict through. It must stay a dict with
    pushed False and a skipped reason, never raise, and never report a push."""
    appmod = _app()
    out = appmod._push_invoice_edit_to_qbo(NUMERIC_REF, _priced())
    assert out.get("pushed") is False
    assert "retired" in (out.get("skipped") or "").lower()
    assert "warning" not in out, "a retired sync is not a failure and must not warn"


def test_no_stray_quickbooks_item_can_be_created(qbo_calls):
    """find_or_create_item is what minted "sleep-syntropy" as QBO item 94. A line with no
    qbo_item_id is exactly what used to reach it."""
    appmod = _app()
    appmod._push_invoice_edit_to_qbo(NUMERIC_REF, _priced())
    assert not [c for c in qbo_calls if c[0] == "find_or_create_item"]
