"""Clarity's caution, approved by Glen on 2026-10-25 in the formulation tab and shipped on
his "yes" in the platform tab the same day."""
import pytest

import app

TEXT = ("Do not use if you are pregnant or breastfeeding. It is not for children. Consult "
        "your health professional before use if you take prescription medications, "
        "especially blood thinners or blood sugar medicines. Also consult first if you are "
        "planning surgery. This formula may change how some medications are processed.")


def test_clarity_carries_the_approved_caution():
    p = app._PRODUCTS["products"]["clarity"]
    assert p["warning"] == TEXT and p["chat_caution"] is True


@pytest.mark.parametrize("q", ["Is Clarity safe while pregnant?",
                               "Can I take Clarity with my blood thinner?",
                               "Can I take Clarity before surgery?"])
def test_a_clarity_safety_question_links_its_caution(q):
    assert "clarity" in app._caution_link_slugs(q)


@pytest.mark.parametrize("q", ["How much Crystalline Clarity should I take while pregnant?",
                               "Clarity of mind is what I lack. Is NAC safe in pregnancy?"])
def test_other_wording_does_not_link_clarity(q):
    assert "clarity" not in app._caution_link_slugs(q)
