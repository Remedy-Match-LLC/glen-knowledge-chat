"""Certification students show as paid members through 2026 (Glen, 2026-10-03).

Agnes Verches is in Certification training, so her Biofield Analysis should be $200,
not $300. Glen: "every one should show as a paid member through the end of the year."
"""
import importlib

import pytest


@pytest.fixture
def a(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    import app as mod
    importlib.reload(mod)
    mod._CERT_CACHE.clear()
    monkeypatch.setattr(mod, "_active_membership_for_email", lambda e: None)
    monkeypatch.setattr(mod, "_family_plan_enabled", lambda: False)
    monkeypatch.setattr(mod, "_canonical_email", lambda e: (e or "").strip().lower())
    return mod


def test_a_certification_student_is_a_paid_member(a, monkeypatch):
    monkeypatch.setattr(a, "_is_certification_student", lambda e: e == "agnes@example.com")
    monkeypatch.setattr(a, "_cert_member_window_open", lambda today=None: True)
    assert a._is_paid_member("agnes@example.com")
    assert not a._is_paid_member("someone@example.com")


def test_the_window_closes_after_december_31(a, monkeypatch):
    assert a._cert_member_window_open("2026-12-31")
    assert not a._cert_member_window_open("2027-01-01")
    monkeypatch.setattr(a, "_is_certification_student", lambda e: True)
    monkeypatch.setattr(a, "_cert_member_window_open", lambda today=None: False)
    assert not a._is_paid_member("agnes@example.com")


def test_she_gets_the_member_biofield_price(a, monkeypatch):
    monkeypatch.setattr(a, "_is_certification_student", lambda e: e == "agnes@example.com")
    monkeypatch.setattr(a, "_cert_member_window_open", lambda today=None: True)
    p = {"price_cents": 30000, "member_price_cents": 20000}
    assert a._member_price_cents(p, "agnes@example.com") == 20000
    assert a._member_price_cents(p, "someone@example.com") is None


def test_the_lookup_is_cached(a, monkeypatch):
    calls = []
    monkeypatch.setattr(a, "_is_certification_student", lambda e: calls.append(e) or True)
    monkeypatch.setattr(a, "_cert_member_window_open", lambda today=None: True)
    for _ in range(5):
        assert a._is_paid_member("agnes@example.com")
    assert calls == ["agnes@example.com"]


def test_a_failed_lookup_fails_closed(a, monkeypatch):
    monkeypatch.setattr(a, "_is_certification_student", lambda e: False)
    monkeypatch.setattr(a, "_cert_member_window_open", lambda today=None: True)
    assert not a._is_paid_member("agnes@example.com")


def test_she_gets_mix_and_match_pricing_on_ffs(a, monkeypatch):
    """Glen: "That should give her mix/match quantity pricing on FFs as well." Two
    DIFFERENT FFs, one each: a member is priced on the order-wide FF count, a
    non-member on each SKU's own quantity."""
    monkeypatch.setattr(a, "_is_certification_student", lambda e: e == "agnes@example.com")
    monkeypatch.setattr(a, "_cert_member_window_open", lambda today=None: True)
    P = a._PRODUCTS["products"]
    ffs = [s for s, p in P.items() if isinstance(p, dict) and p.get("qty_pricing")
           and not p.get("inactive") and not p.get("info_only")
           and int(p.get("price_cents") or 0) == 6997][:3]
    assert len(ffs) == 3
    cart = [{"slug": s, "qty": 1} for s in ffs]

    def subtotal(email):
        ship = {"country": "US", "zip": "01950", "state": "MA", "city": "X", "street": "1 A St"}
        pc = a._price_cart(cart, ship=ship, program_member=a._is_paid_member(email),
                           email=email)
        return pc["priced"]["subtotal_cents"]

    assert subtotal("agnes@example.com") < subtotal("someone@example.com")
