"""A clinical-qa note holding wording Glen approved reaches the model with a copy rule.

Found 2026-10-16: the Wylie cataract note was retrieved for all 6 live questions, and the
model rewrote it each time. It merged the two checkpoints into one figure, added
"reverse", and invented where Dr. Wylie practises. Listed or flagged notes now carry an
[APPROVED WORDING] block that repeats the quoted passage and says to copy it exactly.
"""
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
    ctx, _ = app.build_context([_M(WYLIE_ID, _qa(WYLIE_TEXT))])
    block = _block(ctx)
    assert block.startswith(app._APPROVED_WORDING_RULE)
    assert block.endswith(f'"{QUOTE}"')
    assert "quote it exactly" in block and "do not repeat its figures" in block


def test_a_note_flagged_in_its_metadata_carries_it_too():
    text = 'ANSWER: Glen says "Take it with food, never on an empty stomach, each morning."'
    ctx, _ = app.build_context([_M("qa-other", _qa(text, approved_wording="true"))])
    assert _block(ctx).endswith('"Take it with food, never on an empty stomach, each morning."')


def test_curly_quotes_are_found():
    text = "ANSWER: His results: “About half show improvement by the first checkup.”"
    ctx, _ = app.build_context([_M(WYLIE_ID, _qa(text))])
    assert _block(ctx).endswith('"About half show improvement by the first checkup."')


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
    other = _M("qa-other", _qa('ANSWER: "This other note is not approved wording at all."'), 0.9)
    ctx, _ = app.build_context([other, _M(WYLIE_ID, _qa(WYLIE_TEXT), 0.4)])
    snippets = ctx.split("\n\n---\n\n")
    wylie = [s for s in snippets if "Todd Wylie" in s]
    assert len(wylie) == 1 and "[APPROVED WORDING" in wylie[0]
    assert all("[APPROVED WORDING" not in s for s in snippets if "Todd Wylie" not in s)


def test_the_system_prompt_names_the_rule():
    assert "- APPROVED WORDING: A snippet ending in [APPROVED WORDING: ...]" in app._SYSTEM_BASE
    assert "Never state a fact about a named person" in app._SYSTEM_BASE


def test_the_request_closes_with_a_reminder_only_when_a_note_carries_approved_wording():
    ctx, _ = app.build_context([_M(WYLIE_ID, _qa(WYLIE_TEXT))])
    tail = app._approved_wording_reminder(ctx)
    assert tail.startswith("\n\nAPPROVED WORDING REMINDER:")
    assert "Do not restate its figures" in tail and "reversing" in tail
    plain, _ = app.build_context([_M("qa-other", _qa(WYLIE_TEXT))])
    assert app._approved_wording_reminder(plain) == ""
    assert app._approved_wording_reminder("") == ""


def test_chat_appends_the_reminder_to_the_user_turn():
    import inspect
    src = inspect.getsource(app.chat)
    facts = 'f"{named_product_facts_block(query, prior_user_turns=_prior_user)}"'
    assert facts + '\n            f"{_approved_wording_reminder(context_str)}"' in src
