"""A caution question gets a caution answer in place of the consensus-and-assumption frame.

Production's check after #2127 (2026-10-24): asked "I have an operation next week. Is Stress
Release okay?", answers called stopping everything "a blanket approach" and asked whether
timing "changes the answer entirely", because the brief's beats credit a consensus, name
where it stops short and name its hidden assumption. Others gave "5-7 days" and leave to
restart. A lupus question behind the consent gate gave no caution. A prefix left the frame
standing after it (review round 1), so the caution answer replaces it. Glen said "yes" to
never arguing with a caution (production tab, 2026-10-24).
"""
from types import SimpleNamespace

import pytest

import app

BRIEF_FRAME = "BRIEF — INCLUDE-AND-TRANSCEND OPEN LOOP"
LONG_FRAME = "make breaking THAT assumption the central belief"
RULES = app._CAUTION_ANSWER_RULES
SURGERY = "I have an operation next week. Is Stress Release okay?"


class _Stop(Exception):
    pass


@pytest.fixture
def prompt(monkeypatch):
    """Stubs retrieval and the model client, recording the last user message sent. The
    client raises before any token, so no answer, log row or mail follows."""
    sent = []
    monkeypatch.setattr(app, "embed", lambda text: [0.0] * 1536)
    one = [SimpleNamespace(id="m0", score=0.8, metadata={"text": "calm", "_source_ns": "x"})]
    monkeypatch.setattr(app, "query_all_namespaces", lambda vec: one)
    monkeypatch.setattr(app, "build_context", lambda matches: ("snippet text", []))
    monkeypatch.setattr(app, "_is_gated_question", lambda *a, **k: False)

    def _grab(**kw):
        sent.append(kw["messages"][-1]["content"])
        raise _Stop()

    class _Msgs:
        stream = staticmethod(_grab)
        create = staticmethod(_grab)

    monkeypatch.setattr(app._cl, "messages", _Msgs(), raising=False)
    return sent


def _chat(q, **extra):
    try:
        app.app.test_client().post("/chat", json={"query": q, "level": "executive", **extra},
                                   buffered=True)
    except _Stop:
        pass


@pytest.mark.parametrize("q", [
    SURGERY,
    "Can I take Stress Release before surgery?",
    "I have lupus. Can I take Stress Release?",
    "Can I take Vitamin D Syntropy if I am on warfarin?",
    # Review round 1: other subjects five cautions name, and other words for surgery.
    "I have a procedure next week, can I use Stress Release?",
    "I am having my wisdom teeth pulled Friday. Stress Release ok?",
    "I broke my wrist. Can I take AngiogenX?",
    "Can I use Angiostasis during my period?",
    "Can my 8 year old take Sleep Syntropy?",
    "Can I have wine with Brain Cleanse?",
    "I have fatty liver. Is Brain Cleanse ok?",
    "I have a bleeding disorder, is Fibrosolve ok?",
    # Review round 2: misses.
    "I have an op on Monday. Is Stress Release ok?",
    "I take a drug for my heart. Can I take Stress Release?",
    "Can my 12 yo take Sleep Syntropy?",
    "Is Stress Release ok before my knee replacement?",
    "I am getting my wisdom teeth out. Is Stress Release ok?",
    "I have heavy periods. Can I use Angiostasis?",
    "I cut my hand badly. Can I take AngiogenX?",
    # Review round 3: misses.
    "I'm having a hysterectomy next month. Can I take Fibrosolve?",
    "I'm on chemotherapy. Can I take Angiostasis?",
    "Is Estro-Clear ok with birth control?",
])
def test_a_caution_question_gets_the_caution_answer(q):
    assert app._caution_synth_instr(q).startswith(RULES)


@pytest.mark.parametrize("q", [
    "How much Stress Release should I take?",
    "Can I take Clarity with my blood pressure medicine?",   # no caution to give
    "Is Vitamin D Syntropy ok before surgery?",               # its caution names no surgery
    "Can I buy Stress Release at the co-op?",                 # review round 1
    "Is Stress Release a drug?",                              # review round 1
    # Review round 2: word parts of ordinary questions.
    "Does Brain Cleanse help my kidneys?",
    "Sleep Syntropy dosage for an eighteen stone man?",
    "Is Sleep Syntropy good before a fifteen minute nap?",
    "Can Brain Cleanse help after a childhood trauma?",
    "Is Sleep Syntropy good for someone who babysits at night?",
    "What procedure should I follow to take AngiogenX?",
    "Is Stress Release a replacement for my sleeping aid?",
    # Review round 3.
    "Will AngiogenX help my broken capillaries?",
    "Is there a procedure to follow with Fibrosolve?",
])
def test_every_other_question_keeps_its_frame(q):
    assert app._caution_synth_instr(q) is None


