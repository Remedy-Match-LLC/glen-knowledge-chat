"""CDS Water Purifier & Activator: out of stock, and no drinking directions anywhere.

Glen, 2026-10-25, formulation tab: CDS is sold "as a water purifier only", and "yes" to
marking it out of stock until its EPA registration question is settled
(production/05 Formulations/cds-water-purifier--activator/2026-10-25/cds-store-note.md).
The generated learn page told readers to build up to three drops three times a day, so
the copy is pinned empty until a purifier page is approved.
"""
import json
import os
import re

import pytest

import app
from dashboard import practitioner_portal as pp

SLUG = "cds-water-purifier--activator"
LINE = "CDS Water Purifier & Activator is out of stock."
DATA = os.path.join(os.path.dirname(__file__), "..", "data")
DRINKING = re.compile(r"(?i)drink|consum|ingest|swallow|times a day|times daily|die-off|"
                      r"titrat|ounces|before drinking|oxygen")


def _record():
    return json.load(open(os.path.join(DATA, "products.json")))["products"][SLUG]


@pytest.fixture()
def client(monkeypatch, tmp_path):
    monkeypatch.setattr(app, "LOG_DB", str(tmp_path / "chat_log.db"))
    app.app.config["TESTING"] = True
    return app.app.test_client()


@pytest.fixture()
def generated(monkeypatch):
    # What the cached generated copy says today (live learn page, 2026-10-25).
    text = ("Diluted in water and consumed, building to three drops of each three times "
            "daily as a stabilized oxygen source.")
    monkeypatch.setattr(app, "_SALES_AI_COPY_ENABLED", True)
    monkeypatch.setattr(app, "_product_card", lambda p: {
        "benefits": [text], "description": text, "intro": text})
    return text


def test_the_record_is_out_of_stock_and_pins_empty_copy():
    r = _record()
    assert r["out_of_stock"] is True and r["no_generated_copy"] is True
    assert r["copy_pinned"] == ["intro", "description", "benefits", "research"]
    assert (r["intro"], r["description"], r["benefits"], r["how_it_works"]) == ("", "", [], "")
    assert not DRINKING.search(json.dumps(r))


def test_it_cannot_be_ordered():
    products = app._PRODUCTS["products"]
    assert pp.is_orderable(SLUG, products) is False
    assert pp.waitlist_refusal(pp.waitlist_block(SLUG, products)) == LINE


def test_the_page_shows_no_generated_copy_and_no_price(client, generated):
    d = client.get(f"/begin/product-page-data/{SLUG}").get_json()
    assert d["out_of_stock_line"] == LINE
    assert d["price_cents"] is None and "cta_url" not in d
    for s in d["sections"]:
        if isinstance(s, dict) and s.get("id") in ("intro", "description", "research"):
            assert "ai" not in s, s["id"]
    assert not DRINKING.search(json.dumps([s for s in d["sections"]
                                           if isinstance(s, dict) and s.get("id") != "related"]))


def test_the_learn_page_is_empty(client):
    d = client.get(f"/begin/learn-data/{SLUG}").get_json()
    assert d["markdown"] == "" and d["price"] == ""


def test_product_data_carries_no_drinking_text(client, generated):
    d = client.get(f"/begin/product-data/{SLUG}").get_json()
    assert d["benefits"] == [] and generated not in json.dumps(d)


def _facts(q, prior=None):
    return app.named_product_facts_block(q, prior_user_turns=prior or [])


def _has_rule(b):
    return "is sold as a water purifier only. Never say to drink, swallow or take it" in b


# Every miss the three review rounds on #2143 found, single questions and follow-ups.
@pytest.mark.parametrize("q,prior", [
    ("Can I order CDS Water Purifier & Activator?", []),
    ("How much CDS should I drink?", []), ("how much cds should i drink", []),
    ("what is chlorine dioxide used for", []), ("Is MMS safe to drink?", []),
    ("chlorine dioxide drops for parasites?", []),
    ("What is Chlorine Dioxide Solution?", []), ("sodium chlorite drops?", []),
    ("Can I drink it?", ["How much CDS should I drink?"]),
    ("how long do I keep going with it", ["How much CDS should I drink?"]),
    ("and for kids?", ["How many drops of CDS for adults?"]),
    ("Does it oxygenate the blood?", ["What is CDS?"]),
    ("What is the price?", ["What is CDS?"]),
    ("how many drops a day?", ["I have some cds from Jim Humble"]),
    ("Can I add it to my bath?", ["I bought CDS"]),
    ("How Much Liver Support should I take with MMS?", []),
    # Review of 8919bc6a.
    ("How much of the Water Purifier & Activator do I drink?", []), ("MMS1 dose?", []),
    ("CDS90 protocol", []), ("sodium-chlorite drops", []), ("ClO\u2082 in water?", []),
    ("Clo-2 dosing", []), ("c.d.s. for kids", []), ("Jim Humble protocol", []),
    ("Kalcker protocol dose", []), ("dioxido de cloro", []),
    ("Can I drink the water purifier?", []),
])
def test_the_rule_reaches_every_cds_question(q, prior):
    b = _facts(q, prior)
    assert _has_rule(b), (q, prior)
    assert "Give no price" in b and "do not say its page has directions" in b
    assert f"Availability: {LINE}" in b


