"""Knowledge's read-back of #2352 (2026-11-26): asked "Can I take 10 Nerve Repair a day?",
5 of 6 answers said "That is within the Directions." when 5 a day is the most. Code now
compares the number with the label, and the model may only say "within" when code has."""
import pytest

import app

NR = "1 capsule up to 5 times daily with food, or as guided."


@pytest.mark.parametrize("d,most", [
    (NR, (1, 5)),
    ("1 to 3 capsules daily, 30 to 60 minutes before meals, or as guided.", (3, 3)),
    ("1 capsule 1 to 3 times daily between meals, or as guided.", (1, 3)),
    ("Take 1 capsule up to twice daily, between meals or at bedtime, or as guided.", (1, 2)),
    ("1 capsule 30 to 45 minutes before meals, up to 2 a day, or as guided.", (1, 2)),
    ("Take 1 vegicap a day between meals, or as guided. Up to 3 vegicaps a day may be "
     "used, or as guided.", (3, 3)),
    ("1 capsule with each heavier meal, or as guided.", None),
    ("1 capsule in the middle of the night, or as guided.", None),
    ("Take 1/2 tablet daily, or as guided.", None),
    ("Take 0.5 capsules daily, or as guided.", None),
    ("1 capsule up to 3 times daily, maximum 2 capsules a day, or as guided.", None),
    ("10 drops 3 times a day in water, or as guided.", None),
])
def test_the_label_maximum_is_read_only_when_certain(d, most):
    assert app._directions_most(d) == most


@pytest.mark.parametrize("q,verdict", [
    ("Can I take 10 Nerve Repair a day?", ("over", 10, 5)),
    ("Can I take ten Nerve Repair daily?", ("over", 10, 5)),
    ("Can I take 6 Nerve Repair a day?", ("over", 6, 5)),
    ("Can I take 5 Nerve Repair a day?", None),     # code never certifies "within"
    ("Can I take 1 Nerve Repair?", None),
    ("Can I take 1 Nerve Repair every hour?", None),
    ("Can I take 3 Nerve Repair capsules 3 times a day?", None),
    ("Can I take 3 Nerve Repair?", None),          # at once or in a day: not certain
    ("Can I take 2 or 3 Nerve Repair a day?", None),
    ("How do I take Nerve Repair?", None),
])
def test_the_verdict(q, verdict):
    assert app._amount_verdict(q, NR, "Nerve Repair") == verdict


@pytest.mark.parametrize("q", [
    "How do I take B17 Syntropy?",
    "Can I take 1 Thermogenesis 30 minutes before meals?",
    "Can I take Nerve Repair for 30 days?",
    "Should I take Nerve Repair with 8 oz of water?",
    "I'm 70. Can I take Nerve Repair?",
    "Can I take Nerve Repair with 400 mg magnesium?",
])
def test_a_number_that_is_not_a_capsule_count_is_not_read(q):
    assert app._asked_amount(q, "Nerve Repair") is None or \
        app._asked_amount(q, "Nerve Repair")[0] <= 1


def test_b17_never_reads_as_17():
    assert app._asked_amount("How do I take B17 Syntropy?", "B17 Syntropy") is None


def test_a_count_belongs_only_to_the_product_it_sits_beside():
    q = "Can I take 3 Nerve Repair and Moisturize a day?"
    assert app._asked_amount(q, "Nerve Repair") == (3, True)
    assert app._asked_amount(q, "Moisturize") is None


def test_a_follow_up_is_checked_on_its_own_words():
    block = app.named_product_facts_block(
        "How do I take it?", prior_user_turns=["Can I take 10 Nerve Repair a day?"])
    assert "is MORE than these Directions allow" not in block


def test_the_facts_block_says_10_is_over_and_never_within():
    block = app.named_product_facts_block("Can I take 10 Nerve Repair a day?")
    assert "is MORE than these Directions allow (5 a day at most)" in block
    assert 'say "That is within the Directions."' not in block


def test_an_amount_code_cannot_check_never_gets_within():
    block = app.named_product_facts_block("Can I take 3 Nerve Repair?")
    assert 'say "That is within the Directions."' not in block
    assert "never say whether it is within these Directions" in block


def test_code_never_certifies_within():
    import inspect
    assert "_AS_GUIDED_WITHIN" not in inspect.getsource(app)
    for q in ("Can I take 2 Thermogenesis a day?", "Can I take 1 Nerve Repair every hour?"):
        assert 'say "That is within the Directions."' not in app.named_product_facts_block(q)


def test_a_named_pregnancy_never_gets_within():
    block = app.named_product_facts_block("I am pregnant. Can I take 2 Thermogenesis a day?")
    assert 'say "That is within the Directions."' not in block


def test_a_number_inside_a_word_is_not_a_count():
    # "B12 capsules" must not read as 12 capsules.
    assert app._asked_amount("Can I take Nerve Repair with B12 capsules?", "Nerve Repair") is None


@pytest.mark.parametrize("q,name", [
    ("How long does a 60 capsule bottle of Nerve Repair last?", "Nerve Repair"),
    ("I've taken 10 Nerve Repair capsules this week, is that ok?", "Nerve Repair"),
    ("After 6 weeks on Nerve Repair, should I stop?", "Nerve Repair"),
    ("Can my 2 children take Moisturize?", "Moisturize"),
    ("Can I order 3 bottles of Moisturize?", "Moisturize"),
    ("Can I take 4 Nerve Repair with my Nerve Pulse?", "Nerve Pulse"),
])
def test_round3_a_number_that_is_not_a_daily_dose_never_reads_over(q, name):
    p = next(x for x in app._PRODUCTS["products"].values() if x.get("name", "").startswith(name))
    assert app._amount_verdict(q, p.get("directions"), name) is None


def test_fifteen_a_day_reads_over():
    assert app._amount_verdict("Can I take fifteen Nerve Repair a day?", NR, "Nerve Repair") \
        == ("over", 15, 5)


def test_the_unchecked_rule_forbids_a_yes():
    assert "or that they can take it" in app._AS_GUIDED_UNCHECKED


def test_only_a_taken_number_counts():
    q = "I bought 10 Nerve Repair capsules a day ago. How do I take them?"
    assert app._amount_verdict(q, NR, "Nerve Repair") is None


def test_only_a_daily_amount_reads_over():
    assert app._amount_verdict("Can I take 6 Nerve Repair this week?", NR, "Nerve Repair") is None


def test_a_count_for_nerve_repair_is_never_read_for_nerve_pulse():
    pulse = app._PRODUCTS["products"]["nerve-pulse"]["directions"]
    q = "Can I take 4 Nerve Repair a day with my Nerve Pulse?"
    assert app._amount_verdict(q, pulse, "Nerve Pulse") is None
    assert app._amount_verdict(q, NR, "Nerve Repair") is None     # 4 is within 5
