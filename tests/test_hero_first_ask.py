# tests/test_hero_first_ask.py
"""The hero's first question, and what it does with the answer.

The headline says "Start by telling me what's on your mind." The scripted first
chat message used to say "what should I call you?" -- contradicting it in the same
viewport -- and then ran cleanName() over whatever came back and stored the result
as the client's first name, unguarded.

Measured against the live CRM on 2026-08-20: six real GHL contacts have a funnel
chip label as their first name. Four are called "Sharper vision" and receive the
sequences as "Hi Sharper vision,". One is "Something else". One is "on a", from
tapping "I'm on a mission". The sequences greet with {{contact.first_name}}, and
journey_state.first_name is pushed to GHL on the free-tier transition, so this was
not cosmetic.

Two independent fixes, because they cover different inputs:
  * the hero only treats a first message as a name when isLikelyName passes, so a
    TYPED sentence gets answered instead of swallowed;
  * scrub_first_name refuses our OWN chip labels at the writer, exactly, so no
    caller can reintroduce the tapped-chip case.
"""

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
BEGIN = (REPO / "static" / "begin.html")


# ---------------------------------------------------------------------------
# The question itself
# ---------------------------------------------------------------------------

def test_the_greeting_asks_for_the_outcome_not_the_name():
    html = BEGIN.read_text(encoding="utf-8")
    m = re.search(r'var GREETING = (.+?);\n', html, re.S)
    assert m, "GREETING constant not found"
    greeting = m.group(1).lower()
    assert "what should i call you" not in greeting
    assert "love to change" in greeting


def test_the_greeting_matches_the_engines_own_mission_prompt():
    """begin_funnel.MISSION_PROMPT is what the chips ask. If the scripted opener
    drifts from it the client gets asked two different opening questions."""
    import begin_funnel as bf
    html = BEGIN.read_text(encoding="utf-8")
    stem = bf.MISSION_PROMPT.rstrip("?").lower().replace("what would you", "what you would")
    assert stem in re.search(r'var GREETING = (.+?);\n', html, re.S).group(1).lower()


def test_a_first_message_is_only_a_name_when_it_looks_like_one():
    """The reordering itself: cleanName's result must pass isLikelyName before it
    is stored, and anything else falls through to be answered."""
    html = BEGIN.read_text(encoding="utf-8")
    i = html.index("if (!nameCaptured) {")
    block = html[i:i + 1600]
    assert "isLikelyName(nm)" in block, "name capture is unguarded again"
    guard = block.index("isLikelyName(nm)")
    unlock = block.index("unlock('name'")
    assert guard < unlock, "the server write must sit INSIDE the guard"


# ---------------------------------------------------------------------------
# The writer-side guard
# ---------------------------------------------------------------------------

def _chips():
    import begin_funnel as bf
    return (list(bf.SEED_OUTCOME_CHIPS)
            + [c["label"] for c in bf._STYLE_FORK_CHIPS]
            + [bf._CROSSOVER_TO_MISSION["label"], bf._CROSSOVER_TO_ADVENTURE["label"]])


@pytest.mark.parametrize("label", [
    "Sharper vision", "More energy", "Deeper sleep", "Calm", "Something else",
    "I'm on a mission", "I'm here to explore",
])
def test_no_chip_label_can_be_stored_as_a_name(label):
    import begin_funnel as bf
    assert bf.scrub_first_name(label) == ""


def test_every_chip_the_funnel_emits_is_refused():
    """Parametrised cases above are illustrative; this covers the real list, so a
    NEW chip cannot quietly become storable."""
    import begin_funnel as bf
    leaked = [c for c in _chips() if bf.scrub_first_name(c) != ""]
    assert leaked == [], leaked


@pytest.mark.parametrize("fragment,chip", [
    ("on a", "I'm on a mission"),
    ("here to", "I'm here to explore"),
    ("Just tell", "Just tell me where to start"),
    ("Actually let", "Actually, let me explore instead"),
])
def test_the_cleanname_fragment_of_a_chip_is_refused_too(fragment, chip):
    """cleanName keeps the first two words AFTER stripping a lead-in, so the stored
    value is rarely the label itself. 'on a' is a real row in the live CRM."""
    import begin_funnel as bf
    assert bf.scrub_first_name(fragment) == "", f"{fragment!r} came from {chip!r}"


@pytest.mark.parametrize("name", ["Glen", "Mary Alice", "Karla M.", "Dr Carol", "O'Neill"])
def test_real_names_are_never_refused(name):
    """The guard is set membership over strings we emit, not a heuristic. Guessing
    at free text is what caused the damage; a false positive here deletes a real
    client's name."""
    import begin_funnel as bf
    assert bf.scrub_first_name(name) == name


def test_the_guard_runs_on_the_write_path():
    import sqlite3
    import begin_funnel as bf
    cx = sqlite3.connect(":memory:")
    bf.init_journey_tables(cx)
    st = bf.record_unlock(cx, session_id="s1", trigger="name",
                          first_name="Sharper vision")
    assert st["first_name"] == ""
    st = bf.record_unlock(cx, session_id="s2", trigger="name", first_name="Glen")
    assert st["first_name"] == "Glen"


def test_a_refused_name_never_overwrites_a_good_one():
    """Ordering matters: someone who gave a real name and later taps a chip must
    keep the real name."""
    import sqlite3
    import begin_funnel as bf
    cx = sqlite3.connect(":memory:")
    bf.init_journey_tables(cx)
    bf.record_unlock(cx, session_id="s3", trigger="name", first_name="Glen")
    st = bf.record_unlock(cx, session_id="s3", trigger="question",
                          first_name="Sharper vision")
    assert st["first_name"] == "Glen"
