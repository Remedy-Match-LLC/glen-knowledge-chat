"""A question that touches a caution gets a link to it, never a copy of it.

Glen, platform tab, 2026-10-24: "If the question touches on a caution, open the caution
panel and in the answer also give the link to it at the bottom of the page", then "Chat
doesn't duplicate the caution. It gives a reply that ends with a link that scrolls and
opens it." Production's check after #2128: 2 of 24 surgery answers quoted only the
surgery sentences of Stress Release's five-sentence caution.
"""
import importlib
import json
import os
import re
from types import SimpleNamespace

import pytest

import app

SURGERY = "Can I take Stress Release before surgery?"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _warning(slug):
    return app._PRODUCTS["products"][slug]["warning"]


def _link(name, where=None):
    return app._CAUTION_LINK_LINE.format(name=name, where=where or app._CAUTION_LINK_WHERE)


# ---- which cautions a question touches ----------------------------------------------

@pytest.mark.parametrize("q", [SURGERY, "I have lupus. Can I take Stress Release?",
                               "Is Stress Release safe?",
                               "Does Stress Release have side effects?",
                               "Any cautions for Stress Release?"])
def test_a_subject_or_safety_question_links_the_caution(q):
    assert app._caution_link_slugs(q) == ["stress-release"]


@pytest.mark.parametrize("q", ["How much Stress Release should I take?",
                               "What is Stress Release?",
                               "Is Stress Release unsafe to buy online?",
                               "Is Stress Release a safeguard for my nerves?"])
def test_other_questions_link_nothing(q):
    assert app._caution_link_slugs(q) == []


def test_a_caution_without_the_chat_opt_in_is_never_linked(monkeypatch):
    monkeypatch.setitem(app._PRODUCTS["products"]["stress-release"], "chat_caution", False)
    assert app._caution_link_slugs(SURGERY) == []


