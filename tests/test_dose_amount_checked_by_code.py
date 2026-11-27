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
    assert app._amount_verdict(q, NR) == verdict


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
