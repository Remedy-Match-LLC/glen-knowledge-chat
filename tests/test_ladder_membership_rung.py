# tests/test_ladder_membership_rung.py
"""The ascend ladder must show a rung between free and $300, and offer a way to
buy something without booking a call.

Journey review finding 9 -- and the review mis-named it. It is not a hole in the
OFFERS, it is a ladder that hid offers that already exist. "Choose your depth" read:

    Free -> Free -> $300 -> $3,600 -> $8,500 -> $14,000 -> $25,000 -> $50,000

while a $99/month membership (self-serve Stripe checkout, live) and the whole
formulation line ($20-$250) existed and appeared nowhere on it. A client who had
just bought a $70 formulation was shown a $300 consultation as their next step.

Second defect, same page: every paid tier's cta_label said "Book your
consultation", AND begin-ascend-tier.html hardcoded every CTA href to the intake
form regardless of the label. So there was no self-serve rung at any price -- a
client ready to spend had to schedule a call first.

Membership is rung 3 and carries its own cta_href. The $300 Biofield Analysis
deliberately keeps "Book your consultation": its only checkout is token-scoped to
buying remedies AFTER a reveal, so there is genuinely no self-serve path to the
analysis itself and claiming otherwise would be a lie in the UI.
"""

import pathlib
import re

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent
ASCEND = REPO / "static" / "begin-ascend.html"
TIER_PAGE = REPO / "static" / "begin-ascend-tier.html"


def _tiers():
    import begin_funnel as bf
    return bf.TIER_CATALOG


def test_membership_is_on_the_ladder():
    assert "membership" in _tiers()


def test_membership_sits_between_the_free_courses_and_the_300_analysis():
    t = _tiers()
    assert t["membership"]["n"] == 3, "free courses are rungs 1-2"
    assert t["biofield-analysis"]["n"] == 4, "the $300 tier must move up one"


def test_rung_numbers_are_unique_and_contiguous():
    ns = sorted(t["n"] for t in _tiers().values())
    assert len(ns) == len(set(ns)), f"duplicate rung numbers: {ns}"
    assert ns == list(range(ns[0], ns[0] + len(ns))), f"gap in the ladder: {ns}"


def test_membership_is_the_first_self_serve_rung():
    t = _tiers()["membership"]
    assert t["cta_href"] == "/membership"
    assert "consultation" not in t["cta_label"].lower()


def test_the_price_gap_below_the_analysis_is_closed():
    """The specific complaint: nothing between free and $300."""
    t = _tiers()
    assert "$99" in t["membership"]["price"]
    below = [x for x in t.values() if x["n"] < t["biofield-analysis"]["n"]]
    assert below, "still nothing between the free courses and $300"


# ---------------------------------------------------------------------------
# The $300 tier stays honest
# ---------------------------------------------------------------------------

def test_the_analysis_still_books_a_consultation():
    """There is no self-serve path to buy the analysis; the CTA must not pretend."""
    t = _tiers()["biofield-analysis"]
    assert t["cta_label"] == "Book your consultation"
    assert "cta_href" not in t


# ---------------------------------------------------------------------------
# The CTA actually goes where the label says
# ---------------------------------------------------------------------------

def test_the_tier_page_honours_a_per_tier_cta_href():
    """It used to hardcode truly.vip/Join for EVERY tier, so a self-serve rung
    could not exist however its label was worded."""
    src = TIER_PAGE.read_text(encoding="utf-8")
    assert "tier.cta_href" in src, "the tier page ignores cta_href again"
    i = src.index("var ctaHref")
    block = src[i:i + 900]
    assert "tier.cta_href" in block


def test_an_internal_cta_href_is_not_utm_threaded():
    """Same-origin needs no affiliate threading, and a stray ?utm on /membership
    would just be noise."""
    src = TIER_PAGE.read_text(encoding="utf-8")
    i = src.index("var ctaHref")
    block = src[i:i + 900]
    assert "charAt(0) === '/'" in block


# ---------------------------------------------------------------------------
# The visible card list
# ---------------------------------------------------------------------------

def test_the_ascend_page_shows_the_membership_card():
    html = ASCEND.read_text(encoding="utf-8")
    assert "Healing Oasis Membership" in html
    assert "$99/month" in html


def test_card_numbers_match_their_tiers_and_do_not_repeat():
    html = ASCEND.read_text(encoding="utf-8")
    tiers = re.findall(r'data-tier="(\d)"', html)
    nums = re.findall(r'<span class="card-num">(\d\d)</span>', html)
    assert len(tiers) == len(nums), (tiers, nums)
    assert tiers == sorted(tiers), f"cards out of order: {tiers}"
    assert len(tiers) == len(set(tiers)), f"duplicate data-tier: {tiers}"
    assert [int(n) for n in nums] == [int(t) for t in tiers], (nums, tiers)


def test_every_paid_tier_has_a_card():
    html = ASCEND.read_text(encoding="utf-8")
    for slug in _tiers():
        assert f"/begin/ascend/{slug}" in html, f"{slug} has no card on the ladder"
