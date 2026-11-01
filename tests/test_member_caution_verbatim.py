"""Members get a touched caution word for word; a stated do-not-use situation gets no dose.

Glen, platform tab, 2026-11-01 ("yes"): visitors who have agreed to the Terms also get the
full caution word for word. This replaces his 2026-10-24 "Chat doesn't duplicate the
caution" rule. Knowledge tab ruling, for everyone: when the visitor's stated situation
matches a "do not use" line, such as pregnancy or Graves' disease, the answer quotes the
caution and gives no dose or directions.
"""
import importlib
import json
from types import SimpleNamespace

import pytest

import app

MODEL_TEXT = "Model answer."
# Thyroid Support caution v7, embedded: thyroid-support has no warning on origin/main yet
# (marketing/04 Copy/thyroid-support-panel-2026-10-31/caution-v7.txt).
THYROID_V7 = "Do not use if you are pregnant or breastfeeding. Not for anyone 18 or under. Do not use if you have hormone-sensitive prostate cancer. Do not use if you have an overactive thyroid (hyperthyroidism or Graves' disease). Keep out of reach of children. Rare cases of liver injury have been linked to ashwagandha. Do not use if you have had liver injury from ashwagandha. If you have liver disease, consult your physician before use. Stop use and see your physician if you notice dark urine or yellowing skin or eyes. Each capsule contains about 150 mcg of iodine, the adult daily requirement. If you have any other thyroid condition or take other iodine products, consult your health professional before use. Ashwagandha may raise thyroid hormone levels and change thyroid test results. If you take a thyroid hormone medicine, consult your physician before use. Ginger may increase the risk of bleeding. If you take a blood thinner or antiplatelet medicine, such as warfarin, aspirin or clopidogrel, or have a bleeding disorder, consult your physician before use. If you are planning surgery, tell your surgical team you take this product and ask when to stop. Ashwagandha may add to the effect of sedatives or seizure medicines and may interact with medicines for diabetes, blood pressure or the immune system. Ginger may lower blood sugar too far when taken with diabetes medicines. If you take any prescription medicine, or have an autoimmune condition, consult your physician before use. Zinc can reduce how well quinolone and tetracycline antibiotics and penicillamine are absorbed. If you take one of these, ask your pharmacist how far apart to take them. If you take other zinc or selenium supplements, keep your total from food and supplements at or below 40 mg of zinc and 400 mcg of selenium a day. Ashwagandha may cause drowsiness, stomach upset, diarrhea or vomiting. Do not drive or operate machinery if it makes you drowsy. If these effects are severe, stop use and see your physician. Ashwagandha may be safe for up to three months. Ask your physician before using it longer."
THYROID_DIRECTIONS = "1 capsule daily with a meal, or as guided."
GRAVES = "Can I take Thyroid Support if I have Graves' disease?"


class _FakeStream:
    def __init__(self, text):
        self.text = text

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    @property
    def text_stream(self):
        return iter([self.text])


