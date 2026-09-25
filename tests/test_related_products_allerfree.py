"""AllerFree in every spelling is never recommended (Glen 2026-09-24: Aller-Free is the
correct spelling of AllerFree; "yes" to dropping it from related-product suggestions).
DO_NOT_RECOMMEND also feeds condition triage, legacy store links and curated shop shelves."""
from dashboard.related_products import DO_NOT_RECOMMEND, guardrail_ok


def test_both_allerfree_slugs_are_never_recommended():
    assert {"aller-free-aid", "allerfree-homeoenergetic-drops"} <= DO_NOT_RECOMMEND


def test_guardrail_refuses_allerfree_as_a_related_product():
    products = {"aller-free-aid": {"name": "Aller-Free Aid for Inhalant Allergies"},
                "allerfree-homeoenergetic-drops": {"name": "AllerFree HomeoEnergetic Drops"},
                "immune-modulation": {"name": "Immune Modulation"},
                "microbiome": {"name": "Microbiome"}}
    assert not guardrail_ok("aller-free-aid", "microbiome", products)
    assert not guardrail_ok("allerfree-homeoenergetic-drops", "microbiome", products)
    assert guardrail_ok("immune-modulation", "microbiome", products)
