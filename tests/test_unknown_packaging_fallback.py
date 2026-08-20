# tests/test_unknown_packaging_fallback.py
"""A client must never be refused a sale because WE have not measured a bottle.

#1346 replaced the old silent "default" bottle fallback with a hard CheckoutError.
Right instinct -- the silent default mis-rated whole carts -- but the guard went
into `_price_cart`, which all fifteen callers share, while only the in-house order
builder was wired to handle it. On 2026-08-20 that left 79 live, priced, shippable
products returning a 400 at checkout with an operator instruction as the error
text ("Enter its package type and size before using automatic shipping").

Now:
  * client paths pack an unspecified line at FALLBACK_BOTTLE_TYPE and report it in
    `packaging_review`, so the sale completes and the gap is still visible;
  * operator paths pass strict_packaging=True and keep the hard stop, because Rae
    can enter the real dimensions on the spot.

Glen, 2026-08-20, on the fallback size: single ingredient Pure Powders go in the
bottle used for 120 caps unless the powder is expensive, in which case it is a
SMALLER bottle, "so the larger bottle can be the fallback for shipping size."
"""

import importlib
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent


def _app():
    if str(REPO) not in sys.path:
        sys.path.insert(0, str(REPO))
    try:
        return importlib.import_module("app")
    except Exception as e:  # pragma: no cover
        pytest.skip(f"app module not importable in this env: {e}")


UNMEASURED = {"slug": "lutein", "name": "Lutein", "price_cents": 3997,
              "qty_pricing": True, "qbo_item_id": "27"}
MEASURED = {"slug": "brain-boost", "name": "Brain Boost", "price_cents": 7000,
            "qty_pricing": True, "qbo_item_id": "27", "bottle_type": "30 Caps"}


def _stub(app, monkeypatch, product):
    monkeypatch.setattr(app, "_get_product", lambda s: product if s == product["slug"] else None)


# ---------------------------------------------------------------------------
# The fallback itself
# ---------------------------------------------------------------------------

def test_fallback_is_the_pure_powder_bottle():
    from dashboard import shipping as sh
    assert sh.FALLBACK_BOTTLE_TYPE == "120 caps"
    # It has to be a type prod can actually rate, or the packer raises and the whole
    # cart silently drops to the coarse qty rule.
    assert sh.FALLBACK_BOTTLE_TYPE in sh.PROD_BOTTLE_NAMES
    dims = {name: (d, h) for name, _desc, d, h in sh._STANDARD_BOTTLES}
    assert dims[sh.FALLBACK_BOTTLE_TYPE] == (72, 100)


def test_fallback_is_not_the_oversized_own_box():
    """own-box routes to its own parcel; using it here would grossly overcharge."""
    from dashboard import shipping as sh
    assert sh.FALLBACK_BOTTLE_TYPE != "own-box"


def test_fallback_covers_every_capsule_and_powder_bottle_at_or_below_it():
    """Never undercharge the powder line: the fallback must not be smaller than the
    bottles it stands in for."""
    from dashboard import shipping as sh
    dims = {name: (d, h) for name, _desc, d, h in sh._STANDARD_BOTTLES}
    fd, fh = dims[sh.FALLBACK_BOTTLE_TYPE]
    for smaller in ("30 Caps", "30 g", "15ml", "Dropper 5 mL", "Dropper 30 mL"):
        d, h = dims[smaller]
        assert d <= fd and h <= fh, f"{smaller} is bigger than the fallback"


# ---------------------------------------------------------------------------
# Client path: the sale completes
# ---------------------------------------------------------------------------

def test_client_cart_prices_an_unmeasured_product_instead_of_refusing(monkeypatch):
    app = _app()
    _stub(app, monkeypatch, UNMEASURED)
    monkeypatch.setattr(app._shipping, "quote", lambda b: {"shipping_cents": 995, "box": "S"})
    out = app._price_cart([{"slug": "lutein", "qty": 1}],
                          ship={"state": "CA", "country": "US"})
    assert out["shipping_cents"] == 995
    assert out["qbo_lines"][0]["amount"] == 39.97


