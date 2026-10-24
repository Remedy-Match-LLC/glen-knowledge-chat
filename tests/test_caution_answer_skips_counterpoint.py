"""A caution question gets no consensus-and-assumption frame.

Production's check after #2127 (2026-10-24): asked "I have an operation next week. Is Stress
Release okay?", answers called stopping everything "a blanket approach" and asked whether
timing "changes the answer entirely", because the brief's beats credit a consensus, name
where it stops short and name its hidden assumption. Others gave "5-7 days" and leave to
restart. A lupus question behind the consent gate gave no caution. Glen said "yes" to never
arguing with a caution (production tab, 2026-10-24).
"""
from types import SimpleNamespace

import pytest

import app

INSTR = app._CAUTION_ANSWER_INSTR


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


@pytest.mark.parametrize("q", [
    "I have an operation next week. Is Stress Release okay?",
    "Can I take Stress Release before surgery?",
    "I have lupus. Can I take Stress Release?",
    "Can I take Vitamin D Syntropy if I am on warfarin?",
])
def test_a_caution_question_gets_the_caution_answer_instruction(q):
    assert app._caution_answer_instr(q) == INSTR


@pytest.mark.parametrize("q", [
    "How much Stress Release should I take?",
    "Can I take Clarity with my blood pressure medicine?",   # no caution to give
    "Is Vitamin D Syntropy ok before surgery?",               # its caution names no surgery
])
def test_every_other_question_keeps_its_instruction(q):
    assert app._caution_answer_instr(q) == ""


def test_the_instruction_lifts_the_frame_and_forbids_windows_and_restarts():
    for words in ("do not use the consensus, limitation, assumption and tease beats",
                  "Open with the caution, every sentence of it",
                  "Give no time to stop, pause or restart",
                  "give it in full even when you may not give individual guidance",
                  "Keep the word cap, the Sources line and the CTA directive line"):
        assert words in INSTR, words


def test_the_brief_chat_answer_carries_it_before_the_brief_instruction(prompt):
    try:
        app.app.test_client().post(
            "/chat", json={"query": "I have an operation next week. Is Stress Release okay?",
                           "level": "executive"}, buffered=True)
    except _Stop:
        pass
    msg = prompt[-1]
    assert msg.count(INSTR) == 1
    assert msg.index(INSTR) < msg.index("BRIEF — INCLUDE-AND-TRANSCEND OPEN LOOP")


def test_a_dose_question_in_chat_does_not(prompt):
    try:
        app.app.test_client().post(
            "/chat", json={"query": "How much Stress Release should I take?",
                           "level": "executive"}, buffered=True)
    except _Stop:
        pass
    assert INSTR not in prompt[-1]


def test_the_full_answer_carries_it_too(prompt):
    with pytest.raises(_Stop):
        app._generate_full_answer("Can I take Stress Release before surgery?", "executive")
    assert prompt[-1].count(INSTR) == 1


def test_a_failure_leaves_the_instruction_as_it_was(monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("facts builder broke")
    monkeypatch.setattr(app, "_caution_subjects_asked", _boom)
    assert app._caution_answer_instr("Can I take Stress Release before surgery?") == ""
