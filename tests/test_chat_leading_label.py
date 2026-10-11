"""A chat answer must never open with a bracketed internal label.

Seen live: "[ESTRO-CLEAR PRODUCT ANSWER]" (10-10, made up by the model) and
"[EXECUTIVE SUMMARY — ≈165 words]" (echoing the prompt's format name). The
filter drops a first line that is only a bracketed label, in the stream and in
the logged text alike."""
import pytest

from dashboard.chat_cta import strip_leading_label, strip_leading_label_text


def _stream(text, size):
    return "".join(strip_leading_label(text[i:i + size] for i in range(0, len(text), size)))


LEAKS = [
    "[ESTRO-CLEAR PRODUCT ANSWER]\n\nEstro-Clear contains DIM.",
    "[EXECUTIVE SUMMARY — ≈165 words]\nEstro-Clear contains DIM.",
    "**[ESTRO-CLEAR PRODUCT ANSWER]**\n\nEstro-Clear contains DIM.",
    "## [Product answer]\n\nEstro-Clear contains DIM.",
    "  [ESTRO-CLEAR PRODUCT ANSWER]  \r\n\r\nEstro-Clear contains DIM.",
    "\n\n[ESTRO-CLEAR PRODUCT ANSWER]\nEstro-Clear contains DIM.",
    "*[EXECUTIVE SUMMARY]*\nEstro-Clear contains DIM.",
    "[EXECUTIVE SUMMARY]:\nEstro-Clear contains DIM.",
    " " * 30 + "[" + "A" * 95 + "]\nEstro-Clear contains DIM.",
]

KEEP = [
    "Estro-Clear contains DIM.",
    "[Estro-Clear](https://myhealingoasis.com/p/estro-clear) contains DIM.",
    "[Estro-Clear](https://x.y)\n\nIt contains DIM.",
    "Estro-Clear contains DIM.\n\n[NOTE]\nmore",
    "[Estro-Clear] contains DIM.",
    "",
    "\nEstro-Clear contains DIM.",
    "#" + " " * 200 + "[A]\nbody",
]


@pytest.mark.parametrize("text", LEAKS)
@pytest.mark.parametrize("size", [1, 3, 7, 1000])
def test_label_line_is_dropped_from_stream(text, size):
    assert _stream(text, size) == "Estro-Clear contains DIM."


@pytest.mark.parametrize("text", LEAKS)
def test_label_line_is_dropped_from_text(text):
    assert strip_leading_label_text(text) == "Estro-Clear contains DIM."


@pytest.mark.parametrize("text", KEEP)
@pytest.mark.parametrize("size", [1, 3, 7, 1000])
def test_ordinary_answers_pass_through_unchanged(text, size):
    assert _stream(text, size) == text
    assert strip_leading_label_text(text) == text


def test_label_only_answer_with_no_newline_is_dropped():
    assert _stream("[ESTRO-CLEAR PRODUCT ANSWER]", 4) == ""
    assert strip_leading_label_text("[ESTRO-CLEAR PRODUCT ANSWER]") == ""


def test_long_first_line_is_not_held_back():
    # A first line longer than any label streams before its newline arrives.
    text = "[" + "x" * 300
    out = list(strip_leading_label(iter([text[:150], text[150:]])))
    assert out[0] == text[:150]
    assert "".join(out) == text


def test_stream_drains_the_input():
    seen = []

    def toks():
        for t in ["[LABEL]\n", "Body", " ⟦CTA⟧ page | x | y"]:
            seen.append(t)
            yield t

    "".join(strip_leading_label(toks()))
    assert len(seen) == 3


def test_both_chat_streams_are_wrapped():
    # Wiring: the /chat and match streams pass through the filter, and the
    # logged text is cleaned the same way.
    src = open("app.py", encoding="utf-8").read()
    assert "_store_links_stream(stream_visible(strip_leading_label(_toks())))" in src
    assert "stream_visible(strip_leading_label(_toks()), sentinel=CHIPS_SENTINEL)" in src
    assert src.count('strip_leading_label_text("".join(full_answer))') == 1
    assert src.count('strip_leading_label_text("".join(full))') == 1


def test_body_indentation_is_kept():
    text = "[LABEL]\n\n    first\n    second"
    assert strip_leading_label_text(text) == "    first\n    second"
    assert _stream(text, 1) == "    first\n    second"


def test_stream_matches_text_for_every_split():
    import random
    rng = random.Random(7)
    cases = LEAKS + KEEP + [
        "[LABEL]\n   \n\n⟦CTA⟧ page | x | y",
        "[LABEL]\n" + " " * 200 + "\nbody",
        "[" + "a" * 101 + "]\nbody",
        "   ",
    ]
    for text in cases:
        for _ in range(200):
            cuts, i = [], 0
            while i < len(text):
                n = rng.randint(0, 6)
                cuts.append(text[i:i + n]); i += n
            assert "".join(strip_leading_label(cuts)) == strip_leading_label_text(text), repr(text)