def test_a_failure_links_nothing(monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("facts builder broke")
    monkeypatch.setattr(app, "_named_product_facts", _boom)
    assert app._caution_link_slugs(SURGERY) == []


# ---- what the bot is given -----------------------------------------------------------

def test_the_surgery_block_points_to_the_caution_and_never_carries_it():
    b = app.named_product_facts_block(SURGERY)
    assert b.count(_warning("stress-release")) == 0
    assert b.count(_link("Stress Release")) == 1
    assert app._CAUTION_STAY_LINE not in b and app._CAUTION_CLOSING_REMINDER not in b
    assert app._CAUTION_COPY_REMINDER not in b


def test_the_long_form_writes_the_link_itself():
    b = app.named_product_facts_block(SURGERY, long_form=True)
    url = app._caution_link_url("stress-release")
    assert "Read the full caution: " + url in b
    assert b.count(_link("Stress Release", app._CAUTION_LINK_WHERE_LONG.format(url=url))) == 1
    assert b.count(_warning("stress-release")) == 0


def test_a_caution_held_inside_the_description_leaves_it_too():
    # Fibrosolve's caution sits inside its approved description (2026-10-21).
    q = "Can I take Fibrosolve with warfarin?"
    assert app._caution_link_slugs(q) == ["fibrosolve"]
    b = app.named_product_facts_block(q)
    assert b.count(_warning("fibrosolve")) == 0 and b.count(_link("Fibrosolve")) == 1


def test_a_dose_question_still_copies_the_caution():
    b = app.named_product_facts_block("How much Stress Release should I take?")
    assert b.count(_warning("stress-release")) == 1 and _link("Stress Release") not in b


# ---- the button ----------------------------------------------------------------------

def test_the_button_opens_the_caution_on_the_product_page():
    cta = app._caution_link_cta(SURGERY)
    assert cta == {"type": "page",
                   "target": app._catalog_page_url("stress-release") + "#caution",
                   "label": "Read the full caution for Stress Release"}
    assert cta["target"].endswith("/begin/product/stress-release#caution")
    assert app._caution_link_cta("How much Stress Release should I take?") is None


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
def chat_done(monkeypatch, tmp_path):
    """Posts to /chat with the model stubbed to a short answer whose own button points
    elsewhere, and returns the done event. Own data dir, no outbound call (as
    tests/test_named_product_facts.py does)."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    importlib.reload(app)
    monkeypatch.setattr(app._cl.messages, "create", lambda **kw: (_ for _ in ()).throw(
        RuntimeError("no live calls in tests")))
    monkeypatch.setattr(app, "embed", lambda text: [0.0] * 1536)
    one = [SimpleNamespace(id="m0", score=0.8, metadata={"text": "calm", "_source_ns": "x"})]
    monkeypatch.setattr(app, "query_all_namespaces", lambda vec: one)
    monkeypatch.setattr(app, "build_context", lambda m: ("snippet text", []))
    monkeypatch.setattr(app, "is_member", lambda *a, **k: False)
    monkeypatch.setattr(app, "_is_gated_question", lambda *a, **k: False)
    monkeypatch.setattr(app._cl.messages, "stream", lambda **kw: _FakeStream(
        "Short answer.\n⟦CTA⟧ page | https://example.test/own | See the page"))

    def _post(q):
        r = app.app.test_client().post("/chat", json={"query": q, "level": "self-healing",
                                                      "mode": "brief"}, buffered=True)
        events = [json.loads(line[6:]) for line in r.get_data(as_text=True).splitlines()
                  if line.startswith("data: ") and line[6:].strip()]
        done = [e for e in events if e.get("done")]
        assert done, events[-3:]
        return done[-1]
    return _post


def test_the_chat_answer_ends_with_the_caution_button(chat_done):
    cta = chat_done(SURGERY)["cta"]
    assert cta["type"] == "page" and cta["target"].endswith("/stress-release#caution")
    assert cta["label"] == "Read the full caution for Stress Release"


def test_any_other_answer_keeps_its_own_button(chat_done):
    cta = chat_done("How much Stress Release should I take?")["cta"]
    assert cta["target"] == "https://example.test/own"


# ---- the product page ----------------------------------------------------------------

@pytest.fixture
def page(monkeypatch):
    app.app.config["TESTING"] = True
    monkeypatch.setattr(app, "_product_card",
                        lambda p: {"description": "", "ingredients": [], "benefits": []})
    monkeypatch.setattr(app, "_product_how", lambda p: "")
    return lambda slug: app.app.test_client().get(
        f"/begin/product-page-data/{slug}").get_json()


def test_the_caution_has_its_own_section_before_order(page):
    ids = [s["id"] for s in page("stress-release")["sections"]]
    assert "caution" in ids and ids.index("caution") < ids.index("cta")
    secs = {s["id"]: s for s in page("stress-release")["sections"]}
    assert secs["caution"]["body"] == _warning("stress-release")
    assert secs["caution"]["title"] == "Caution" and secs["caution"]["default_open"] is False
    assert "warning" not in secs["ingredients"]["body"]


def test_a_product_with_no_caution_has_no_caution_section(page):
    slug = next(s for s, p in app._PRODUCTS["products"].items()
                if p.get("active", True) and not (p.get("warning") or "").strip()
                and p.get("ingredients") and not p.get("bundle"))
    assert not any(s["id"] == "caution" for s in page(slug)["sections"])


def test_the_page_opens_the_caution_from_the_link():
    src = open(os.path.join(ROOT, "static", "begin-product.html")).read()
    assert "openSectionFromHash" in src
    assert "/^caution(-[a-z0-9-]+)?$/" in src
    assert re.search(r"renderProduct\(data\);\s*openSectionFromHash\(\);", src)


# ---- review round 1 (2026-10-24) -------------------------------------------------------

def test_a_bundle_question_gets_a_button_for_each_linked_caution():
    ctas = app._caution_link_ctas("Is Sleep Bundle safe in pregnancy?")
    assert [c["target"].rsplit("/", 1)[1] for c in ctas] == [
        "brain-cleanse#caution", "sleep-syntropy#caution"]
    assert app._caution_link_cta("Is Sleep Bundle safe in pregnancy?") == ctas[0]


def test_no_copy_order_is_left_beside_a_linked_caution():
    b = app.named_product_facts_block("Is Brain Cleanse OK before surgery?", gated=True)
    assert "repeat its full caution" not in b.lower()
    assert "copy every caution in full" not in b
    assert app._GATED_LINK_NOTE in b
    assert "A caution this text only links is not repeated." in b
    # A question touching no caution keeps the copy orders as they were.
    plain = app.named_product_facts_block("Tell me about Brain Cleanse", gated=True)
    assert app._GATED_LINK_NOTE not in plain
    assert "A caution this text only links is not repeated." not in plain


def test_a_snippet_holding_a_linked_caution_is_dropped():
    from types import SimpleNamespace as NS
    warning = app._PRODUCTS["products"]["stress-release"]["warning"]
    held = NS(id="a", score=0.9, metadata={"text": "Stress Release.  " + warning})
    other = NS(id="b", score=0.8, metadata={"text": "Ashwagandha calms the nerves."})
    q = "Can I take Stress Release before surgery?"
    assert app._drop_caution_subject_snippets([held, other], q) == [other]
    # Not linked: both kept.
    assert app._drop_caution_subject_snippets(
        [held, other], "Tell me about Stress Release") == [held, other]


@pytest.mark.parametrize("q", ["How many pills of Stress Release should I take a day?",
                               "How do the ingredients in AngiogenX interact with each other?"])
def test_a_dose_or_ingredient_question_is_not_linked(q):
    assert app._caution_link_slugs(q) == []


def test_my_pills_is_still_a_medicine_question():
    assert app._caution_link_slugs("Can I take Stress Release with my pills?") == ["stress-release"]


def test_the_embed_renders_every_caution_button():
    src = open("static/embed.html").read()
    assert src.count("(evt.extra_ctas || []).forEach") == 3