@pytest.fixture
def post(monkeypatch, tmp_path):
    """Posts to /chat with the model stubbed; returns (answer, prompts sent, gate seen)."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    importlib.reload(app)
    monkeypatch.setattr(app._cl.messages, "create", lambda **kw: (_ for _ in ()).throw(
        RuntimeError("no live calls in tests")))
    monkeypatch.setattr(app, "embed", lambda text: [0.0] * 1536)
    one = [SimpleNamespace(id="m0", score=0.8, metadata={"text": "x", "_source_ns": "x"})]
    monkeypatch.setattr(app, "query_all_namespaces", lambda vec: one)
    monkeypatch.setattr(app, "build_context", lambda m: ("snippet text", []))
    monkeypatch.setattr(app, "_is_gated_question", lambda *a, **k: True)
    sent = []

    def _stream(**kw):
        sent.append(kw["messages"][-1]["content"])
        return _FakeStream(MODEL_TEXT)
    monkeypatch.setattr(app._cl.messages, "stream", _stream)
    rec = app._PRODUCTS["products"]["thyroid-support"]
    for k, v in (("warning", THYROID_V7), ("chat_caution", True),
                 ("copy_pinned", ["directions"]), ("directions", THYROID_DIRECTIONS)):
        monkeypatch.setitem(rec, k, v)

    def _post(q, member=True):
        monkeypatch.setattr(app, "is_member", lambda *a, **k: member)
        sent.clear()
        r = app.app.test_client().post("/chat", json={"query": q, "level": "self-healing",
                                                      "mode": "brief"}, buffered=True)
        events = [json.loads(line[6:]) for line in r.get_data(as_text=True).splitlines()
                  if line.startswith("data: ") and line[6:].strip()]
        assert [e for e in events if e.get("done")], events[-3:]
        answer = "".join(e["token"] for e in events if e.get("token"))
        return answer, list(sent), any(e.get("gate") for e in events)
    return _post


def _warning(slug):
    return app._PRODUCTS["products"][slug]["warning"].strip()


def _assert_caution_and_no_dose(answer, sent, slug):
    warning = _warning(slug)
    directions = app._PRODUCTS["products"][slug]["directions"].strip()
    assert warning in answer
    assert directions not in answer
    # The v7 warning itself says "Each capsule ... the adult daily requirement", so dose
    # words are checked outside the quoted caution.
    rest = answer.replace(warning, " ").lower()
    assert "capsule" not in rest and "daily" not in rest
    assert sent == []


def test_member_graves_question_gets_the_caution_and_no_dose(post):
    answer, sent, gate = post(GRAVES)
    _assert_caution_and_no_dose(answer, sent, "thyroid-support")
    assert app._PRE_TERMS_CAUTION_INVITE not in answer and not gate


def test_member_safranal_pregnancy_question_gets_the_caution_and_no_dose(post):
    answer, sent, _ = post("Can I take Macular Wellness Safranal while pregnant?")
    _assert_caution_and_no_dose(answer, sent, "macular-wellness-safranal")
    assert app._PRE_TERMS_CAUTION_INVITE not in answer


def test_member_blood_cleanse_aspirin_question_quotes_the_caution(post):
    answer, sent, _ = post("Is Blood Cleanse safe with aspirin?")
    assert answer == MODEL_TEXT and len(sent) == 1
    msg = sent[0]
    assert app._CAUTION_QUOTE_LINE.format(name="Blood Cleanse") in msg
    assert f"Caution: {_warning('blood-cleanse')}" in msg
    assert app._CAUTION_LINK_LINE.split("{name}")[1][:60] not in msg
    assert "Do not quote" not in msg


def test_anonymous_graves_question_keeps_the_pre_terms_reply(post):
    answer, sent, gate = post(GRAVES, member=False)
    assert answer == (app._PRE_TERMS_CAUTION_LEAD.format(name="Thyroid Support") + "\n\n"
                      + THYROID_V7 + "\n\n" + app._PRE_TERMS_CAUTION_INVITE)
    assert sent == [] and gate


def test_a_member_non_safety_question_is_unchanged(post):
    answer, sent, _ = post("What is Blood Cleanse?")
    assert answer == MODEL_TEXT and len(sent) == 1
    assert app._CAUTION_QUOTE_LINE.format(name="Blood Cleanse") not in sent[0]


def test_the_member_caution_rules_swap_the_point_step_for_the_quote_step():
    assert app._CAUTION_ANSWER_POINT in app._CAUTION_ANSWER_RULES
    got = app._caution_synth_instr("Can I take Stress Release before surgery?", member=True)
    assert app._CAUTION_ANSWER_QUOTE in got and "Do not quote" not in got


# ---- the matcher -----------------------------------------------------------------------

@pytest.mark.parametrize("said", [
    "I am pregnant", "during pregnancy", "I could become pregnant", "I'm breastfeeding",
    "I am nursing my baby", "I have Graves' disease", "I have hyperthyroidism",
    "my overactive thyroid", "for my children", "she is under 18", "my 12 year old",
    "I have hormone-sensitive prostate cancer"])
def test_a_stated_situation_matches_thyroid_support(said):
    assert app._stated_do_not_use(THYROID_V7, said)


@pytest.mark.parametrize("said", [
    "Is it safe with aspirin?", "I take warfarin", "I work in a nursing home",
    "I have liver disease", "I have an underactive thyroid", "I am 40 years old"])
def test_other_situations_do_not_match_thyroid_support(said):
    # Liver disease is a consult-your-physician line there, not a do-not-use one, and
    # "Keep out of reach of children" is no do-not-use line either.
    assert not app._stated_do_not_use(THYROID_V7, said)


def test_a_do_not_use_if_you_have_condition_matches_by_its_own_words():
    assert app._stated_do_not_use("Do not use if you have liver disease.", "I have liver disease")
    assert app._stated_do_not_use("Do not use if you have a bleeding disorder or a history "
                                  "of bleeding.", "I have a history of bleeding")
    assert not app._stated_do_not_use("If you have liver disease, consult your physician "
                                      "before use.", "I have liver disease")


def test_keep_out_of_reach_of_children_is_no_do_not_use_line():
    warning = "Keep out of reach of children. Do not use if you are pregnant."
    assert not app._stated_do_not_use(warning, "Can my children take it?")
    assert app._stated_do_not_use(warning, "I am pregnant")


def test_an_avoid_use_line_counts_as_do_not_use():
    # Stress Release: "Avoid use in pregnancy or while breastfeeding."
    assert app._stated_do_not_use(_warning("stress-release"), "I am breastfeeding")
    assert not app._stated_do_not_use(_warning("stress-release"), "I take warfarin")
