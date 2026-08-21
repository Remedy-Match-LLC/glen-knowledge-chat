# tests/test_doorway_rail.py
"""The "Where to begin" rail must always offer a way in that costs nothing.

`surface()` fell back to the default trio only when the match set was EMPTY, so a
single match rendered a single card -- under a header reading "Choose the doorway
that meets you where you are", in a numbered list, as a lone "01".

Measured across 675 simulated visitor states on 2026-08-20: 44% rendered one card,
and 228 of those showed a paid or ladder door as the only option. The sharpest case
is the most engaged non-buyer in the funnel -- video watched, question asked, name,
email, ToS and a scan on file -- who types "hello there" and is offered nothing but
The Path Deeper, the $300 to $50,000 ladder.

Fix: matched cards keep their order, then pad from _DEFAULT_TRIO up to the cap. Only
when something matched -- a no-signal state already had a deliberate fallback (the
trio for the rail, one gentle quiz card in chat) and those are untouched.
"""

import pytest

FREE_DOORS = {"quiz", "e4l_scan", "intake"}
LADDER = {"ash_masterclass", "ash_course", "product", "founding_offer"}


def _keys(cards):
    return [c["key"] for c in cards]


def _state(aw="unknown", rung="arrival", gates=()):
    return {"awareness_stage": aw, "current_rung": rung, "unlocked_gates": list(gates)}


# ---------------------------------------------------------------------------
# The rail
# ---------------------------------------------------------------------------

def test_the_engaged_visitor_is_no_longer_shown_only_the_ladder():
    """The case that motivated this: every gate open, generic greeting."""
    import begin_funnel as bf
    st = _state(gates=["video", "question", "name", "email", "tos", "scan"])
    keys = _keys(bf.surface(st, ["hello there"], ""))
    assert keys[0] == "ash_masterclass", "relevance ranking must survive"
    assert len(keys) == 3
    assert FREE_DOORS & set(keys), "no free way in"


def test_the_rail_is_never_a_single_doorway():
    """Exhaustive over the states the funnel can actually be in."""
    from itertools import product
    import begin_funnel as bf
    texts = ["hello there", "i cannot sleep", "does EVOX help", "anyone near me?",
             "tell me about the course", "i want to refer a friend", ""]
    lonely = []
    for t, rung, aw, gates in product(
            texts,
            ["arrival", "inquire", "free_tier", "assess", "ascend"],
            ["unknown", "problem", "solution", "product", "most"],
            [[], ["video", "question"],
             ["video", "question", "name", "email", "tos", "scan"]]):
        cards = bf.surface(_state(aw, rung, gates), [t], "")
        if len(cards) < 2:
            lonely.append((t, rung, aw, len(gates), _keys(cards)))
    assert lonely == [], lonely[:5]


def test_a_paid_door_is_never_the_only_door():
    from itertools import product
    import begin_funnel as bf
    alone = []
    for t, gates in product(["hello there", "tell me about the course", "does EVOX help"],
                            [[], ["video", "question", "name", "email", "tos", "scan"]]):
        keys = _keys(bf.surface(_state(gates=gates), [t], ""))
        if set(keys) and set(keys) <= LADDER:
            alone.append((t, keys))
    assert alone == [], alone


def test_padding_never_reorders_or_duplicates():
    import begin_funnel as bf
    keys = _keys(bf.surface(_state(rung="inquire"), ["anyone near me?"], ""))
    assert keys[0] == "practitioner", "the match must stay first"
    assert len(keys) == len(set(keys)), "a padded key duplicated a matched one"


def test_padding_does_not_displace_a_second_real_match():
    """Two matches plus one pad, not one match plus two pads."""
    import begin_funnel as bf
    keys = _keys(bf.surface(_state(rung="inquire"),
                            ["i want a remedy for my eyes"], ""))
    assert len(keys) == 3
    assert "remedy_match" in keys and "product" in keys


def test_the_no_signal_fallback_is_untouched():
    import begin_funnel as bf
    assert _keys(bf.surface(_state(), ["hello there"], "")) == ["quiz", "e4l_scan", "intake"]


def test_still_capped_at_three():
    import begin_funnel as bf
    st = _state(aw="most", rung="assess")
    cards = bf.surface(st, ["dentist near me, EVOX voice, learn a course, refer a friend"], "")
    assert len(cards) == 3


# ---------------------------------------------------------------------------
# The chat variant
# ---------------------------------------------------------------------------

def test_chat_pads_a_lone_match_to_two():
    import begin_funnel as bf
    st = _state(gates=["video", "question", "name", "email", "tos", "scan"])
    keys = _keys(bf.surface_for_chat(st, ["hello there"], ""))
    assert keys[0] == "ash_masterclass"
    assert len(keys) == 2
    assert FREE_DOORS & set(keys)


def test_chat_keeps_its_single_gentle_card_when_nothing_matched():
    """A no-signal chat turn deliberately shows ONE gentle card. Padding there
    would make the conversation pushier, which is a different question from a
    lone expensive door."""
    import begin_funnel as bf
    assert _keys(bf.surface_for_chat({}, ["hello, how are you"], "")) == ["quiz"]


def test_chat_still_capped_at_two():
    import begin_funnel as bf
    q = ["I want a remedy for my eyes, find a practitioner near me, voice frequency scan"]
    keys = _keys(bf.surface_for_chat({}, q, ""))
    assert len(keys) == 2 and keys[0] == "practitioner"


# ---------------------------------------------------------------------------
# The founding-offer variant rides on surface()
# ---------------------------------------------------------------------------

def test_founding_offer_still_leads_and_still_caps():
    import begin_funnel as bf
    st = _state(aw="product", rung="assess", gates=["quiz"])
    cards = bf.surface_with_founding(st, ["neuro magnesium"], ref="", founding_open=True)
    assert cards[0]["key"] == "founding_offer"
    assert len(cards) == 3