def test_client_cart_reports_what_it_had_to_guess(monkeypatch):
    app = _app()
    _stub(app, monkeypatch, UNMEASURED)
    monkeypatch.setattr(app._shipping, "quote", lambda b: {"shipping_cents": 995})
    out = app._price_cart([{"slug": "lutein", "qty": 1}],
                          ship={"state": "CA", "country": "US"})
    assert out["packaging_review"] == ["Lutein"]


def test_a_measured_product_is_never_flagged(monkeypatch):
    app = _app()
    _stub(app, monkeypatch, MEASURED)
    monkeypatch.setattr(app._shipping, "quote", lambda b: {"shipping_cents": 995})
    out = app._price_cart([{"slug": "brain-boost", "qty": 1}],
                          ship={"state": "CA", "country": "US"})
    assert out["packaging_review"] == []


def test_the_guess_packs_at_the_fallback_not_at_nothing(monkeypatch):
    """The line must reach the packer. Dropping it would undercharge the cart."""
    app = _app()
    from dashboard import shipping as sh
    _stub(app, monkeypatch, UNMEASURED)
    seen = {}
    monkeypatch.setattr(app._shipping, "quote",
                        lambda b: seen.update(boxes=b) or {"shipping_cents": 995})
    app._price_cart([{"slug": "lutein", "qty": 3}], ship={"state": "CA", "country": "US"})
    assert seen["boxes"].get(sh.FALLBACK_BOTTLE_TYPE) == 3


def test_review_list_dedupes(monkeypatch):
    app = _app()
    _stub(app, monkeypatch, UNMEASURED)
    monkeypatch.setattr(app._shipping, "quote", lambda b: {"shipping_cents": 995})
    out = app._price_cart([{"slug": "lutein", "qty": 1}, {"slug": "lutein", "qty": 2}],
                          ship={"state": "CA", "country": "US"})
    assert out["packaging_review"] == ["Lutein"]


# ---------------------------------------------------------------------------
# Operator path: the hard stop stays
# ---------------------------------------------------------------------------

def test_operator_path_still_refuses_an_unmeasured_product(monkeypatch):
    app = _app()
    _stub(app, monkeypatch, UNMEASURED)
    monkeypatch.setattr(app._shipping, "quote", lambda b: {"shipping_cents": 995})
    with pytest.raises(app.CheckoutError) as e:
        app._price_cart([{"slug": "lutein", "qty": 1}],
                        ship={"state": "CA", "country": "US"}, strict_packaging=True)
    assert "Lutein" in str(e.value)


def test_the_three_operator_routes_ask_for_the_hard_stop():
    """Wiring check: the operator routes opt in, the token-authed customer control
    on an invoice does not -- it has no operator to fix anything."""
    lines = (REPO / "app.py").read_text(encoding="utf-8").splitlines()
    wired = [l for l in lines
             if "strict_packaging=True" in l and not l.strip().startswith("#")]
    assert len(wired) == 3, wired


def test_pickup_still_skips_the_line_entirely(monkeypatch):
    """allow_unknown_packaging is a different question and must be unchanged: a
    pickup order ships nothing, so the line is dropped rather than guessed."""
    app = _app()
    _stub(app, monkeypatch, UNMEASURED)
    monkeypatch.setattr(app._shipping, "quote", lambda b: {"shipping_cents": 0})
    out = app._price_cart([{"slug": "lutein", "qty": 1}],
                          ship={"state": "CA", "country": "US"},
                          allow_unknown_packaging=True)
    assert out["packaging_review"] == []


def test_admin_list_marks_unmeasured_products_as_unassigned():
    """Rae's review list: an unmeasured product must READ as unassigned. The
    selected-state test keyed off the retired "default" sentinel, so since #1346
    those rows silently showed the first bottle in the dropdown instead."""
    html = (REPO / "static" / "admin-shipping.html").read_text(encoding="utf-8")
    assert 'item.resolved === "default"' not in html
    assert "needs measuring" in html
