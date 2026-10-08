"""The answer audit must catch the bugs that actually shipped.

A checker that never fires is worse than none — it manufactures confidence.
These fixtures are the real answers production gave on 2026-07-20, so if the
audit logic drifts, the cases it was built for go red.

Offline: audit() takes an answer string, so nothing here touches the network.
"""
import importlib.util
import json
import pathlib

import pytest

_ROOT = pathlib.Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("aa", _ROOT / "scripts" / "answer_audit.py")
aa = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(aa)


@pytest.fixture(scope="module")
def products():
    return json.load(open(_ROOT / "data" / "products.json"))["products"]


def test_catches_invented_price(products):
    """The helmet is $4,997 + $32. Production said "$754 (includes $132
    shipping)" — the $132 leaked from the Healing Tools Package."""
    f = aa.audit(
        "[NIR Brain Frequency Helmet]"
        "(https://illtowell.com/begin/product/nir-brain-frequency-helmet)\n"
        "**Price:** $754 (includes $132 shipping)", products)
    assert any("754" in x for x in f), f


def test_catches_wrong_product_link(products):
    """"Harmony Soft Laser" was linked to /clarity, a $69.97 formulation."""
    f = aa.audit("[Harmony Soft Laser](https://illtowell.com/begin/product/clarity)",
                 products)
    assert any("clarity" in x.lower() for x in f), f


def test_catches_retired_destinations(products):
    assert aa.audit("log in at healingoasis.practicebetter.io", products)
    assert aa.audit("buy at https://remedymatch.com/resources/345-x", products)


def test_catches_link_to_nonexistent_product(products):
    f = aa.audit("[Mega Device](https://illtowell.com/begin/product/no-such-thing)",
                 products)
    assert any("NOT in the catalog" in x for x in f), f


def test_does_not_flag_a_correct_answer(products):
    """False positives train people to ignore the report."""
    assert aa.audit(
        "[NIR Brain Frequency Helmet]"
        "(https://illtowell.com/begin/product/nir-brain-frequency-helmet) "
        "is $4,997.00 list price. The page will show your current pricing.",
        products) == []


def test_does_not_flag_a_correct_cheap_product(products):
    assert aa.audit(
        "[Terrain Restore](https://illtowell.com/begin/product/terrain-restore) "
        "is $70 list price.", products) == []


# 2026-10-08: the live bot's AngioGenX answer, trimmed to the invented clauses.
_ANGIOGENX_LIVE = (
    "These five clinically active compounds, including notoginseng (a traditional "
    "adaptogen), strengthen vascular endothelial function and reduce the drivers of "
    "abnormal vessel growth (neovascularization). Stop before surgery (it supports "
    "vascular remodeling, which can interfere with hemostasis during recovery). "
    "Single daily capsule maximizes bioavailability and tissue uptake."
)


def test_claim_questions_are_in_the_question_set():
    claim = [q for g, q in aa.QUESTIONS if g == "claim"]
    assert "What is AngioGenX and how does it help the eyes?" in claim
    assert "How much AngioGenX should I take and why?" in claim


def test_catches_every_invented_mechanism_phrase(products):
    f = aa.audit(_ANGIOGENX_LIVE, products, "claim")
    for phrase in ("neovascular", "vascular remodeling", "hemostasis",
                   "maximizes bioavailability", "clinically active", "adaptogen",
                   "endothelial"):
        assert any(f"'{phrase}'" in x for x in f), (phrase, f)


def test_claim_check_runs_only_on_claim_questions(products):
    """The phrases can be legitimate elsewhere; only claim answers are checked."""
    assert aa.audit(_ANGIOGENX_LIVE, products) == []
    assert aa.audit(_ANGIOGENX_LIVE, products, "price") == []


def test_a_sourced_angiogenx_answer_passes(products):
    assert aa.audit("AngioGenX supports healthy circulation in the eyes. Take one "
                    "capsule daily. Stop two weeks before surgery.",
                    products, "claim") == []
