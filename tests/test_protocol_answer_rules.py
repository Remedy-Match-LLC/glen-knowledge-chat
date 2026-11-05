"""An answer from one of Glen's protocols keeps to the protocol's wording.

Knowledge's check on 2026-11-05 (chats 2709-2711, the earthing protocol): the consensus /
limitation / assumption beats were filled with invented timings, invented reasons, a
health claim no source makes, and barefoot walking for someone with reduced feeling in
the feet. Glen "yes" (platform tab): drop that frame and answer from the protocol."""
import ast
import inspect
from types import SimpleNamespace

import pytest

import app

BRIEF_FRAME = "BRIEF — INCLUDE-AND-TRANSCEND OPEN LOOP"
RULES = app._PROTOCOL_ANSWER_RULES
PROTOCOL = [{"name": "Earthing protocol", "field": "clinical-protocol", "score": 0.9}]
OTHER = [{"name": "A book", "field": "book", "score": 0.9}]
SHOWER = "How much Himalayan salt in the shower for earthing?"


class _Stop(Exception):
    pass


class _Sent(list):
    pass


@pytest.fixture
def prompt(monkeypatch):
    """Retrieval and the model are stubbed; the client raises before any token, so no
    answer, log row or mail follows. Records the last user message sent."""
    sent, sources = _Sent(), {"list": PROTOCOL}
    monkeypatch.setattr(app, "embed", lambda text: [0.0] * 1536)
    one = [SimpleNamespace(id="protocol:earthing:chunk:0000", score=0.8,
                           metadata={"text": "earthing", "_source_ns": "glen-authored-works"})]
    monkeypatch.setattr(app, "query_all_namespaces", lambda vec: one)
    monkeypatch.setattr(app, "build_context", lambda matches: ("snippet text", sources["list"]))
    monkeypatch.setattr(app, "_is_gated_question", lambda *a, **k: False)

    def _grab(**kw):
        sent.append(kw["messages"][-1]["content"])
        raise _Stop()

    class _Msgs:
        stream = staticmethod(_grab)
        create = staticmethod(_grab)

    monkeypatch.setattr(app._cl, "messages", _Msgs(), raising=False)
    sent.sources = sources
    return sent


def _chat(q):
    try:
        app.app.test_client().post("/chat", json={"query": q, "level": "executive"},
                                   buffered=True)
    except _Stop:
        pass


def test_the_rules_keep_wording_and_forbid_carrying_numbers_and_claims():
    for phrase in ("no consensus", "no hidden assumption", "no tease",
                   "'up to' stays 'up to'", "'may' stays 'may'",
                   "applies to that method only", "Never carry a number across",
                   "never suggest to them the method it advises against",
                   "Add no benefit, mechanism, reason, timing, dose-response or health claim",
                   # Glen, 2026-11-05: "Don't people with neuropathy take baths and
                   # showers?" A restriction on bare feet outdoors is not one on the shower.
                   "covers only the method and situation the protocol names",
                   "never extend it to a method it does not name",
                   "including the system prompt's 5-beat lead",
                   "answer that part from the other sources",
                   "is always given, plainly",
                   "its extended and long formats",
                   "does not cover a bath or a shower",
                   "keep every warning and cut steps"):
        assert phrase in RULES, phrase


def test_a_protocol_source_takes_the_protocol_answer():
    got = app._protocol_synth_instr(PROTOCOL)
    assert got.startswith(RULES) and app._CTA_DIRECTIVE_SPEC in got and "200 words" in got
    assert BRIEF_FRAME not in got
    long_form = app._protocol_synth_instr(PROTOCOL, long_form=True)
    assert long_form.startswith(RULES) and long_form.endswith("List sources at the end.\n\n")


@pytest.mark.parametrize("sources", [None, [], OTHER, [{"name": "x"}], [None], "junk"])
def test_no_protocol_source_means_no_protocol_answer(sources):
    assert app._protocol_synth_instr(sources) is None


