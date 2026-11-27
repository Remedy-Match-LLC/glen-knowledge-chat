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


OPEN = "The label directions for Aller-Free HomeoEnergetic Drops are: "


def test_a_dose_question_gets_the_directions_sentence_from_code():
    assert _dirs() == "10 drops 3 times a day or as needed, or as guided."
    assert app._directions_only_opening(Q) == OPEN + _dirs()
    b = app._directions_only_block(Q)
    assert ('Your answer already opens with this sentence, written for you: "'
            + OPEN + _dirs() + '" Do not write it again.') in b
    for ban in ("no dilution", "no succussion", "no opening a capsule", "no water",
                "no doubling", "Name no water, device or formula",
                "do not tell the client to adjust the dose to how they feel",
                "Do not state the Directions again", "do not recommend it"):
        assert ban in b, ban
    # Glen's standing rule: AllerFree goes to Immune Modulation (review round 3).
    assert 'no product except one the user named and "Immune Modulation"' in b
    assert "give its product page link" in b


def test_without_the_opening_the_directions_stay_word_for_word():
    b = app._directions_only_block("What is AllerFree?")
    assert ('Give these Directions word for word whenever you say how to take it, '
            'keeping "or as guided".') in b
    assert "Do not state the Directions again" not in b


@pytest.mark.parametrize("q", ["When should I take AllerFree?", "How is AllerFree taken?",
                               "Can I take 20 drops of AllerFree at once?",
                               "AllerFree dosage", "How many drops of Aller-Free a day?"])
def test_dose_questions_get_the_sentence(q):
    assert app._directions_only_opening(q) == OPEN + _dirs(), q


def test_the_directions_come_from_the_record(monkeypatch):
    rec = dict(app._PRODUCTS["products"][SLUG], directions="5 drops daily.")
    monkeypatch.setitem(app._PRODUCTS["products"], SLUG, rec)
    assert app._directions_only_opening(Q) == OPEN + "5 drops daily."
    b = app._directions_only_block(Q)
    assert "5 drops daily." in b and "or as guided" not in b and "10 drops" not in b
    b2 = app._directions_only_block("What is AllerFree?")
    assert "5 drops daily." in b2 and 'keeping "or as guided"' not in b2


@pytest.mark.parametrize("q", ["What is AllerFree?", "allerfree dosage",
                               "Tell me about Aller Free drops", "aller‑free?"])
def test_any_spelling_of_the_name(q):
    assert app._directions_only_block(q)


@pytest.mark.parametrize("q", ["What is Aller-Free HomeoEnergetic Drops?",
                               "How does AllerFree work?", "Is AllerFree vegan?",
                               "How is AllerFree different from Immune Modulation?",
                               "How can AllerFree work so gently?",
                               "How do I take Immune Modulation and AllerFree together?"])
def test_not_dose_questions_get_no_sentence(q):
    assert app._directions_only_block(q)
    assert app._directions_only_opening(q) == "", q


@pytest.mark.parametrize("q", ["I have allergies, what helps?", "Is it allergy-free?",
                               "What is Immune Modulation?", "gluten free options"])
def test_no_name_no_block(q):
    assert app._directions_only_block(q) == ""


def test_not_a_dose_question_has_no_opening():
    assert app._directions_only_opening("What is AllerFree?") == ""
    b = app._directions_only_block("What is AllerFree?")
    assert b and "already opens" not in b and _dirs() in b


def test_a_follow_up_carries_it():
    # Carried: the rule, never the code-written sentence (review round 1).
    assert app._directions_only_opening("How do I take it?", ["What is AllerFree?"]) == ""
    b = app._directions_only_block("How do I take it?", ["What is AllerFree?"])
    assert b and 'keeping "or as guided"' in b
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


def _no_model(monkeypatch):
    def _boom(**kw):
        raise AssertionError("the model must not be called for a code-written reply")
    monkeypatch.setattr(app._cl.messages, "create", _boom)
    monkeypatch.setattr(app._cl.messages, "stream", _boom)


def test_chat_writes_the_whole_reply(monkeypatch, tmp_path):
    # Glen, platform tab, 2026-11-26: "yes, code writes both answers".
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    importlib.reload(app)
    monkeypatch.setattr(app, "embed", lambda text: [0.0] * 1536)
    one = [SimpleNamespace(id="m0", score=0.8, metadata={"text": "x", "_source_ns": "x"})]
    monkeypatch.setattr(app, "query_all_namespaces", lambda vec: one)
    monkeypatch.setattr(app, "build_context", lambda m: ("snippet text", []))
    monkeypatch.setattr(app, "_is_gated_question", lambda *a, **k: False)
    monkeypatch.setattr(app, "is_member", lambda *a, **k: False)
    _no_model(monkeypatch)
    r = app.app.test_client().post("/chat", json={"query": Q, "level": "self-healing",
                                                  "mode": "brief"}, buffered=True)
    events = [json.loads(line[6:]) for line in r.get_data(as_text=True).splitlines()
              if line.startswith("data: ") and line[6:].strip()]
    text = "".join(e.get("token", "") for e in events)
    reply = app._coded_directions_reply(Q)
    assert reply.startswith(OPEN + _dirs())
    assert text == reply
    done = [e for e in events if e.get("done")][-1]
    assert done["cta"]["target"] == app._catalog_page_url(SLUG)
    import sqlite3
    with sqlite3.connect(app.LOG_DB) as cx:
        logged = cx.execute("SELECT answer FROM query_log ORDER BY id DESC LIMIT 1").fetchone()[0]
    assert logged == reply


def test_full_report_is_the_code_written_reply(monkeypatch):
    monkeypatch.setattr(app, "embed", lambda text: [0.0] * 1536)
    one = [SimpleNamespace(id="m0", score=0.8, metadata={"text": "x", "_source_ns": "x"})]
    monkeypatch.setattr(app, "query_all_namespaces", lambda vec: one)
    monkeypatch.setattr(app, "build_context", lambda m: ("snippet text", []))
    _no_model(monkeypatch)
    answer, _s, _n = app._generate_full_answer(Q, "self-healing")
    assert answer == app._coded_directions_reply(Q)
    with app.app.test_request_context("/"):
        body = "".join(c.decode() if isinstance(c, bytes) else c
                       for c in app._full_report_stream(1, Q, "self-healing", "s1").response)
    events = [json.loads(line[6:]) for line in body.splitlines()
              if line.startswith("data: ") and line[6:].strip()]
    assert "".join(e.get("token", "") for e in events) == app._coded_directions_reply(Q)
    assert events[-1].get("done")


def test_an_inactive_or_unnamed_record_gives_none(monkeypatch):
    rec = dict(app._PRODUCTS["products"][SLUG])
    monkeypatch.setitem(app._PRODUCTS["products"], SLUG, dict(rec, inactive=True))
    assert app._directions_only_block(Q) == ""
    monkeypatch.setitem(app._PRODUCTS["products"], SLUG, dict(rec, name=""))
    assert app._directions_only_block(Q) == ""
