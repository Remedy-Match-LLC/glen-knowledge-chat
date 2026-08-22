# tests/test_ladder_naming.py
"""The ladder has four stages and each is named the same way everywhere.

Journey review finding 3. Both halves of every stage name already existed and had
drifted apart:

  * the ribbon (static/shell-map.json, rendered by shell.js) used the BRANDS
  * the quest used the brands too
  * the "How it works" block used bare verbs, said "Three steps", and omitted Give
  * the journey cards used ad-hoc parentheticals -- "Your Biofield", "the root
    causes" -- of which only one was an actual stage name

A client cannot read a progress indicator whose stages they cannot name, so every
surface now shows the PAIR. Which half leads is a per-surface choice: the verb
leads where the ladder is being explained, the brand leads on the ribbon, where it
is wayfinding.

begin_funnel.LADDER is the single source. These tests fail if any surface drifts
from it again.
"""

import json
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
BEGIN = REPO / "static" / "begin.html"
SHELL_MAP = REPO / "static" / "shell-map.json"


def _ladder():
    import begin_funnel as bf
    return bf.LADDER, bf.LADDER_ORDER


# ---------------------------------------------------------------------------
# The canon
# ---------------------------------------------------------------------------

def test_the_ladder_has_exactly_four_stages_in_order():
    ladder, order = _ladder()
    assert order == ["scan", "find", "heal", "give"]
    assert set(ladder) == set(order)


def test_every_stage_carries_both_halves():
    ladder, _ = _ladder()
    for key, names in ladder.items():
        assert names["brand"].strip(), key
        assert names["verb"].strip(), key
        assert names["brand"] != names["verb"], key


def test_the_ribbon_map_agrees_with_the_canon():
    """static/shell-map.json feeds the ribbon. If it drifts, the ribbon and the
    journey cards start calling the same stage two different things -- which is
    exactly the state this finding was about."""
    ladder, order = _ladder()
    lands = json.loads(SHELL_MAP.read_text(encoding="utf-8"))["lands"]
    assert set(lands) == set(order), "the ribbon and the engine disagree on the STAGES"
    for key in order:
        assert lands[key]["name"] == ladder[key]["brand"], (
            f"{key}: ribbon says {lands[key]['name']!r}, canon says {ladder[key]['brand']!r}")


# ---------------------------------------------------------------------------
# The journey cards
# ---------------------------------------------------------------------------

def test_the_journey_cards_are_built_from_the_canon():
    import begin_funnel as bf
    cards = {c["key"]: c for c in bf.JOURNEY_STEPS}
    ladder, order = _ladder()
    assert list(cards) == order
    for key in order:
        assert cards[key]["label"] == ladder[key]["verb"]
        assert cards[key]["paren"] == ladder[key]["brand"]


@pytest.mark.parametrize("gone", ["Your Biofield", "the root causes", "lift others"])
def test_the_ad_hoc_parentheticals_are_gone(gone):
    import begin_funnel as bf
    assert gone not in {c["paren"] for c in bf.JOURNEY_STEPS}


def test_the_client_side_fallback_ladder_matches_the_server():
    """begin.html carries its own copy for the pre-state render. A fallback that
    disagrees with the server shows one set of names, then swaps to another."""
    import begin_funnel as bf
    html = BEGIN.read_text(encoding="utf-8")
    block = html[html.index("JOURNEY_FALLBACK"):][:1400]
    for c in bf.JOURNEY_STEPS:
        # The mark may be written literally or as a JS escape; both mean ™.
        forms = {c["paren"], c["paren"].replace("\u2122", "\\u2122")}
        assert any(f"paren:'{b}'" in block for b in forms), (
            f"fallback drifted for {c['key']}: expected one of {forms}")


def test_the_fallback_scan_link_uses_the_bridge():
    """It still pointed at the raw E4L signup, the one entry point #1382 missed."""
    html = BEGIN.read_text(encoding="utf-8")
    block = html[html.index("JOURNEY_FALLBACK"):][:1400]
    assert "truly.vip/E4L" not in block
    assert "/begin/scan" in block


# ---------------------------------------------------------------------------
# The how-it-works block
# ---------------------------------------------------------------------------

def test_how_it_works_no_longer_claims_three_steps():
    html = BEGIN.read_text(encoding="utf-8")
    assert "Three steps" not in html
    assert "Four stages" in html


def test_how_it_works_names_all_four_stages_with_both_halves():
    ladder, order = _ladder()
    html = BEGIN.read_text(encoding="utf-8")
    block = html[html.index('class="how-flow"'):html.index('class="how-foot"')]
    for key in order:
        verb, brand = ladder[key]["verb"], ladder[key]["brand"]
        assert f"<b>{verb}</b>" in block, f"{key}: verb missing from how-it-works"
        needle = brand.replace("™", "&trade;")
        assert needle in block, f"{key}: brand {brand!r} missing from how-it-works"


def test_give_has_a_motif_like_its_siblings():
    """Three cells with an animation and a fourth without reads as unfinished."""
    html = BEGIN.read_text(encoding="utf-8")
    assert 'id="m-give"' in html
    assert re.search(r"grab\('m-give'\)", html), "the Give motif is never drawn"


def test_the_flow_grid_has_room_for_four():
    html = BEGIN.read_text(encoding="utf-8")
    m = re.search(r"\.how-flow \{[^}]*grid-template-columns: ([^;]+);", html)
    assert m, ".how-flow grid rule not found"
    assert m.group(1).count("1fr") == 4, "a fourth stage would wrap or overflow"
