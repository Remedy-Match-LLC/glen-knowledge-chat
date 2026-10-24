"""The bot never argues with a caution, and a no-caution answer loses its interaction snippets.

Production's after-deploy check, 2026-10-24: asked "Can I take Stress Release before
surgery?", the bot called the caution one-size, said many supplements are safe to continue
and offered a pause of days or weeks. Glen said "yes" in the production tab to two fixes for
every opted-in caution: surgery and autoimmune subject words, and a rule against arguing
with the caution or adding a time window. The same filter now drops interaction snippets
where a product says it has no caution (knowledge read-back on Clarity, 2026-10-24).
"""
import importlib
from types import SimpleNamespace

import pytest

NO_ARGUMENT = ("Do not argue with, qualify or weigh the caution, and give no time window it "
               "does not state.")
STARGROVE = {"book": "Herb, Nutrient, and Drug Interactions", "author": "Stargrove"}


@pytest.fixture
def appmod(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake")
    monkeypatch.setenv("PINECONE_API_KEY", "pcsk_fake")
    import app as a
    importlib.reload(a)
    return a


def _matches():
    return [SimpleNamespace(metadata=dict(STARGROVE)),
            SimpleNamespace(metadata={"source": "glen", "title": "Calm and the nervous system"})]


def test_the_stay_line_and_the_closing_reminder_forbid_arguing(appmod):
    assert appmod._CAUTION_STAY_LINE.endswith(" " + NO_ARGUMENT)
    assert appmod._CAUTION_CLOSING_REMINDER.endswith(" " + NO_ARGUMENT)


def test_the_surgery_answer_carries_the_rule_after_the_caution_and_at_the_end(appmod):
    q = "Can I take Stress Release before surgery?"
    b = appmod.named_product_facts_block(q)
    warning = appmod._PRODUCTS["products"]["stress-release"]["warning"]
    assert b.index(NO_ARGUMENT) > b.index(warning)
    assert b.count(NO_ARGUMENT) == 2 and b.endswith(NO_ARGUMENT)


@pytest.mark.parametrize("q, subject", [
    ("Can I take Stress Release before surgery?", "surgery"),
    ("I have an operation next week. Is Brain Cleanse ok?", "surgery"),
    ("Is Angiostasis ok before anesthesia?", "surgery"),
    ("Can I take Fibrosolve before my surgical procedure?", "surgery"),
    ("I have lupus. Can I take Stress Release?", "autoimmune"),
    ("Is Sleep Syntropy ok with rheumatoid arthritis?", "autoimmune"),
    ("I have an autoimmune condition. Is Brain Cleanse safe?", "autoimmune"),
    # Other word forms (review round 1).
    ("Can I take Stress Release before my general anesthetic?", "surgery"),
    ("Can I take Stress Release between my two surgeries?", "surgery"),
    ("I am being operated on Friday. Is Stress Release ok?", "surgery"),
    ("Can I take Stress Release before my op?", "surgery"),
    ("Is Stress Release ok before an anaesthetic?", "surgery"),
    ("Is Stress Release ok pre-op?", "surgery"),
    ("Is Stress Release ok in the operating room recovery?", "surgery"),
    ("I have psoriatic arthritis. Can I take Stress Release?", "autoimmune"),
    ("I have Hashimoto's. Can I take Stress Release?", "autoimmune"),
    ("I have Graves disease. Is Stress Release ok?", "autoimmune"),
    ("I have celiac disease. Is Stress Release ok?", "autoimmune"),
    ("Is Stress Release ok with coeliac disease?", "autoimmune"),
    ("Is Stress Release ok with Crohn's?", "autoimmune"),
    ("Is Stress Release ok with ulcerative colitis?", "autoimmune"),
    ("Is Stress Release ok with multiple sclerosis?", "autoimmune"),
    ("Is Stress Release ok with Sjogren's?", "autoimmune"),
    ("Is Stress Release ok with type 1 diabetes?", "autoimmune"),
    ("Is Stress Release ok with thyroiditis?", "autoimmune"),
    ("Is Stress Release ok with an auto-immune disease?", "autoimmune"),
])
def test_surgery_and_autoimmune_questions_name_the_subject(appmod, q, subject):
    assert subject in appmod._caution_subjects_asked(q)
    ms = _matches()
    assert appmod._drop_caution_subject_snippets(ms, q) == ms[1:]


def test_a_caution_that_names_neither_takes_no_such_subject(appmod):
    # Vitamin D Syntropy's caution names neither surgery nor an autoimmune condition.
    assert appmod._caution_subjects_asked("Is Vitamin D Syntropy ok before surgery?") == []
    assert appmod._caution_subjects_asked("Is Vitamin D Syntropy ok with lupus?") == []


@pytest.mark.parametrize("q", ["Can I take Brain Cleanse for better cooperation at work?",
                               "Can I operate machinery after Brain Cleanse?",
                               "Can I take Brain Cleanse with an optician's advice?"])
def test_operate_machinery_and_cooperation_are_not_surgery(appmod, q):
    assert "surgery" not in appmod._caution_subjects_asked(q)


@pytest.mark.parametrize("q", [
    "Can I take Clarity with my blood pressure medicine?",
    "Is Clarity safe while pregnant?",
    "Does Clarity interact with sertraline?",
    "Can I take Clarity before surgery?",
    # No list of drug names is whole, so a named product drops them for any question
    # (review round 2).
    "Can I take Clarity with lisinopril?",
    "Is Clarity ok during chemo?",
    "What is in Clarity?",
])
def test_a_no_caution_product_loses_the_interaction_snippets(appmod, q):
    assert "Clarity has no caution on its product page." in appmod.named_product_facts_block(q)
    assert appmod._no_caution_product_named(q)
    ms = _matches()
    assert appmod._drop_caution_subject_snippets(ms, q) == ms[1:]


def test_a_question_with_no_product_keeps_its_snippets(appmod):
    q = "Does ashwagandha interact with my thyroid medicine?"
    assert not appmod._no_caution_product_named(q)
    ms = _matches()
    assert appmod._drop_caution_subject_snippets(ms, q) is ms


def test_a_product_with_a_caution_is_not_a_no_caution_product(appmod):
    assert not appmod._no_caution_product_named("Can I take Stress Release with lisinopril?")


def test_a_follow_up_about_a_no_caution_product_loses_them_too(appmod):
    prior = ["Tell me about Clarity."]
    q = "Can I take it with my blood pressure medicine?"
    assert appmod._no_caution_product_named(q, prior)
    ms = _matches()
    assert appmod._drop_caution_subject_snippets(ms, q, prior) == ms[1:]


@pytest.mark.parametrize("q", ["Can I take Stress Release with lisinopril?",
                               "Is Stress Release ok with my statin?",
                               "Can I take Scar Silk with my medication?",
                               "Can I take Stress Release with my pills?"])
def test_a_caution_naming_prescription_medication_takes_any_medicine(appmod, q):
    assert "prescription" in appmod._caution_subjects_asked(q)
    ms = _matches()
    assert appmod._drop_caution_subject_snippets(ms, q) == ms[1:]


@pytest.mark.parametrize("q", ["Can I take Stress Release on my pillow?",
                               "Is Vitamin D Syntropy ok with my medication?"])
def test_no_prescription_subject_without_its_word_or_its_caution(appmod, q):
    # A pillow is not a pill, and Vitamin D Syntropy's caution names no prescription.
    assert "prescription" not in appmod._caution_subjects_asked(q)


def test_the_scope_of_the_rules_names_surgery(appmod):
    for text in (appmod._CAUTION_NO_ADDITIONS, appmod._CAUTION_STAY_LINE,
                 appmod._CAUTION_CLOSING_REMINDER):
        assert "pregnancy, surgery or" in text


def test_the_gated_surgery_answer_carries_the_rule_too(appmod):
    b = appmod.named_product_facts_block("Can I take Stress Release before surgery?",
                                         gated=True)
    assert b.count(NO_ARGUMENT) == 2 and b.endswith(NO_ARGUMENT)


def test_two_cautions_with_different_surgery_windows_each_keep_their_own(appmod):
    b = appmod.named_product_facts_block("Can I take Brain Cleanse and Fibrosolve before surgery?")
    assert "Stop a few days before surgery." in b
    assert "within about two weeks of surgery" in b
    assert b.count(NO_ARGUMENT) == 3 and b.endswith(NO_ARGUMENT)
