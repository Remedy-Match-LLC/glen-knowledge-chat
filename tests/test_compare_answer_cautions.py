"""A question that names two or more caution products gets each caution, written by code.

Knowledge read-back, 2026-11-26: "What is the difference between Nerve Pulse and Nerve
Repair?" carried both cautions in its facts block, and the answer gave neither. It also
invented uses. Glen, platform tab: "yes, fix the comparison answer".
"""
import importlib
import json
from types import SimpleNamespace

import pytest

import app

Q = "What is the difference between Nerve Pulse and Nerve Repair?"
LEAD = app._PRE_TERMS_CAUTION_LEAD


def _warning(slug):
    return app._PRODUCTS["products"][slug]["warning"].strip()


def _lead(slug):
    return LEAD.format(name=app._PRODUCTS["products"][slug]["name"].strip())


# ---- which questions -----------------------------------------------------------------

def test_two_caution_products_in_the_order_named():
    assert app._compare_caution_slugs(Q) == ["nerve-pulse", "nerve-repair"]
    assert app._compare_caution_slugs("Nerve Repair or Nerve Pulse?") == [
        "nerve-repair", "nerve-pulse"]


def test_one_product_is_not_a_comparison():
    assert app._compare_caution_slugs("What is Nerve Pulse?") == []
    assert app._compare_answer_rules("What is Nerve Pulse?") == ""
    assert app._compare_caution_tail("What is Nerve Pulse?", "Answer.") == ""


def test_a_product_with_no_caution_does_not_count(monkeypatch):
    products = dict(app._PRODUCTS["products"])
    nr = dict(products["nerve-repair"])
    nr["chat_caution"] = False
    products["nerve-repair"] = nr
    monkeypatch.setitem(app._PRODUCTS, "products", products)
    assert app._compare_caution_slugs(Q) == []
    assert app._compare_caution_tail(Q, "Answer.") == ""


