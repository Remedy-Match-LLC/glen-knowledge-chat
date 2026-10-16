""""What is in <product>" is a product cue for a name typed in lowercase.

Found 2026-10-16 (formulation-32): "what is in fibrolysis factors" and "whats in
fibrolysis factors?" got no facts block, while "What is in Fibrolysis Factors?" did. With
no block the model named none of the five actives, said the source did not list them,
and added scar claims Glen has ruled out. A lowercase cue name needs a cue beside it, and
"what is in" was not one.
"""
import pytest

import app


@pytest.mark.parametrize("question", [
    "What is in Fibrolysis Factors?",
    "what is in fibrolysis factors",
    "whats in fibrolysis factors?",
    "what's in fibrolysis factors?",
    "what’s in fibrolysis factors?",
    "ingredients in fibrolysis factors?",
    "ingredients of fibrolysis factors",
    "what is in the fibrolysis factors?",
    "what is in fibrolysis factors capsules?",      # a cue after the name still counts
])
def test_what_is_in_brings_the_fibrolysis_factors_block(question):
    block = app.named_product_facts_block(question)
    assert "### Fibrolysis Factors" in block, question


@pytest.mark.parametrize("question,name", [
    ("what is in clear the way?", "Clear the Way"),
    ("whats in reverse age", "Reverse AGE"),
    ("what\u2019s fibrolysis factors?", "Fibrolysis Factors"),
    ("what is in the estrogen and fibrin balance program?",
     "Estrogen and Fibrin Balance Program"),
])
def test_the_cue_serves_every_lowercase_cue_name(question, name):
    assert f"### {name}" in app.named_product_facts_block(question), question


@pytest.mark.parametrize("question", [
    "what is in clear the way for my lymph",        # the name runs into a longer phrase
    "what is in reverse age discrimination law",
    "what is in brain cleanse and liver flush protocols",   # "and" extends the name
    "the ingredients of brain cleanse and detox diets",
    "what is in stamina plus or minus",
    "what is in microbiome?",                       # strict name: a "what" cue never counts
    "ingredients in microbiome?",                   # nor an "ingredients" cue
    "what is in the appestat?",                     # "the" before a one-word name
])
def test_ordinary_wording_still_brings_no_block(question):
    assert app.named_product_facts_block(question) == "", question