def test_the_caution_answer_lifts_the_frame_and_forbids_windows_and_restarts():
    for words in ("no consensus, no limitation, no hidden assumption, no tease, and no "
                  "break-and-rebuild",
                  "1. Open with each caution that names what the user asks about, every "
                  "sentence of it",
                  "A caution's own words do not count toward any word limit",
                  "2. Then answer the rest of the question plainly",
                  "Answer about any other product as usual",
                  "give no time to stop, pause or restart",
                  "give it in full even when you may not give individual guidance",
                  "that one next step is the invitation to agree to them"):
        assert words in RULES, words


def test_the_brief_form_keeps_the_cap_and_the_cta_line_and_the_long_form_neither():
    brief = app._caution_synth_instr(SURGERY)
    assert "200 words or fewer" in brief and brief.endswith(app._CTA_DIRECTIVE_SPEC + "\n\n")
    long_ = app._caution_synth_instr(SURGERY, long_form=True)
    assert "⟦CTA⟧" not in long_ and long_.endswith("List sources at the end.\n\n")
    assert app._CTA_DIRECTIVE_SPEC in app._brief_synth_instruction()


def test_the_brief_chat_answer_replaces_the_frame(prompt):
    _chat(SURGERY)
    msg = prompt[-1]
    assert msg.count(RULES) == 1 and BRIEF_FRAME not in msg


@pytest.mark.parametrize("logged_in", [False, True])
def test_chat_s_full_mode_replaces_its_frame_too(logged_in):
    # /chat's full mode sits behind the email gate, so its choice is tested directly.
    got = app._chat_synth_instr(SURGERY, None, "full", logged_in)
    assert got.startswith(RULES) and got.endswith("List sources at the end.\n\n")
    assert app._chat_synth_instr("How much Stress Release should I take?", None, "full",
                                 logged_in) == app._long_form_synth_instr(logged_in)


def test_a_dose_question_in_chat_keeps_the_frame(prompt):
    _chat("How much Stress Release should I take?")
    assert RULES not in prompt[-1] and BRIEF_FRAME in prompt[-1]


def test_a_follow_up_in_chat_takes_the_product_from_its_history(prompt):
    _chat("Can I take it before surgery?",
          history=[{"role": "user", "content": "Tell me about Stress Release"}])
    assert RULES in prompt[-1]


def test_the_gated_chat_answer_keeps_the_caution_answer(prompt, monkeypatch):
    monkeypatch.setattr(app, "_is_gated_question", lambda *a, **k: True)
    monkeypatch.setattr(app, "is_member", lambda *a, **k: False)
    _chat("I have lupus. Can I take Stress Release?")
    msg = prompt[-1]
    assert msg.count(RULES) == 1 and BRIEF_FRAME not in msg
    assert app._PRODUCTS["products"]["stress-release"]["warning"] in msg


def test_the_full_answer_replaces_its_frame(prompt):
    with pytest.raises(_Stop):
        app._generate_full_answer("Can I take Stress Release before surgery?", "executive")
    assert prompt[-1].count(RULES) == 1 and LONG_FRAME not in prompt[-1]


def test_the_full_report_stream_replaces_its_frame(prompt):
    # The stream reports a model error as an event, so drain it and read the prompt.
    with app.app.test_request_context():
        resp = app._full_report_stream(1, "Can I take Stress Release before surgery?",
                                       "executive", "s1")
        list(resp.response)
    assert prompt[-1].count(RULES) == 1 and LONG_FRAME not in prompt[-1]


def test_a_failure_leaves_the_frame_as_it_was(monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("facts builder broke")
    monkeypatch.setattr(app, "_caution_subjects_asked", _boom)
    assert app._caution_synth_instr("Can I take Stress Release before surgery?") is None
