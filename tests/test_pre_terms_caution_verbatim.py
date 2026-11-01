"""Before the Terms, a safety question about a named product gets its caution verbatim.

Glen, 2026-10-31 (relayed): when a chat visitor names a product and asks a safety
question, the chat quotes that product's full caution word for word, whether or not the
visitor has agreed to the Terms. Before they agree it adds no commentary or personal
application of its own. Log 2616 ("Is Blood Cleanse safe while breastfeeding?",
anonymous) only said that a caution applied, then asked for a name and the Terms.
"""
import importlib
import json
from types import SimpleNamespace

import pytest

import app

SAFETY = "Is Blood Cleanse safe while breastfeeding?"
MODEL_TEXT = "Model answer with an invented mechanism."


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
    """Posts to /chat with the model stubbed, and returns (answer, done, streamed).
    Own data dir, no outbound call (as tests/test_caution_link.py does)."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    importlib.reload(app)
    monkeypatch.setattr(app._cl.messages, "create", lambda **kw: (_ for _ in ()).throw(
        RuntimeError("no live calls in tests")))
    monkeypatch.setattr(app, "embed", lambda text: [0.0] * 1536)
    one = [SimpleNamespace(id="m0", score=0.8, metadata={"text": "x", "_source_ns": "x"})]
    monkeypatch.setattr(app, "query_all_namespaces", lambda vec: one)
    monkeypatch.setattr(app, "build_context", lambda m: ("snippet text", []))
    monkeypatch.setattr(app, "_is_gated_question", lambda *a, **k: True)
    calls = []

    def _stream(**kw):
        calls.append(kw)
        return _FakeStream(MODEL_TEXT)
    monkeypatch.setattr(app._cl.messages, "stream", _stream)

    def _post(q, member=False):
        monkeypatch.setattr(app, "is_member", lambda *a, **k: member)
        calls.clear()
        r = app.app.test_client().post("/chat", json={"query": q, "level": "self-healing",
                                                      "mode": "brief"}, buffered=True)
        events = [json.loads(line[6:]) for line in r.get_data(as_text=True).splitlines()
                  if line.startswith("data: ") and line[6:].strip()]
        done = [e for e in events if e.get("done")]
        assert done, events[-3:]
        answer = "".join(e["token"] for e in events if e.get("token"))
        return answer, done[-1], bool(calls), any(e.get("gate") for e in events)
    return _post


def _warning(slug="blood-cleanse"):
    return app._PRODUCTS["products"][slug]["warning"].strip()


def test_anonymous_blood_cleanse_breastfeeding_gets_the_caution_verbatim(post):
    answer, done, streamed, gate = post(SAFETY)
    assert _warning() in answer
    # Code writes the whole reply: the caution and the Terms invitation, nothing else.
    assert answer == (app._PRE_TERMS_CAUTION_LEAD.format(name="Blood Cleanse") + "\n\n"
                      + _warning() + "\n\n" + app._PRE_TERMS_CAUTION_INVITE)
    assert not streamed and MODEL_TEXT not in answer
    assert gate
    assert done["next_question"] == ""
    assert done["cta"]["target"].endswith("/blood-cleanse#caution")


def test_an_anonymous_non_safety_question_is_unchanged(post):
    answer, done, streamed, _ = post("What is Blood Cleanse?")
    assert streamed and answer == MODEL_TEXT


def test_after_the_terms_the_answer_is_unchanged(post):
    # Caution v2 says "consult" on aspirin, not "do not use", so the model answers.
    answer, done, streamed, _ = post("Is Blood Cleanse safe with aspirin?", member=True)
    assert streamed and answer == MODEL_TEXT


def test_after_the_terms_breastfeeding_gets_the_caution_and_no_dose(post):
    # Caution v2 says "Do not use if you ... are breastfeeding".
    answer, done, streamed, _ = post(SAFETY, member=True)
    assert not streamed and "breastfeeding" in answer and "capsules" not in answer


def test_a_product_without_the_chat_opt_in_is_unchanged(post, monkeypatch):
    monkeypatch.setitem(app._PRODUCTS["products"]["blood-cleanse"], "chat_caution", False)
    answer, done, streamed, _ = post(SAFETY)
    assert streamed and answer == MODEL_TEXT


def test_a_pinned_product_with_a_caution_but_no_chat_opt_in_is_unchanged(post):
    # Lipid Cleanse is named by the facts builder and has a warning, but no opt-in.
    pr = app._PRODUCTS["products"]["lipid-cleanse"]
    assert pr.get("warning") and not pr.get("chat_caution")
    answer, done, streamed, _ = post("Is Lipid Cleanse safe while breastfeeding?")
    assert streamed and answer == MODEL_TEXT


def test_the_reply_builder_gives_nothing_without_a_caution_question():
    assert app._pre_terms_caution_reply("What is Blood Cleanse?") == ""
    assert _warning() in app._pre_terms_caution_reply(SAFETY)


def test_anonymous_pregnancy_question_gets_the_caution_and_no_dose(post):
    # Live chat 2622 (anonymous): "Can I take Macular Wellness Safranal while pregnant?"
    # said a caution applies, then gave "1 to 3 capsules daily ..." to a pregnant visitor.
    pr = app._PRODUCTS["products"]["macular-wellness-safranal"]
    directions = pr["directions"].strip()
    assert pr.get("chat_caution") and directions
    answer, done, streamed, _ = post("Can I take Macular Wellness Safranal while pregnant?")
    assert _warning("macular-wellness-safranal") in answer
    assert directions not in answer
    assert not any(w in answer.lower() for w in ("capsule", "daily"))
    assert not streamed


def test_lines_are_the_approved_wording():
    # Glen approved these two lines verbatim on 2026-11-01. A reword needs his yes.
    assert app._PRE_TERMS_CAUTION_LEAD == "The caution on the product page for {name} is:"
    assert app._PRE_TERMS_CAUTION_INVITE == (
        "If you'd like guidance for your own situation, please share your first and "
        "last name and agree to the Terms.")
