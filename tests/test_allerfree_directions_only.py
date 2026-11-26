"""A client who names AllerFree gets its Directions, and nothing else on how to take it.

Production read-back, 2026-11-26: 1 of 8 answers to "How do I take Aller-Free
HomeoEnergetic Drops?" gave the approved Directions. Others added a capsule dilution,
doubled the dose, or suggested Living Water. Glen, platform tab: "yes, fix the AllerFree
directions". AllerFree stays never recommended.
"""
import importlib
import json
from types import SimpleNamespace

import pytest

import app
from dashboard.related_products import DO_NOT_RECOMMEND

Q = "How do I take Aller-Free HomeoEnergetic Drops?"
SLUG = "allerfree-homeoenergetic-drops"


def _dirs():
    return app._PRODUCTS["products"][SLUG]["directions"].strip()


def test_still_never_recommended():
    assert SLUG in DO_NOT_RECOMMEND
    assert app._named_facts_survivor(SLUG, app._PRODUCTS["products"]) is None
    assert app._named_product_spans(Q, app._PRODUCTS["products"]) == {}


def test_a_dose_question_opens_with_the_directions_word_for_word():
    b = app._directions_only_block(Q)
    assert _dirs() == "10 drops 3 times a day or as needed, or as guided."
    assert ('Open the answer with this sentence, word for word: "The label directions for '
            'Aller-Free HomeoEnergetic Drops are: ' + _dirs() + '"') in b
    for ban in ("no dilution", "no succussion", "no opening a capsule", "no water",
                "no doubling", "Name no other product, water, device or formula",
                "never suggest it to anyone", 'keeping "or as guided"'):
        assert ban in b, ban


@pytest.mark.parametrize("q", ["What is AllerFree?", "allerfree dosage",
                               "Tell me about Aller Free drops", "aller‑free?"])
def test_any_spelling_of_the_name(q):
    assert app._directions_only_block(q)


@pytest.mark.parametrize("q", ["I have allergies, what helps?", "Is it allergy-free?",
                               "What is Immune Modulation?", "gluten free options"])
def test_no_name_no_block(q):
    assert app._directions_only_block(q) == ""


def test_not_a_dose_question_has_no_opening():
    b = app._directions_only_block("What is AllerFree?")
    assert b and "Open the answer" not in b and _dirs() in b


def test_a_follow_up_carries_it():
    assert "Open the answer" in app._directions_only_block("How do I take it?",
                                                           ["What is AllerFree?"])
    assert app._directions_only_block("How do I take it?", ["What is Vitamin C?"]) == ""


def test_fails_open(monkeypatch):
    monkeypatch.setattr(app, "_PRODUCTS", {"products": {SLUG: {"name": object()}}})
    assert app._directions_only_block(Q) == ""
    monkeypatch.setattr(app, "_PRODUCTS", {"products": {SLUG: {"name": "X"}}})
    assert app._directions_only_block(Q) == ""


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


def test_chat_gives_the_model_the_block(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    importlib.reload(app)
    monkeypatch.setattr(app._cl.messages, "create", lambda **kw: (_ for _ in ()).throw(
        RuntimeError("no live calls in tests")))
    monkeypatch.setattr(app, "embed", lambda text: [0.0] * 1536)
    one = [SimpleNamespace(id="m0", score=0.8, metadata={"text": "x", "_source_ns": "x"})]
    monkeypatch.setattr(app, "query_all_namespaces", lambda vec: one)
    monkeypatch.setattr(app, "build_context", lambda m: ("snippet text", []))
    monkeypatch.setattr(app, "_is_gated_question", lambda *a, **k: False)
    monkeypatch.setattr(app, "is_member", lambda *a, **k: False)
    seen = {}

    def _stream(**kw):
        seen["user"] = kw["messages"][-1]["content"]
        return _FakeStream("Answer.")
    monkeypatch.setattr(app._cl.messages, "stream", _stream)
    r = app.app.test_client().post("/chat", json={"query": Q, "level": "self-healing",
                                                  "mode": "brief"}, buffered=True)
    r.get_data()
    assert seen["user"].rstrip().endswith(app._directions_only_block(Q).rstrip())


def test_full_report_gives_the_model_the_block(monkeypatch):
    monkeypatch.setattr(app, "embed", lambda text: [0.0] * 1536)
    one = [SimpleNamespace(id="m0", score=0.8, metadata={"text": "x", "_source_ns": "x"})]
    monkeypatch.setattr(app, "query_all_namespaces", lambda vec: one)
    monkeypatch.setattr(app, "build_context", lambda m: ("snippet text", []))
    seen = {}

    def _create(**kw):
        seen["user"] = kw["messages"][-1]["content"]
        return SimpleNamespace(content=[SimpleNamespace(text="Long.")])
    monkeypatch.setattr(app._cl.messages, "create", _create)
    app._generate_full_answer(Q, "self-healing")
    assert app._directions_only_block(Q) in seen["user"]
    monkeypatch.setattr(app._cl.messages, "stream", lambda **kw: (
        seen.__setitem__("s", kw["messages"][-1]["content"]) or _FakeStream("L.")))
    with app.app.test_request_context("/"):
        "".join(c.decode() if isinstance(c, bytes) else c
                for c in app._full_report_stream(1, Q, "self-healing", "s1").response)
    assert app._directions_only_block(Q) in seen["s"]


def test_an_inactive_or_unnamed_record_gives_none(monkeypatch):
    rec = dict(app._PRODUCTS["products"][SLUG])
    monkeypatch.setitem(app._PRODUCTS["products"], SLUG, dict(rec, inactive=True))
    assert app._directions_only_block(Q) == ""
    monkeypatch.setitem(app._PRODUCTS["products"], SLUG, dict(rec, name=""))
    assert app._directions_only_block(Q) == ""
