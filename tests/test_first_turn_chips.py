# tests/test_first_turn_chips.py
"""The chips on the first turn must answer the question on the first turn.

Journey review finding 5. A cold visitor was asked two different things in one
viewport: the greeting asked "Tell me what you would love to change", and the chip
block directly beneath it asked "Are you here on a mission ... or an adventure?"
with two style chips. The chips answered the second question; the first, which the
headline sets up, was left hanging.

Now the root turn shows the SEED OUTCOME CHIPS, which answer the greeting, and the
travel style is inferred rather than asked: someone who names a concrete outcome is
on a mission by definition. `unknown` therefore behaves as `mission`, and the
standing `Actually, let me explore instead` secondary is how a client crosses over,
which is the escape hatch the design already required everywhere else.

Tapping a seed chip submits its LABEL as the message. That is what wrote "Sharper
vision" into the CRM as a client's first name, so this is only safe alongside the
finding-4 reorder and scrub_first_name -- covered by test_hero_first_ask.py, and
guarded again here.
"""

import pytest


def _state(travel_style="unknown", rung="arrival", gates=(), aw="unknown"):
    return {"travel_style": travel_style, "current_rung": rung,
            "unlocked_gates": list(gates), "awareness_stage": aw}


def _labels(chips):
    return [c["label"] for c in chips]


# ---------------------------------------------------------------------------
# The first turn
# ---------------------------------------------------------------------------

def test_the_root_turn_asks_only_one_question():
    """The greeting IS the question. A second prompt here stacks two asks."""
    import begin_funnel as bf
    assert bf.next_step_prompt(_state()) == ""


def test_the_root_chips_answer_the_greeting():
    import begin_funnel as bf
    chips = bf.next_step_chips(_state())
    primary = [c for c in chips if c["role"] == "primary"]
    assert _labels(primary) == list(bf.SEED_OUTCOME_CHIPS)


def test_the_style_fork_question_is_no_longer_asked():
    import begin_funnel as bf
    st = _state()
    assert bf.next_step_prompt(st) != bf.OPENING_PROMPT
    assert not [c for c in bf.next_step_chips(st) if c.get("action") == "style"
                and c["role"] == "primary"]


def test_the_root_turn_still_has_exactly_one_escape_hatch():
    """The opening fork used to be the deliberate 2-primary / 0-secondary
    exception to the design's own rule. It is no longer an exception."""
    import begin_funnel as bf
    chips = bf.next_step_chips(_state())
    secondary = [c for c in chips if c["role"] == "secondary"]
    assert len(secondary) == 1
    assert secondary[0]["action"] == "style"
    assert secondary[0]["value"] == "adventure"


def test_seed_chips_submit_their_label_as_text():
    """They are answers to a question, not navigation."""
    import begin_funnel as bf
    for c in bf.next_step_chips(_state()):
        if c["role"] == "primary":
            assert c["action"] == "text"
            assert not c["href"]


# ---------------------------------------------------------------------------
# The style is inferred, not asked
# ---------------------------------------------------------------------------

def test_unknown_style_behaves_as_mission():
    import begin_funnel as bf
    unknown = bf.next_step_chips(_state(travel_style="unknown"))
    mission = bf.next_step_chips(_state(travel_style="mission"))
    assert _labels(unknown) == _labels(mission)


def test_a_named_outcome_resolves_to_a_single_forward_chip():
    """Once they answer, the root turn stops offering seeds and points onward."""
    import begin_funnel as bf
    chips = bf.next_step_chips(_state(), query_texts=["what helps with my insomnia"])
    primary = [c for c in chips if c["role"] == "primary"]
    assert len(primary) == 1
    assert primary[0]["action"] == "link"
    assert primary[0]["href"]


@pytest.mark.parametrize("chip", ["Sharper vision", "More energy", "Deeper sleep",
                                  "Calm", "Something else"])
def test_no_seed_chip_is_a_dead_end(chip):
    """Every one of the five used to match NOTHING, so tapping one returned the
    same five chips. The safety net looped instead of moving anyone forward."""
    import begin_funnel as bf
    assert bf._match_card_keys(_state(), [chip]), f"{chip!r} leads nowhere"


def test_tapping_a_seed_chip_hands_back_a_forward_chip():
    """End to end: the label becomes the message, and the next turn moves on."""
    import begin_funnel as bf
    for chip in bf.SEED_OUTCOME_CHIPS:
        nxt = bf.next_step_chips(_state(), query_texts=[chip])
        primary = [c for c in nxt if c["role"] == "primary"]
        assert len(primary) == 1 and primary[0]["href"], f"{chip!r} looped"


def test_the_cues_are_derived_from_the_chips_not_duplicated():
    """A new chip must wire itself up; a hand-kept list would drift."""
    import begin_funnel as bf
    assert set(bf._outcome_cues()) == {c.lower() for c in bf.SEED_OUTCOME_CHIPS}


def test_choosing_adventure_still_works():
    import begin_funnel as bf
    chips = bf.next_step_chips(_state(travel_style="adventure"))
    labels = _labels(chips)
    assert "Explore my biofield" in labels
    assert len([c for c in chips if c["role"] == "secondary"]) == 1


def test_the_crossover_can_still_reach_adventure_from_the_root():
    """The fork survives; it is just not a question any more."""
    import begin_funnel as bf
    sec = [c for c in bf.next_step_chips(_state()) if c["role"] == "secondary"][0]
    after = bf.next_step_chips(_state(travel_style=sec["value"]))
    assert "Explore my biofield" in _labels(after)


# ---------------------------------------------------------------------------
# A tapped chip must never become the client's name
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("i", range(5))
def test_no_root_chip_label_could_be_stored_as_a_name(i):
    import begin_funnel as bf
    chips = [c for c in bf.next_step_chips(_state()) if c["role"] == "primary"]
    label = chips[i]["label"]
    assert bf.scrub_first_name(label) == "", f"{label!r} could be stored as a name"
