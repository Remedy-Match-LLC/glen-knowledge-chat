"""The chat's own rules never appear on an answer's Sources line.

Read-back of the Wellness Ensurance rule, 2 Nov 2026: three of four answers listed the
rule as a source despite the prompt. Glen, 2026-11-02, platform tab: "yes" to removing
them in code. The stream and the logged text must agree.
"""
import random

import pytest

from dashboard import rule_sources as rs

BODY = "Wellness Ensurance is no longer offered.\n\nWhat brought you here?\n\n---\n\n"

SEEN = [  # as the live chat wrote them on 2 Nov 2026
    "**Sources:** Wellness Ensurance retirement directive (2026-11-02)",
    "**Sources:** Wellness Ensurance program status (retired as of 2026-11-02).  ",
    "**Sources:** Wellness Ensurance retirement notice (2026-11-02).  ",
    "Sources: Standing CDS rule",
]


@pytest.mark.parametrize("line", SEEN)
def test_a_line_naming_only_a_rule_is_removed(line):
    assert rs.rewrite_text(BODY + line + "\n") == BODY


def test_real_sources_are_kept_and_only_the_rule_goes():
    text = BODY + ("**Sources:** Clinical Theory (terrain), Brain Cleanse product page, "
                   "Wellness Ensurance retirement directive (2026-11-02)\n")
    assert rs.rewrite_text(text) == BODY + ("**Sources:** Clinical Theory (terrain), "
                                            "Brain Cleanse product page\n")


def test_a_comma_inside_brackets_stays_with_its_entry():
    text = BODY + ("**Sources:** Wellness Ensurance retirement notice (2 Nov, platform), "
                   "Clinical Theory (terrain, liver)\n")
    assert rs.rewrite_text(text) == BODY + "**Sources:** Clinical Theory (terrain, liver)\n"


def test_a_line_with_no_rule_is_left_exactly_as_written():
    text = BODY + "**Sources:** Clinical Theory (terrain, liver; field), Q&A.\n"
    assert rs.rewrite_text(text) == text


def test_an_entry_that_names_a_rule_goes_even_if_real():
    """The trade-off, stated: a real source titled with these words is dropped too.
    A missing source name costs less than the chat citing its own rules."""
    text = BODY + "**Sources:** Clinical Theory (terrain), Rule of Thirds lecture\n"
    assert rs.rewrite_text(text) == BODY + "**Sources:** Clinical Theory (terrain)\n"


@pytest.mark.parametrize("prose", [
    "The rule of thumb is one capsule with food.\n",
    "Sources of magnesium include greens; a standing instruction is to rest.\n",
    "Understanding the notice period helps.\n",
])
def test_ordinary_text_is_never_touched(prose):
    assert rs.rewrite_text(prose) == prose


@pytest.mark.parametrize("line", SEEN + [
    "**Sources:** Clinical Theory (terrain), Wellness Ensurance retirement directive",
    "**Sources:** Clinical Theory (terrain)",
])
def test_the_stream_shows_what_the_log_keeps_however_it_is_split(line):
    text = BODY + line + "\n\nSources of iron: greens.\n"
    rng = random.Random(7)
    for _ in range(40):
        cuts = sorted(rng.sample(range(1, len(text)), rng.randint(1, 12)))
        parts = [text[a:b] for a, b in zip([0] + cuts, cuts + [len(text)])]
        assert "".join(rs.rewrite_stream(iter(parts))) == rs.rewrite_text(text)


def test_an_unfinished_last_line_is_cleaned_too():
    text = BODY + "**Sources:** Wellness Ensurance retirement notice"
    assert "".join(rs.rewrite_stream(iter([text[:60], text[60:]]))) == BODY


def test_only_a_possible_sources_line_is_held_back():
    """The answer still streams: an ordinary line goes out before its newline."""
    out = rs.rewrite_stream(iter(["Wellness ", "Ensurance is", " no longer"]))
    assert next(out) == "Wellness "


def test_every_chat_route_runs_it():
    """Every chat route streams through _store_links_stream and logs through
    _store_links_text (test_legacy_store_links covers that wiring)."""
    import importlib
    app = importlib.import_module("app")
    line = "**Sources:** Wellness Ensurance retirement directive (2026-11-02)\n"
    assert app._store_links_text(BODY + line) == BODY
    assert "".join(app._store_links_stream(iter([BODY, line]))) == BODY
