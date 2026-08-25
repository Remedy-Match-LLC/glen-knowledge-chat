import re
import sqlite3

import pytest

from dashboard import cart_block as CB
from dashboard import cart_store as CS


@pytest.fixture()
def cx(tmp_path):
    c = sqlite3.connect(str(tmp_path / "cart.db"))
    CS.init_cart_tables(c)
    yield c
    c.close()


def test_block_is_inert_when_disabled(cx):
    CS.get_or_create(cx, "mem1", email="a@x.com")
    CS.add_item(cx, "mem1", "brain-boost", qty=3)
    assert CB.build_block(cx, "a@x.com", False) == {"enabled": False}


def test_block_counts_the_open_cart(cx):
    CS.get_or_create(cx, "mem1", email="a@x.com")
    CS.add_item(cx, "mem1", "brain-boost", qty=3)
    CS.add_item(cx, "mem1", "wholomega", qty=2)
    assert CB.build_block(cx, "a@x.com", True) == {"enabled": True, "count": 5}


def test_block_is_zero_when_the_member_has_no_cart(cx):
    assert CB.build_block(cx, "nobody@x.com", True) == {"enabled": True, "count": 0}


def test_block_ignores_an_ordered_cart(cx):
    CS.get_or_create(cx, "mem1", email="a@x.com")
    CS.add_item(cx, "mem1", "brain-boost", qty=3)
    CS.mark_ordered(cx, "mem1", "ref1")
    assert CB.build_block(cx, "a@x.com", True) == {"enabled": True, "count": 0}


def test_block_ignores_a_cart_mid_checkout(cx):
    """A cart claimed for checkout (status='checking_out') is deliberately NOT
    open (see cart_store.claim_for_checkout / open_token_for_email's status
    filter), so it must not show as a live cart -- a regression here would
    show a customer a live cart mid-payment."""
    CS.get_or_create(cx, "mem1", email="a@x.com")
    CS.add_item(cx, "mem1", "brain-boost", qty=3)
    assert CS.claim_for_checkout(cx, "mem1") is True
    assert CB.build_block(cx, "a@x.com", True) == {"enabled": True, "count": 0}


def test_block_never_raises_into_the_payload(cx):
    """A portal payload must degrade, not 500, when a source fails.

    Creates a real open cart with items FIRST, so open_token_for_email
    resolves a non-empty token and the subsequent items() lookup actually
    hits the dropped table -- otherwise this would pass for the wrong
    reason (an email with no cart at all short-circuits to 0 without ever
    touching cart_items, never exercising the guard this test claims to
    cover)."""
    CS.get_or_create(cx, "mem1", email="a@x.com")
    CS.add_item(cx, "mem1", "brain-boost", qty=3)
    cx.execute("DROP TABLE cart_items")
    cx.commit()
    assert CB.build_block(cx, "a@x.com", True) == {"enabled": True, "count": 0}


def test_hub_tile_is_available_for_legacy_portals():
    """Legacy-token payloads can omit v.cart, but must still expose My Cart."""
    html = open("static/client-portal.html", encoding="utf-8").read()
    assert 'actTiles.push(["cart", "My Cart"' in html, (
        "the cart tile must remain available for legacy hub portals"
    )


def test_cart_panel_is_available_for_legacy_portals():
    """The cart target must exist when a legacy hub portal opens it."""
    html = open("static/client-portal.html", encoding="utf-8").read()
    pattern = re.compile(
        r'_hub\s*\?\s*'
        r'`<section data-panel="cart"',
    )
    assert pattern.search(html), (
        "the cart panel must remain available for legacy hub portals"
    )
