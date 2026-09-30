"""Membership page wording, Glen 2026-09-30 (via marketing-12, from the video scripts):
"$200 for paid members" and "The free-member price is $300, and it includes one month of
membership." The funnel ladder's price line uses the same "paid members" wording.
"""
from pathlib import Path


def test_the_membership_page_uses_glens_wording():
    html = Path("static/membership-choose.html").read_text()
    assert "$200 for paid members" in html
    assert "The free-member price is $300, and it includes one month of membership." in html
    assert "active members" not in html
    assert "non-member price" not in html
    assert "Your Bioenergetic Wellness Scan is interpreted automatically" in html


def test_the_funnel_ladder_uses_paid_members():
    src = Path("begin_funnel.py").read_text()
    assert "$200 for paid members" in src
    assert "$200 for active members" not in src
