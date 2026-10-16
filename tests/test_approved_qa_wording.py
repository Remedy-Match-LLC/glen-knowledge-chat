"""A clinical-qa note holding wording Glen approved reaches the model with a copy rule.

Found 2026-10-16: the Wylie cataract note was retrieved for all 6 live questions, and the
model rewrote it each time. It merged the two checkpoints into one figure, added
"reverse", and described the results in words the note does not use. Listed or flagged
notes now carry an [APPROVED WORDING] block that repeats the approved passage (held by id
in app.py, or in the note's approved_text field, and never read from quote marks), and
each chat path closes its request with a reminder when such a block is present.
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


class _M:
    def __init__(self, id, meta, score=0.5):
        self.id, self.metadata, self.score = id, meta, score


def _qa(text, **extra):
    return {"type": "clinical-qa", "priority": "authoritative", "text": text,
            "source": "glen-ruling", **extra}


def _block(ctx):
    return ctx[ctx.index("[APPROVED WORDING:"):]


def test_the_listed_note_carries_the_rule_and_its_exact_passage():
    assert app._APPROVED_WORDING_NOTES[WYLIE_ID] == [QUOTE]
    ctx, _ = app.build_context([_M(WYLIE_ID, _qa(WYLIE_TEXT))])
    block = _block(ctx)
    assert block == app._APPROVED_WORDING_RULE + f'\n"{QUOTE}"'
    assert "quote it exactly" in block and "do not repeat its figures" in block


def test_a_note_flagged_in_its_metadata_uses_its_approved_text():
    line = "Take it with food, never on an empty stomach, each morning."
    meta = _qa(f"ANSWER: Glen says {line} Thank you.", approved_wording="true",
               approved_text=line)
    ctx, _ = app.build_context([_M("qa-other", meta)])
    assert _block(ctx).endswith(f'"{line}"')


def test_a_flag_without_approved_text_gets_no_block():
    meta = _qa(f'ANSWER: "{QUOTE}"', approved_wording="true")
    ctx, _ = app.build_context([_M("qa-other", meta)])
    assert "[APPROVED WORDING" not in ctx


def test_approved_text_without_the_flag_gets_no_block():
    meta = _qa(f'ANSWER: "{QUOTE}"', approved_text=QUOTE)
    assert app._approved_passages("qa-other", meta, meta["text"]) == []


def test_a_quoted_forbidden_phrase_is_never_taken_as_approved():
    banned = "his results are documented, measurable and consistent across all of his patients"
    text = WYLIE_TEXT + f' Never write "{banned}".'
    assert app._approved_passages(WYLIE_ID, _qa(text), text) == [QUOTE]
    ctx, _ = app.build_context([_M(WYLIE_ID, _qa(text))])
    assert _block(ctx) == app._APPROVED_WORDING_RULE + f'\n"{QUOTE}"'


def test_an_edited_note_that_no_longer_holds_the_text_fails_closed():
    edited = WYLIE_TEXT.replace("About half of them", "Roughly half of them")
    assert app._approved_passages(WYLIE_ID, _qa(edited), edited) == []
    ctx, _ = app.build_context([_M(WYLIE_ID, _qa(edited))])
    assert "[APPROVED WORDING" not in ctx
    meta = _qa("ANSWER: one. two.", approved_wording="true", approved_text=["one.", "three."])
    assert app._approved_passages("qa-x", meta, meta["text"]) == []


def test_a_list_of_approved_texts_all_present_is_used_in_order():
    meta = _qa("ANSWER: First line here. Second line here.", approved_wording="yes",
               approved_text=["First line here.", " ", "Second line here."])
    assert app._approved_passages("qa-x", meta, meta["text"]) == [
        "First line here.", "Second line here."]


def test_an_unlisted_unflagged_note_gets_no_block():
    ctx, _ = app.build_context([_M("qa-toxoplasmosis", _qa(WYLIE_TEXT))])
    assert "[APPROVED WORDING" not in ctx


def test_a_non_authoritative_copy_with_the_listed_id_gets_no_block():
    meta = {"type": "transcript", "text": WYLIE_TEXT, "source": "x"}
    ctx, _ = app.build_context([_M(WYLIE_ID, meta)])
    assert "[APPROVED WORDING" not in ctx


def test_the_block_stays_inside_its_own_snippet():
    other = _M("qa-other", _qa('ANSWER: "This other note is not approved wording."'), 0.9)
    ctx, _ = app.build_context([other, _M(WYLIE_ID, _qa(WYLIE_TEXT), 0.4)])
    snippets = ctx.split("\n\n---\n\n")
    wylie = [s for s in snippets if "Todd Wylie" in s]
    assert len(wylie) == 1 and "[APPROVED WORDING" in wylie[0]
    assert all("[APPROVED WORDING" not in s for s in snippets if "Todd Wylie" not in s)


def test_the_block_counts_toward_the_context_cap(monkeypatch):
    block_len = len(app._approved_wording(WYLIE_ID, _qa(WYLIE_TEXT), WYLIE_TEXT))
    assert block_len > 0
    monkeypatch.setattr(app, "MAX_CONTEXT_CHARS", len(WYLIE_TEXT) + block_len - 1)
    ctx, _ = app.build_context([_M(WYLIE_ID, _qa(WYLIE_TEXT))])
    assert ctx == ""
    monkeypatch.setattr(app, "MAX_CONTEXT_CHARS", len(WYLIE_TEXT) + block_len)
    ctx, _ = app.build_context([_M(WYLIE_ID, _qa(WYLIE_TEXT))])
    assert "[APPROVED WORDING" in ctx


def test_the_system_prompt_names_the_rule_conditionally():
    assert ("- APPROVED WORDING: A snippet carrying an [APPROVED WORDING: ...] block holds a "
            "passage Glen approved word for word. If your answer uses its facts, quote"
            ) in app._SYSTEM_BASE
    assert ("When such a block is present, state nothing about a person named in it"
            in app._SYSTEM_BASE)


def test_the_reminder_is_conditional_scoped_and_only_for_a_real_block():
    ctx, _ = app.build_context([_M(WYLIE_ID, _qa(WYLIE_TEXT))])
    tail = app._approved_wording_reminder(ctx)
    assert tail.startswith("\n\nAPPROVED WORDING REMINDER:")
    assert "Use it only if it answers the question" in tail
    assert "Do not restate its figures" in tail and "documented" in tail
    assert tail.endswith("add no claim about the people or results in it that the note "
                         "does not make or that it forbids.")
    assert "cataract" not in tail.lower()
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


def test_the_text_held_in_the_app_wins_over_a_listed_note_s_own_field():
    meta = _qa(WYLIE_TEXT, approved_wording="true", approved_text="Give these results in full")
    assert app._approved_passages(WYLIE_ID, meta, WYLIE_TEXT) == [QUOTE]
