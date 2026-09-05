"""Glen's ruling 2026-09-04: a wholesale sale earns no points of any kind.

A drop-ship or dispensary order is compensated by the practitioner's own margin. Before
this, two credits could still fire on one: the affiliate referral credit, and buyer earn.
Most drop-ships go to a client of the affiliate practitioner, so in the normal case the
same person collected the wholesale margin AND the referral credit for a single sale.

The trap this guards against: the existing "full price only" rule does NOT catch it. That
rule tests whether anything was taken OFF the order (points redeemed, shipping credit).
Wholesale pricing is baked into the price and never appears as a discount, so a wholesale
sale at or above the MAP floor reads as full price. Anyone "fixing" this by copying the
full-price rule around would get the appearance of protection with none of the substance.
"""
import re
from pathlib import Path

APP = Path(__file__).resolve().parent.parent / "app.py"
SRC = APP.read_text()


def _detector():
    start = SRC.index("_WHOLESALE_SOURCES = {")
    end = SRC.index("def _rewards_enabled")
    ns = {}
    exec(compile(SRC[start:end], "app_excerpt", "exec"), ns)
    return ns["_is_wholesale_order"]


def test_both_wholesale_channels_are_recognised():
    f = _detector()
    for src in ("dropship", "dispensary", "DROPSHIP", " Dispensary "):
        assert f({"source": src}), f"{src!r} must count as wholesale"


def test_retail_orders_are_not_wholesale():
    f = _detector()
    for src in ("retail", "reorder", "portal-reorder", "", None):
        assert not f({"source": src}), f"{src!r} must NOT count as wholesale"
    assert not f({})
    assert not f(None)


def test_affiliate_referral_is_suppressed_on_a_wholesale_sale():
    """The leak this closes: practitioner margin plus affiliate credit for one sale."""
    body = SRC[SRC.index("def _settle_referral("):SRC.index("def _settle_referral(") + 1800]
    assert "_is_wholesale_order(order)" in body, (
        "the referral settler no longer checks for a wholesale sale"
    )


def test_buyer_earn_is_suppressed_on_a_wholesale_sale():
    body = SRC[SRC.index("def _settle_order_points("):]
    body = body[:body.index("def ", 40)] if "def " in body[40:] else body
    assert "not _is_wholesale_order(order)" in body, (
        "buyer earn no longer checks for a wholesale sale"
    )


def test_the_full_price_rule_is_not_relied_on_for_this():
    """Documented so a future reader does not 'simplify' the wholesale check away on the
    grounds that the full-price rule already covers it. It does not."""
    start = SRC.index("_WHOLESALE_SOURCES = {")
    preamble = SRC[max(0, start - 1400):start]
    assert "full price" in preamble.lower()
    assert "never appears as a discount" in preamble
