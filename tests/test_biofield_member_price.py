# tests/test_biofield_member_price.py
"""Biofield Analysis is $200 for a FULL member, $300 for everyone else.

Glen 2026-08-27: the $100 difference IS the included month of member access. A
full member has already paid for that month, so they pay $200 and receive no
additional time; everyone else pays $300 and the month comes with it.

The trap this file exists to pin: `program_member` is set true merely by a
biofield line being PRESENT in the cart (that flag exists so the accompanying
remedies quote at member rates). Pricing the member rate off it would be
self-fulfilling -- any non-member adding Biofield would flip themselves to $200
and collect the month for free. The price must key off _is_paid_member, which
also excludes the $1-trial cohort, which is what "full member" means.
"""
import json
import pathlib

import pytest


def _app():
    import importlib, sys
    repo = pathlib.Path(__file__).resolve().parent.parent
    if str(repo) not in sys.path:
        sys.path.insert(0, str(repo))
    try:
        return importlib.import_module("app")
    except Exception as e:  # pragma: no cover
        pytest.skip(f"app not importable: {e}")


# ---------------------------------------------------------------------------
# The catalog
# ---------------------------------------------------------------------------

def test_the_sku_declares_both_prices():
    repo = pathlib.Path(__file__).resolve().parent.parent
    d = json.loads((repo / "data" / "products.json").read_text())
    p = d["products"]["biofield-analysis"]
    assert p["price_cents"] == 30000
    assert p["member_price_cents"] == 20000, "the full-member price is the fix"


# ---------------------------------------------------------------------------
# The resolver
# ---------------------------------------------------------------------------

def test_a_full_member_gets_the_member_price(monkeypatch):
    app = _app()
    monkeypatch.setattr(app, "_is_paid_member", lambda e: True)
    p = app._get_product("biofield-analysis")
    assert app._member_price_cents(p, "member@example.com") == 20000
    assert app._member_priced_product(p, "member@example.com")["price_cents"] == 20000


def test_a_non_member_pays_full_price(monkeypatch):
    app = _app()
    monkeypatch.setattr(app, "_is_paid_member", lambda e: False)
    p = app._get_product("biofield-analysis")
    assert app._member_price_cents(p, "nobody@example.com") is None
    assert app._member_priced_product(p, "nobody@example.com")["price_cents"] == 30000


def test_no_email_pays_full_price(monkeypatch):
    app = _app()
    monkeypatch.setattr(app, "_is_paid_member", lambda e: True)
    p = app._get_product("biofield-analysis")
    assert app._member_price_cents(p, "") is None
    assert app._member_price_cents(p, None) is None


def test_a_sku_without_a_member_price_is_untouched(monkeypatch):
    """Every other product must price exactly as before."""
    app = _app()
    monkeypatch.setattr(app, "_is_paid_member", lambda e: True)
    p = dict(app._get_product("terrain-restore") or {"price_cents": 6997})
    p.pop("member_price_cents", None)
    assert app._member_price_cents(p, "member@example.com") is None
    assert app._member_priced_product(p, "member@example.com") is p, "must return the same object"


def test_the_catalog_dict_is_never_mutated(monkeypatch):
    """NOTE: this assertion cannot currently fail, and that is deliberate.

    `_get_product` already returns `dict(p)` -- a fresh copy per call -- so the
    `dict(p)` inside `_member_priced_product` is a SECOND mechanism guarding the
    same case. Replacing it with an in-place mutation leaves every test green.
    Both are kept: _get_product's copy is the one that actually protects the
    catalog today, and the local copy keeps the helper correct for any caller
    that hands it a dict from somewhere else. Written down because a mutation
    test here comes back "still green" and looks like a sleeping guard."""
    app = _app()
    monkeypatch.setattr(app, "_is_paid_member", lambda e: True)
    p = app._get_product("biofield-analysis")
    app._member_priced_product(p, "member@example.com")
    assert app._get_product("biofield-analysis")["price_cents"] == 30000


# ---------------------------------------------------------------------------
# THE TRAP
# ---------------------------------------------------------------------------

def test_the_member_price_does_not_key_off_program_member(monkeypatch):
    """program_member goes true just because a biofield line is in the cart. If
    the $200 keyed off that, a non-member would price themselves down to $200 by
    adding the very product whose $100 premium buys their month."""
    app = _app()
    monkeypatch.setattr(app, "_is_paid_member", lambda e: False)
    p = app._get_product("biofield-analysis")
    # program_member=True is exactly the state a biofield line induces
    assert app._inhouse_line_unit_cents(
        p, None, 0, app._pricing_settings() if hasattr(app, "_pricing_settings") else {},
        program_member=True) != 20000, "a non-member reached the member price"


def test_every_server_side_pricing_path_applies_it():
    """Four paths quote this SKU: portal checkout, the cart, the in-house
    invoice, and the price preview. A path that misses the swap means display
    and charge disagree -- the failure mode this codebase keeps hitting."""
    import ast
    repo = pathlib.Path(__file__).resolve().parent.parent
    tree = ast.parse((repo / "app.py").read_text())
    callers = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        for sub in ast.walk(node):
            if (isinstance(sub, ast.Call) and isinstance(sub.func, ast.Name)
                    and sub.func.id == "_member_priced_product"):
                callers.add(node.name)
    for fn in ("_portal_priced_lines", "_price_cart", "_price_inhouse_invoice",
               "api_orders_price_preview"):
        assert fn in callers, f"{fn} does not apply the member price"