def test_the_brief_chat_answer_uses_it_when_a_protocol_is_retrieved(prompt):
    _chat(SHOWER)
    assert prompt and RULES in prompt[-1] and BRIEF_FRAME not in prompt[-1]


def test_the_brief_chat_answer_keeps_its_frame_otherwise(prompt):
    prompt.sources["list"] = OTHER
    _chat(SHOWER)
    assert prompt and RULES not in prompt[-1] and BRIEF_FRAME in prompt[-1]


def test_a_caution_question_still_takes_the_caution_answer():
    q = "I have an operation next week. Is Stress Release okay?"
    got = app._chat_synth_instr(q, None, "brief", False, sources_list=PROTOCOL)
    assert got.startswith(app._CAUTION_ANSWER_RULES)


def test_full_mode_uses_it_too():
    assert app._chat_synth_instr(SHOWER, None, "full", True, sources_list=PROTOCOL
                                 ).startswith(RULES)
    assert app._chat_synth_instr(SHOWER, None, "full", True, sources_list=OTHER
                                 ) == app._long_form_synth_instr(True)


def _calls_in(fn_name, callee):
    tree = ast.parse(inspect.getsource(app))
    fns = [f for f in ast.walk(tree)
           if isinstance(f, (ast.FunctionDef, ast.AsyncFunctionDef)) and f.name == fn_name]
    assert len(fns) == 1, fn_name
    return [n for n in ast.walk(fns[0]) if isinstance(n, ast.Call)
            and getattr(n.func, "id", "") == callee]


def test_chat_passes_its_sources():
    calls = _calls_in("chat", "_chat_synth_instr")
    assert calls and all(any(k.arg == "sources_list" and getattr(k.value, "id", "") ==
                             "sources_list" for k in c.keywords) for c in calls)


@pytest.mark.parametrize("fn", ["_generate_full_answer", "_full_report_stream"])
def test_each_full_report_path_offers_the_protocol_answer(fn):
    """Read from each function's own code (review round 2: a global count could pass
    with one path missing)."""
    calls = _calls_in(fn, "_protocol_synth_instr")
    assert len(calls) == 1 and getattr(calls[0].args[0], "id", "") == "sources_list"


def test_a_malformed_source_does_not_hide_a_protocol_after_it():
    assert app._has_protocol_source([{"field": "book"}, "bad entry", None,
                                     {"field": "clinical-protocol"}])


def test_real_protocol_metadata_reaches_the_rule():
    """build_context itself maps the protocol's source_type to field (review round 2)."""
    meta = {"title": "Earthing protocol", "source": "Glen's clinical protocol",
            "source_type": "clinical-protocol", "text": "Plug the drain before showering.",
            "_source_ns": "glen-authored-works"}
    m = SimpleNamespace(id="protocol:earthing:chunk:0000", score=0.8, metadata=meta)
    context, sources = app.build_context([m])
    assert "Plug the drain" in context
    assert app._protocol_synth_instr(sources).startswith(RULES)


def test_a_caution_answer_that_cites_a_protocol_keeps_the_wording_rules():
    q = "I have an operation next week. Is Stress Release okay?"
    got = app._chat_synth_instr(q, None, "brief", False, sources_list=PROTOCOL)
    assert got.startswith(app._CAUTION_ANSWER_RULES) and app._PROTOCOL_STEPS in got
    plain = app._chat_synth_instr(q, None, "brief", False, sources_list=OTHER)
    assert plain.startswith(app._CAUTION_ANSWER_RULES) and app._PROTOCOL_STEPS not in plain


@pytest.mark.parametrize("fn", ["_generate_full_answer", "_full_report_stream"])
def test_each_full_report_caution_path_keeps_the_wording_rules(fn):
    calls = _calls_in(fn, "_with_protocol_steps")
    assert len(calls) == 1 and getattr(calls[0].args[1], "id", "") == "sources_list"
