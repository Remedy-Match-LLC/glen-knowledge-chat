"""A clinical-qa note holding wording Glen approved reaches the model with a copy rule.

Found 2026-10-16: the Wylie cataract note was retrieved for all 6 live questions, and the
model rewrote it each time. It merged the two checkpoints into one figure, added
"reverse", and described the results in words the note does not use. Listed or flagged
notes now carry an [APPROVED WORDING] block that repeats the approved passage, and each
chat path closes its request with a reminder when such a block is present.
"""
import ast
import inspect

import app

WYLIE_ID = "qa-wylie-cataract-results-2026-10"
QUOTE = ("He puts his cataract patients on a six-month program, with a first checkup at "
         "three months. About half of them show improvement, often by that first checkup. "
         "Most of the rest are more stable than they were before.")
WYLIE_TEXT = (
    "QUESTION: What results has Dr. Todd Wylie seen with his cataract patients?\n\n"
    "ANSWER: Dr. Todd Wylie, OD, FCOVD, is an optometrist who has referred patients to "
    f'Healing Oasis. His results: "{QUOTE}"\n\nGive these results in full, as written here. '
    "Do not shorten them to one figure.")
OPEN, CLOSE = "“", "”"


class _M:
    def __init__(self, id, meta, score=0.5):
        self.id, self.metadata, self.score = id, meta, score


def _qa(text, **extra):
    return {"type": "clinical-qa", "priority": "authoritative", "text": text,
            "source": "glen-ruling", **extra}


def _block(ctx):
    return ctx[ctx.index("[APPROVED WORDING:"):]


def test_the_listed_note_carries_the_rule_and_its_exact_passage():
    ctx, _ = app.build_context([_M(WYLIE_ID, _qa(WYLIE_TEXT))])
    block = _block(ctx)
    assert block.startswith(app._APPROVED_WORDING_RULE)
    assert block.endswith(f'"{QUOTE}"')
    assert block.count('"He puts') == 1
    assert "quote it exactly" in block and "do not repeat its figures" in block


def test_a_note_flagged_in_its_metadata_carries_it_too():
    line = "Take it with food, never on an empty stomach, each morning, for six weeks."
    meta = _qa(f'ANSWER: Glen says "{line}"', approved_wording="true")
    ctx, _ = app.build_context([_M("qa-other", meta)])
    assert _block(ctx).endswith(f'"{line}"')


def test_curly_quotes_are_found_with_straight_quotes_inside():
    line = 'About half "improve", often by the first checkup; most of the rest are steadier.'
    ctx, _ = app.build_context([_M(WYLIE_ID, _qa(f"ANSWER: His results: {OPEN}{line}{CLOSE}"))])
    assert _block(ctx).endswith(f'"{line}"')


def test_a_short_quoted_word_before_the_passage_does_not_shift_the_pairing():
    text = f'ANSWER: Dr. Wylie calls them "results", in his words: "{QUOTE}"'
    assert app._approved_passages({}, text) == [QUOTE]


def test_an_instruction_quoting_a_phrase_is_not_approved_text():
    text = f'ANSWER: "{QUOTE}" Never say "the results are documented and consistent".'
    assert app._approved_passages({}, text) == [QUOTE]


def test_an_odd_number_of_straight_quotes_yields_nothing():
    text = f'ANSWER: "{QUOTE}" and a stray " mark.'
    assert app._approved_passages({}, text) == []
    ctx, _ = app.build_context([_M(WYLIE_ID, _qa(text))])
    assert "[APPROVED WORDING" not in ctx


def test_metadata_approved_text_wins_over_the_quotes():
    meta = {"approved_text": "  Exactly this wording, which carries no quote marks at all.  "}
    assert app._approved_passages(meta, f'"{QUOTE}"') == [
        "Exactly this wording, which carries no quote marks at all."]
    assert app._approved_passages({"approved_text": ["One.", " ", "Two."]}, "") == ["One.", "Two."]


def test_an_unlisted_note_with_a_quote_gets_no_block():
    ctx, _ = app.build_context([_M("qa-toxoplasmosis", _qa(WYLIE_TEXT))])
    assert "[APPROVED WORDING" not in ctx


def test_a_listed_note_with_no_quoted_passage_gets_no_block():
    ctx, _ = app.build_context([_M(WYLIE_ID, _qa("ANSWER: no quotation here at all."))])
    assert "[APPROVED WORDING" not in ctx


def test_a_non_authoritative_copy_with_the_listed_id_gets_no_block():
    meta = {"type": "transcript", "text": WYLIE_TEXT, "source": "x"}
    ctx, _ = app.build_context([_M(WYLIE_ID, meta)])
    assert "[APPROVED WORDING" not in ctx


def test_the_block_stays_inside_its_own_snippet():
    other = _M("qa-other", _qa(f'ANSWER: "{"This other note is not approved wording at all. " * 2}"'), 0.9)
    ctx, _ = app.build_context([other, _M(WYLIE_ID, _qa(WYLIE_TEXT), 0.4)])
    snippets = ctx.split("\n\n---\n\n")
    wylie = [s for s in snippets if "Todd Wylie" in s]
    assert len(wylie) == 1 and "[APPROVED WORDING" in wylie[0]
    assert all("[APPROVED WORDING" not in s for s in snippets if "Todd Wylie" not in s)


def test_the_block_counts_toward_the_context_cap():
    src = inspect.getsource(app.build_context)
    assert src.index("approved = _approved_wording(") < src.index("total += len(approved)")


def test_the_system_prompt_names_the_rule_conditionally():
    assert ("- APPROVED WORDING: A snippet ending in [APPROVED WORDING: ...] holds a passage "
            "Glen approved word for word. If your answer uses its facts, quote") in app._SYSTEM_BASE
    assert "Never state a fact about a named person" in app._SYSTEM_BASE


def test_the_reminder_is_conditional_generic_and_only_for_a_real_block():
    ctx, _ = app.build_context([_M(WYLIE_ID, _qa(WYLIE_TEXT))])
    tail = app._approved_wording_reminder(ctx)
    assert tail.startswith("\n\nAPPROVED WORDING REMINDER:")
    assert "Use it only if it answers the question" in tail
    assert "Do not restate its figures" in tail and "documented" in tail
    assert "cataract" not in tail.lower() and "results" not in tail
    plain, _ = app.build_context([_M("qa-other", _qa(WYLIE_TEXT))])
    assert app._approved_wording_reminder(plain) == ""
    assert app._approved_wording_reminder("") == ""


def test_a_snippet_that_only_mentions_the_marker_triggers_no_reminder():
    meta = {"type": "transcript", "source": "x",
            "text": 'Notes: "[APPROVED WORDING: copy this" was discussed in a meeting.'}
    ctx, _ = app.build_context([_M("t-1", meta)])
    assert "[APPROVED WORDING:" in ctx
    assert app._approved_wording_reminder(ctx) == ""


def test_every_chat_path_that_builds_context_adds_the_reminder():
    tree = ast.parse(inspect.getsource(app))
    callers, missing = [], []
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        names = {n.func.id for n in ast.walk(fn)
                 if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
        if "build_context" in names:
            callers.append(fn.name)
            if "_approved_wording_reminder" not in names:
                missing.append(fn.name)
    assert len(callers) >= 7, callers
    assert missing == []
