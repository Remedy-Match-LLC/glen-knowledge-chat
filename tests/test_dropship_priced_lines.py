"""A drop-ship order must record what each bottle cost, not just the total.

The practitioner cart stores only {slug, qty} (dashboard/practitioner_portal.cart_items),
and the checkout route passed that straight into _ingest_order. So every drop-ship
order since the flow went live recorded every line at $0.00 while carrying a correct
order total.

Seen on Ashley King's #115, #116, #144 and #148: totals right, every line $0.00. Her
saved $40 rate meant the money was never wrong, but the record could not tell her what
she bought, and her invoice showed a $303.00 total over six $0.00 lines.

build_dropship_order already prices every line through quote_dropship_cart; it just
never handed them back.
"""
import pytest

from dashboard import dropship_checkout as dc


PRAC = {"id": "test-pid", "modules_completed": 0,
        "email": "prac@example.com", "name": "Test Prac"}
SHIP = {"name": "A Patient", "street": "1 Main", "city": "Austin",
        "state": "TX", "zip": "78701", "country": "US"}


@pytest.fixture(autouse=True)
def _no_wallet(monkeypatch):
    """Wallet writes hit a real store; this is about the line record, not credit."""
    monkeypatch.setattr(dc.wallet, "redeem_for_order", lambda *a, **k: 0)
    monkeypatch.setattr(dc.wallet, "earn_fee_free", lambda *a, **k: 0)


def _build(cart, **kw):
    out = dc.build_dropship_order(cart, PRAC, patient_ship=SHIP, **kw)
    assert out.get("ok"), out
    return out


def test_the_order_carries_a_price_for_every_line():
    out = _build([{"slug": "terrain-restore", "qty": 1}, {"slug": "iron-out", "qty": 2}])
    lines = out["lines"]
    assert [l["slug"] for l in lines] == ["terrain-restore", "iron-out"]
    assert [l["qty"] for l in lines] == [1, 2]
    assert all(int(l["unit_cents"]) > 0 for l in lines), lines


def test_the_line_prices_are_the_ones_the_practitioner_was_quoted():
    """Not a re-derivation. The recorded price must be the quote they saw, or the
    invoice and the charge describe different orders."""
    cart = [{"slug": "terrain-restore", "qty": 2}, {"slug": "iron-out", "qty": 1}]
    quote = dc.quote_dropship_cart(cart, PRAC)
    out = _build(cart)
    assert {(l["slug"], l["unit_cents"]) for l in out["lines"]} == \
           {(l["slug"], l["unit_cents"]) for l in quote["lines"]}


def test_the_lines_add_up_to_the_subtotal_that_is_charged():
    out = _build([{"slug": "terrain-restore", "qty": 3}, {"slug": "iron-out", "qty": 1}])
    assert sum(l["unit_cents"] * l["qty"] for l in out["lines"]) == out["subtotal_cents"]


def test_shipping_is_not_smuggled_in_as_a_product_line():
    """Shipping is its own field. A shipping line among the products would be
    re-priced as a product by anything that later reprices the order."""
    out = _build([{"slug": "terrain-restore", "qty": 1}], shipping_cents=2300)
    assert out["shipping_cents"] == 2300
    assert not any("ship" in l["slug"].lower() for l in out["lines"])
    assert sum(l["unit_cents"] * l["qty"] for l in out["lines"]) == out["subtotal_cents"]


def test_an_empty_cart_still_refuses_rather_than_recording_nothing():
    assert dc.build_dropship_order([], PRAC, patient_ship=SHIP)["ok"] is False


def test_the_checkout_route_records_the_priced_lines_not_the_cart():
    """The fix has to reach the WRITE, not just the builder.

    Parsed rather than grepped: a mention in the comment that explains this very
    defect would otherwise satisfy a string search.
    """
    import ast
    import pathlib
    src = pathlib.Path(__file__).resolve().parent.parent / "app.py"
    tree = ast.parse(src.read_text())
    calls = [c for f in ast.walk(tree) if isinstance(f, ast.FunctionDef)
             for c in ast.walk(f)
             if isinstance(c, ast.Call) and isinstance(c.func, ast.Name)
             and c.func.id == "_ingest_order"
             and any(k.arg == "source" and getattr(k.value, "value", None) == "dropship"
                     for k in c.keywords)]
    # There are THREE: the practitioner checkout and two console paths. Fixing only
    # the first would have left the console, which is where Ashley's #115 and #116
    # were created.
    assert len(calls) == 3, "dropship ingest sites changed: found %d" % len(calls)
    for c in calls:
        items = next(k.value for k in c.keywords if k.arg == "items")
        names = {n.id for n in ast.walk(items) if isinstance(n, ast.Name)}
        assert "out" in names, (
            "dropship ingest at line %d does not record the builder's priced lines" % c.lineno)
