import sqlite3

import pytest

import app as appmod
from dashboard import shipping


@pytest.fixture
def _seeded_shipping(monkeypatch, tmp_path):
    """Isolate from ambient DB state. These tests call shipping functions with
    db_path=None, which resolves the default DB at call time — so in the full
    suite they hit whatever DB exists (often missing the usps_rates table:
    `no such table`). Seed a tmp DB via init_shipping_schema (default rates +
    EMPTY box-fit catalog — exactly the state these fallback tests assume) and
    point shipping's default-path resolver at it. Surgical: only shipping's
    default path is redirected, no pollution to other tests."""
    dbp = str(tmp_path / "chat_log.db")
    with sqlite3.connect(dbp) as cx:
        shipping.init_shipping_schema(cx)
    monkeypatch.setattr(shipping, "_default_db_path", lambda: dbp)


def _rates():
    return shipping.get_current_rates()


def test_fallback_shipping_thresholds(_seeded_shipping):
    r = _rates()
    assert appmod._fallback_shipping_cents(0) == 0
    assert appmod._fallback_shipping_cents(1) == r["S"]["charged_cents"]
    assert appmod._fallback_shipping_cents(4) == r["S"]["charged_cents"]      # boundary -> S
    assert appmod._fallback_shipping_cents(5) == r["M"]["charged_cents"]      # -> M
    assert appmod._fallback_shipping_cents(12) == r["M"]["charged_cents"]     # boundary -> M
    assert appmod._fallback_shipping_cents(13) == r["L"]["charged_cents"]     # -> L
    assert appmod._fallback_shipping_cents(20) == r["L"]["charged_cents"]


def test_shipping_for_cart_falls_back_on_empty_catalog(_seeded_shipping):
    # The box-fit catalog is empty, so quote() raises UnknownBottleType -> qty fallback.
    r = _rates()
    assert appmod._shipping_for_cart({"default": 3}, 3) == r["S"]["charged_cents"]
    assert appmod._shipping_for_cart({"default": 8}, 8) == r["M"]["charged_cents"]
    assert appmod._shipping_for_cart({}, 0) == 0


def _unmeasured(monkeypatch):
    monkeypatch.setattr(appmod, "_get_product",
        lambda s: {"slug": s, "name": "Brain Boost", "price_cents": 7000,
                   "qty_pricing": True, "qbo_item_id": "27", "bottle_type": None} if s == "brain-boost" else None)


def test_operator_path_blocks_unknown_packaging_instead_of_guessing(monkeypatch, _seeded_shipping):
    # The original bug: _price_cart keyed shipping by product NAME -> UnknownBottleType -> 500.
    # #1346 made unknown packaging loud so an operator enters real dimensions.
    _unmeasured(monkeypatch)
    with pytest.raises(appmod.CheckoutError, match="Packaging specifications required"):
        appmod._price_cart([{"slug": "brain-boost", "qty": 6}],
                           ship={"state": "CA", "country": "US", "name": "B"},
                           strict_packaging=True)


def test_client_path_completes_the_sale_and_flags_it(monkeypatch, _seeded_shipping):
    """Inverted 2026-08-20. This used to assert the CLIENT was blocked too, which
    put 79 live products behind a 400 at checkout showing an operator instruction.
    A client has no way to enter package dimensions, so the sale completes at the
    fallback bottle and the gap is reported instead. See
    tests/test_unknown_packaging_fallback.py."""
    _unmeasured(monkeypatch)
    pc = appmod._price_cart([{"slug": "brain-boost", "qty": 6}],
                            ship={"state": "CA", "country": "US", "name": "B"})
    assert pc["shipping_cents"] > 0
    assert pc["packaging_review"] == ["Brain Boost"]


def test_owner_override_may_bypass_unknown_geometry(monkeypatch, _seeded_shipping):
    monkeypatch.setattr(appmod, "_get_product",
        lambda s: {"slug": s, "name": "Unknown Widget", "price_cents": 1000,
                   "bottle_type": None} if s == "widget" else None)
    pc = appmod._price_cart(
        [{"slug": "widget", "qty": 1}],
        ship={"state": "CA", "country": "US", "name": "B"},
        allow_unknown_packaging=True)
    assert pc["shipping_cents"] == 0
