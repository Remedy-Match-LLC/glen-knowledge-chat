"""The Biofield reveal page must never end in a dead end.

The $1 lifetime unlock was retired and BIOFIELD_TRIAL_ENABLED switched off. The
CTA had no other branch, so it fell back to a disabled button reading "Unlock
your full Biofield Analysis" with the note "(unlocking soon)". Every visitor who
reached a reveal saw a promise that nothing was going to keep, and had no way
forward. That sat live for months.

Membership is the live door, so the page offers it when it is enabled, and says
something true when it is not.
"""
from pathlib import Path

PAGE = (Path(__file__).resolve().parents[1] / "static" / "begin-biofield.html").read_text(encoding="utf-8")


def test_the_unlocking_soon_dead_end_is_gone():
    assert "(unlocking soon)" not in PAGE
    assert "cta-btn-disabled" not in PAGE


def test_membership_is_offered_when_it_is_enabled():
    assert "data.membership_enabled" in PAGE
    assert 'memberLink.href = "/membership"' in PAGE
    assert "See all your matches" in PAGE


def test_the_retired_dollar_offer_still_works_if_it_is_ever_switched_back_on():
    """The $1 branch is untouched. Turning BIOFIELD_TRIAL_ENABLED back on must
    restore the old behaviour rather than land on the membership fallback."""
    assert "if (data.trial_enabled) {" in PAGE
    assert "Unlock lifetime access ($1)" in PAGE
    assert PAGE.index("if (data.trial_enabled) {") < PAGE.index("data.membership_enabled")


def test_the_last_resort_makes_no_promise():
    """With nothing on sale the page must not imply that something is coming."""
    tail = PAGE[PAGE.index("data.membership_enabled"):]
    assert "soon" not in tail.split("root.appendChild(ctaSection)")[0]
    assert "Your remaining matches are held for now" in PAGE


def test_the_server_sends_the_membership_flag():
    app_src = (Path(__file__).resolve().parents[1] / "app.py").read_text(encoding="utf-8")
    # Both reveal payloads, the paid branch and the blurred branch.
    assert app_src.count('"membership_enabled": MEMBERSHIP_PRODUCTS_ENABLED,') == 2