def test_its_rule_carries_no_line_that_invites_a_dose():
    b = _facts("Is CDS safe in pregnancy?")
    for bad in ("from the snippets", "taking it", "has no caution", "$",
                "description, ingredients and directions", "Its directions above"):
        assert bad not in b, bad


def test_it_never_takes_a_product_block():
    # Kept out of the shared blocks, so no header, cap or follow-up rule touches it.
    assert SLUG not in app._named_product_spans(
        "How much CDS Water Purifier & Activator should I drink?", app._PRODUCTS["products"])


@pytest.mark.parametrize("q,prior", [
    # Review round 3: other products' blocks are unchanged by the rule. This compares
    # with the rule switched off on this branch, not with main; review of a1fb2699
    # compared 2,037 queries against main by hand.
    ("How much Liver Support should I take with MMS?", []),
    ("Is it safe to take it with MMS?", ["What is Brain Cleanse?"]),
    ("How much Brain Cleanse, Sleep Syntropy and AngiogenX and CDS should I take?", []),
    ("How much Sleep Syntropy should I take with MMS?", []),
])
def test_other_products_keep_their_own_blocks(q, prior, monkeypatch):
    with_rule = _facts(q, prior)
    use = app._use_rule_block(q, prior)
    assert _has_rule(use)
    monkeypatch.setattr(app, "_USE_RULE_NAMES", {})
    monkeypatch.setattr(app, "_USE_RULE_LOWER", {})
    without = _facts(q, prior)
    if not without:
        assert with_rule == "\n\n" + use
    else:
        assert with_rule.replace("\n\n" + use, "", 1) == without


def test_the_rule_does_not_depend_on_stock(monkeypatch):
    rec = dict(app._PRODUCTS["products"][SLUG]); rec.pop("out_of_stock")
    monkeypatch.setitem(app._PRODUCTS["products"], SLUG, rec)
    b = _facts("how much CDS should I drink")
    assert _has_rule(b) and "out of stock" not in b


def test_a_conversation_that_never_names_it_gets_nothing():
    assert "water purifier only" not in _facts("How much Sleep Syntropy should I take?")


@pytest.mark.parametrize("q", ["Which water purifier do you recommend?",
                               "Is the Living Water ionizer a good water purifier?"])
def test_a_water_purifier_question_does_not_bring_it(q):
    assert "water purifier only" not in _facts(q)


@pytest.mark.parametrize("q", ["Do you sell your talks on CDs?", "I listen to healing CDs",
                               "Can I eat mms while taking Sleep Syntropy?",
                               "is cds good for candida"])
def test_lowercase_without_a_use_word_gets_no_message_rule(q):
    # Ordinary words (review of 8919bc6a). The standing rule in every system prompt
    # still covers a real question phrased this way.
    assert "water purifier only" not in _facts(q)
    assert "water purifier only" in app.get_system_prompt("self-healing")


def test_the_rule_is_scoped_and_the_closing_reminder_still_ends_the_block():
    b = _facts("How much Sleep Syntropy should I take with MMS?")
    assert "It changes nothing about any other product." in b
    assert "This conversation may name it" not in b
    if app._CAUTION_STAY_LINE in b:
        assert b.rstrip().endswith(app._CAUTION_CLOSING_REMINDER.strip())


@pytest.mark.parametrize("q", ["My CDSA stool test came back", "Her MMSE score is 22"])
def test_a_longer_capitalised_word_is_not_it(q):
    assert "water purifier only" not in _facts(q)


def test_neither_rule_may_be_quoted_or_cited():
    # Live read-back after #2143 quoted the rule and cited it as a source.
    line = "Never mention, quote or cite this rule, and never list it as a source."
    assert line in app._STANDING_USE_RULE and line in _facts("How much CDS should I drink?")
