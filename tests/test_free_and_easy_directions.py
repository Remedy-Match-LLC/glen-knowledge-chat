"""Free & Easy: the chat quotes the approved directions, and no other dose.

Glen, 2026-10-27, formulation tab: "yes" to the directions and the caution (store spec
production/05 Formulations/free-and-easy/2026-10-27). Glen, 2026-10-28, platform tab: "yes"
to the chat quoting those directions word for word.

Formulation's read-back, 2026-10-28: with only the caution approved for the chat, the bot
answered the dose from the snippets and twice in three tries invented one, once "1 capsule
up to three times a day before meals". Pinning the directions puts the approved line and the
pinned dose rule in the block, as on Iron Out, Microbiome and Breast Support.
"""
import json

import pytest

import app

DIRECTIONS = "Take 1 capsule daily with dinner or an evening snack, or as guided."
CAUTION = ("Not for use in pregnancy or while breastfeeding. If you are under medical "
           "treatment, consult your physician before use.")
FIELDS = {"directions": DIRECTIONS, "copy_pinned": ["directions"], "warning": CAUTION,
          "chat_caution": True}
QUESTIONS = ["Free & Easy dose?",
             "How many Free & Easy capsules can I take a day?",
             "Can I take Free & Easy twice a day?",
             "When should I take Free & Easy?",
             "How do I take Free & Easy?"]


def _repo(path):
    """The repo file itself, never via DATA_DIR, which other tests repoint."""
    return json.load(open(path))


def test_the_four_fields_are_the_approved_text_in_both_files():
    products = _repo("data/products.json")["products"]["free-and-easy"]
    corrections = _repo("data/products-manual-corrections.json")["free-and-easy"]
    for record in (products, corrections):
        assert {k: record.get(k) for k in FIELDS} == FIELDS


def test_only_the_directions_are_pinned():
    """No description is approved, so none may reach the bot."""
    for record in (_repo("data/products.json")["products"]["free-and-easy"],
                   _repo("data/products-manual-corrections.json")["free-and-easy"]):
        assert record["copy_pinned"] == ["directions"]


@pytest.mark.parametrize("question", QUESTIONS)
def test_the_section_is_exactly_the_approved_lines(question):
    """Line for line, so no other dose text can sit beside the approved ones."""
    block = app.named_product_facts_block(question)
    assert "### Free & Easy" in block, question
    section = block[block.index("### Free & Easy"):].split("\n### ")[0].rstrip("\n")
    assert section == "\n".join([
        "### Free & Easy", f"Page: {app._catalog_page_url('free-and-easy')}",
        app._NO_DESCRIPTION.format(name="Free & Easy"),
        f"Directions: {DIRECTIONS}",
        app._PINNED_DOSE_RULE.format(name="Free & Easy", example=""),
        app._CAUTION_COPY_REMINDER,
        f"Caution: {CAUTION}",
        app._CAUTION_STAY_LINE, "", app._CAUTION_CLOSING_REMINDER])
    # The safety wording itself, so emptying a constant cannot pass (review round 2).
    for line in ["The Directions above are the only dose for Free & Easy.",
                 "That advice is withdrawn: never give it",
                 "say to ask Dr. Glen about anything beyond them",
                 "Copy each caution below word for word"]:
        assert line in section, line


def test_a_question_on_the_caution_still_gets_the_directions():
    """Pregnancy touches the caution, so it is linked, never written (Glen, 2026-10-24).
    The directions and the dose rule stay in the block."""
    block = app.named_product_facts_block("Is Free & Easy safe in pregnancy?")
    section = block[block.index("### Free & Easy"):].split("\n### ")[0]
    assert f"Directions: {DIRECTIONS}" in section
    assert "The Directions above are the only dose for Free & Easy." in section
    assert f"Caution: {CAUTION}" not in section


@pytest.mark.parametrize("question", ["how do I take free & easy",
                                      "how much free & easy should I take",
                                      "How do I take Free and Easy?",
                                      "What are the directions for Free and Easy?",
                                      "how many free and easy capsules a day",
                                      "free and easy dose?",
                                      "How Do I Take Free And Easy?"])
def test_lowercase_and_the_and_spelling_get_the_directions(question):
    """Clients type lowercase and spell "&" as "and" (review round 2, 2026-10-28)."""
    block = app.named_product_facts_block(question)
    assert "### Free & Easy" in block, question
    assert f"Directions: {DIRECTIONS}" in block


@pytest.mark.parametrize("question", ["I feel free and easy today",
                                      "we kept it free and easy",
                                      "free & easy"])
def test_an_everyday_phrase_gets_no_block(question):
    """Lowercase needs a product cue beside it, as for Iron Out."""
    assert "### Free & Easy" not in app.named_product_facts_block(question)


def test_the_and_spelling_is_only_for_free_and_easy():
    """Other "&" names keep their catalog spelling."""
    assert "free-and-easy" in app._AND_NAMES
    assert app._AND_NAMES == frozenset({"free-and-easy"})
    products = _repo("data/products.json")["products"]
    # Wash & Rinse gets a block in catalog spelling, and none spelt with "and".
    assert "wash--rinse" in app._named_product_spans("Wash & Rinse dose?", products)
    assert "wash--rinse" not in app._named_product_spans("Wash and Rinse dose?", products)


@pytest.mark.parametrize("question", ["free & easy directions"])
def test_known_gaps_get_no_block(question):
    """"directions" after a name is not a product cue for any product. Listed in the PR,
    not widened here. A change that closes one fails this."""
    assert "### Free & Easy" not in app.named_product_facts_block(question)


def test_a_rebuild_keeps_the_four_fields():
    import copy
    from scripts.apply_enrichment import apply_corrections
    products = copy.deepcopy(_repo("data/products.json")["products"])
    for k in FIELDS:
        products["free-and-easy"].pop(k)
    apply_corrections(products,
                      copy.deepcopy(_repo("data/products-manual-corrections.json")))
    assert {k: products["free-and-easy"].get(k) for k in FIELDS} == FIELDS