def test_a_failure_gives_none(monkeypatch):
    monkeypatch.setattr(app, "_named_product_facts",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    assert app._compare_caution_slugs(Q) == []
    assert app._compare_caution_tail(Q, "Answer.") == ""
    assert app._compare_answer_rules(Q) == ""


# ---- what code writes ----------------------------------------------------------------

def test_the_tail_is_each_caution_word_for_word_in_order():
    tail = app._compare_caution_tail(Q, "Some answer.")
    assert tail == ("\n\n" + _lead("nerve-pulse") + "\n\n" + _warning("nerve-pulse")
                    + "\n\n" + _lead("nerve-repair") + "\n\n" + _warning("nerve-repair"))


def test_a_caution_already_given_word_for_word_is_not_repeated():
    said = "Answer.\n\n" + _warning("nerve-repair").replace(" ", "\n", 3)
    tail = app._compare_caution_tail(Q, said)
    assert _warning("nerve-pulse") in tail
    assert _warning("nerve-repair") not in tail and _lead("nerve-repair") not in tail
    both = "A. " + _warning("nerve-pulse") + " B. " + _warning("nerve-repair")
    assert app._compare_caution_tail(Q, both) == ""


def test_a_partial_caution_still_gets_the_full_one():
    half = _warning("nerve-pulse").split(". ")[0] + "."
    assert _warning("nerve-pulse") in app._compare_caution_tail(Q, half)


def test_the_rules_name_both_products_and_ban_added_claims():
    r = app._compare_answer_rules(Q)
    assert "The question names Nerve Pulse and Nerve Repair." in r
    assert "Skip any consensus" in r
    assert "never say anyone recommends taking them together" in r
    assert "Never say who a product is ideal or best for" in r
    assert "Code adds each caution after your answer, word for word." in r
    assert "including the Caution reminder" in r
    assert "keep any instruction to open with a sentence about a caution" in r
    assert "Answer directly and briefly." in r
    assert "Answer directly. Skip" in app._compare_answer_rules(Q, long_form=True)


# ---- the routes ----------------------------------------------------------------------

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
def chat(monkeypatch, tmp_path):
    """Posts to /chat with the model stubbed, and returns (streamed text, done event,
    the user message the model got). No outbound call."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    importlib.reload(app)
    monkeypatch.setattr(app._cl.messages, "create", lambda **kw: (_ for _ in ()).throw(
        RuntimeError("no live calls in tests")))
    monkeypatch.setattr(app, "embed", lambda text: [0.0] * 1536)
    one = [SimpleNamespace(id="m0", score=0.8, metadata={"text": "x", "_source_ns": "x"})]
    monkeypatch.setattr(app, "query_all_namespaces", lambda vec: one)
    monkeypatch.setattr(app, "build_context", lambda m: ("snippet text", []))
    monkeypatch.setattr(app, "_is_gated_question", lambda *a, **k: False)
    seen = {}

    def _stream(**kw):
        seen["user"] = kw["messages"][-1]["content"]
        return _FakeStream("Short answer.\n⟦CTA⟧ page | https://example.test/own | See")
    monkeypatch.setattr(app._cl.messages, "stream", _stream)

    def _post(q, member=False):
        monkeypatch.setattr(app, "is_member", lambda *a, **k: member)
        seen.pop("user", None)
        r = app.app.test_client().post("/chat", json={"query": q, "level": "self-healing",
                                                      "mode": "brief"}, buffered=True)
        events = [json.loads(line[6:]) for line in r.get_data(as_text=True).splitlines()
                  if line.startswith("data: ") and line[6:].strip()]
        done = [e for e in events if e.get("done")]
        assert done, events[-3:]
        text = "".join(e.get("token", "") for e in events)
        return text, done[-1], seen.get("user")
    return _post


def test_chat_streams_and_logs_both_cautions_after_the_answer(chat):
    text, done, user = chat(Q)
    tail = app._compare_caution_tail(Q, "Short answer.")
    assert tail and text.endswith(tail)
    assert text.index("Short answer.") < text.index(_warning("nerve-pulse"))
    assert app._compare_answer_rules(Q) in user
    assert user.rstrip().endswith(app._compare_answer_rules(Q).rstrip())
    assert done.get("cta", {}).get("target") == "https://example.test/own"
    import sqlite3  # the logged answer carries the cautions
    with sqlite3.connect(app.LOG_DB) as cx:
        logged = cx.execute("SELECT answer FROM query_log ORDER BY id DESC LIMIT 1").fetchone()[0]
    assert logged.endswith(_warning("nerve-repair"))


def test_one_product_chat_gets_no_tail_and_no_rules(chat):
    text, _done, user = chat("What is Nerve Pulse?")
    assert _warning("nerve-pulse") not in text
    assert "MORE THAN ONE PRODUCT WITH A CAUTION" not in user


def test_a_code_written_reply_gets_no_second_copy(chat):
    q = "Is Nerve Pulse or Nerve Repair safe while pregnant?"
    reply = app._pre_terms_caution_reply(q)
    assert reply, "this question should get the code-written reply before the Terms"
    text, _done, user = chat(q)
    assert user is None  # the model was never called
    assert text.count(_warning("nerve-repair")) == 1


def test_the_full_report_appends_the_tail(monkeypatch):
    monkeypatch.setattr(app, "embed", lambda text: [0.0] * 1536)
    one = [SimpleNamespace(id="m0", score=0.8, metadata={"text": "x", "_source_ns": "x"})]
    monkeypatch.setattr(app, "query_all_namespaces", lambda vec: one)
    monkeypatch.setattr(app, "build_context", lambda m: ("snippet text", []))
    seen = {}

    def _create(**kw):
        seen["user"] = kw["messages"][-1]["content"]
        return SimpleNamespace(content=[SimpleNamespace(text="Long answer.")])
    monkeypatch.setattr(app._cl.messages, "create", _create)
    answer, _src, _n = app._generate_full_answer(Q, "self-healing")
    assert answer == "Long answer." + app._compare_caution_tail(Q, "Long answer.")
    assert app._compare_answer_rules(Q, long_form=True) in seen["user"]


def test_the_full_report_stream_appends_the_tail(monkeypatch):
    monkeypatch.setattr(app, "embed", lambda text: [0.0] * 1536)
    one = [SimpleNamespace(id="m0", score=0.8, metadata={"text": "x", "_source_ns": "x"})]
    monkeypatch.setattr(app, "query_all_namespaces", lambda vec: one)
    monkeypatch.setattr(app, "build_context", lambda m: ("snippet text", []))
    monkeypatch.setattr(app._cl.messages, "stream", lambda **kw: _FakeStream("Long."))
    with app.app.test_request_context("/"):
        resp = app._full_report_stream(1, Q, "self-healing", "s1")
        body = "".join(c.decode() if isinstance(c, bytes) else c
                       for c in resp.response)
    toks = "".join(json.loads(line[6:]).get("token", "") for line in body.splitlines()
                   if line.startswith("data: ") and line[6:].strip())
    assert toks == "Long." + app._compare_caution_tail(Q, "Long.")


def test_a_code_written_reply_for_one_caution_stays_as_written(chat):
    # Before the Terms the code-written reply is the whole answer (Glen, 2026-10-31).
    # Here it covers Nerve Pulse only, so no tail may follow its Terms invitation.
    q = "Can I take Nerve Pulse or Nerve Repair with antidepressants?"
    reply = app._pre_terms_caution_reply(q)
    assert _warning("nerve-pulse") in reply and _warning("nerve-repair") not in reply
    text, _done, _user = chat(q)
    assert _warning("nerve-repair") not in text
    assert text.rstrip().endswith(app._PRE_TERMS_CAUTION_INVITE)


def test_no_caution_text_means_no_rule_to_leave_them_out(monkeypatch):
    # Review round 2: the model must never be told to leave the cautions out when code
    # could not build them.
    monkeypatch.setattr(app, "_verbatim_caution_paras",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    assert app._compare_cautions(Q) == []
    assert app._compare_answer_rules(Q) == ""
    assert app._compare_caution_tail(Q, "Answer.") == ""


def test_a_missing_paragraph_means_no_rule(monkeypatch):
    real = app._verbatim_caution_paras
    monkeypatch.setattr(app, "_verbatim_caution_paras",
                        lambda *a, **k: real(*a, **k)[:1])
    assert app._compare_cautions(Q) == []
    assert app._compare_answer_rules(Q) == ""


def test_the_rule_and_tail_fail_open(monkeypatch):
    bad = [(object(), "w", "p")]
    assert app._compare_answer_rules(Q, cautions=[(1, "w", "p"), (None, "v", "q")]) == ""
    assert app._compare_caution_tail(Q, "Answer.", cautions=bad + [None]) == ""


def test_a_bundle_is_not_two_named_products():
    # Review round 1: a bundle's component cautions join the facts block, but the
    # question named one product.
    for q in ("What is the Sleep Bundle?", "Is Brain Cleanse in the Sleep Bundle?"):
        assert app._compare_caution_slugs(q) == [], q
        assert app._compare_answer_rules(q) == "", q


def test_the_full_report_keeps_its_length(monkeypatch):
    monkeypatch.setattr(app, "embed", lambda text: [0.0] * 1536)
    one = [SimpleNamespace(id="m0", score=0.8, metadata={"text": "x", "_source_ns": "x"})]
    monkeypatch.setattr(app, "query_all_namespaces", lambda vec: one)
    monkeypatch.setattr(app, "build_context", lambda m: ("snippet text", []))
    seen = {}

    def _create(**kw):
        seen["user"] = kw["messages"][-1]["content"]
        return SimpleNamespace(content=[SimpleNamespace(text="Long answer.")])
    monkeypatch.setattr(app._cl.messages, "create", _create)
    app._generate_full_answer(Q, "self-healing")
    assert "Answer directly. Skip" in seen["user"]
    assert "and briefly" not in seen["user"].split("MORE THAN ONE PRODUCT")[1]
