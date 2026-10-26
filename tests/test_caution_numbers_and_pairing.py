"""Live read-back after #2138 (knowledge, 2026-10-25), B17 Syntropy.

Asked about dizziness, the bot moved the caution's Poison Control symptoms to 911. Asked
about taking it with B17 Max, it said the two were "designed to work as a pair" and named
"the safe total daily ceiling". Neither is in any approved text.
"""
import app

KEEP = "Keep each symptom with the number this caution gives it"
PAIR = ("Never say two products are designed or made to work together, and never call a "
        "dose or a combined amount safe, unless this text says so.")


def test_a_symptom_question_keeps_each_symptom_with_its_number():
    b = app._named_product_facts("I feel dizzy on B17 Syntropy, should I take less?")
    assert "### B17 Syntropy" in b and "Poison Control, 1-800-222-1222" in b
    assert KEEP in b


def test_a_pairing_question_gets_no_invented_pairing_or_safety():
    b = app._named_product_facts("Can I take B17 Syntropy with B17 Max?")
    assert "### B17 Syntropy" in b and "### B17 Max" in b
    assert PAIR in b


def test_a_present_or_unclear_danger_still_goes_to_911_first():
    # Review of 8cd9325c: "unusual breathing" (Poison Control) and "breathing difficulty"
    # (911) sit close together, so the doubtful case must still reach 911.
    assert ("If the user may be in danger now, or a symptom could fit either list, give "
            "911 first.") in app._STOP_AT_SYMPTOM_RULE


def test_a_caution_only_pair_gets_the_same_rule():
    # Caution-only blocks send everything but the caution to the snippets, which say
    # "synergistic with" freely (review of 8cd9325c).
    products = app._PRODUCTS["products"]
    names = [p["name"] for p in products.values()
             if p.get("chat_caution") and not p.get("copy_pinned") and not p.get("inactive")]
    q = f"Can I take {names[0]} with {names[1]}?"
    b = app._named_product_facts(q)
    assert "approved store text for each product's caution" in b, q
    assert PAIR in b
