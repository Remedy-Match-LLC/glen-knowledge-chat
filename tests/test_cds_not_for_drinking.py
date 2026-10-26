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


@pytest.mark.parametrize("q", ["Can I order CDS Water Purifier & Activator?",
                               "How much CDS should I drink?"])
def test_the_chat_is_told_it_cannot_be_ordered(q):
    b = app._named_product_facts(q)
    assert "### CDS Water Purifier & Activator" in b
    assert f"Availability: {LINE}" in b
    assert "CDS Water Purifier & Activator cannot be ordered now." in b
    assert app._USE_RULES[SLUG] in b and "water purifier only" in app._USE_RULES[SLUG]
    # Live read-back before #2143: "Before taking this product", "the list price is $70"
    # and "full directions" on its page (knowledge, 2026-10-25).
    for must in ("Never say to drink, swallow or take it", "Give no price",
                 "do not say its page has directions"):
        assert must in app._USE_RULES[SLUG]
    assert "$" not in b.split("### CDS Water Purifier & Activator", 1)[1]


@pytest.mark.parametrize("q", ["how much cds should i drink", "Is MMS safe to drink?",
                               "chlorine dioxide drops for parasites?"])
def test_short_names_in_any_case_bring_the_rule(q):
    b = app._named_product_facts(q)
    assert app._USE_RULES[SLUG] in b


def test_its_block_carries_no_line_that_invites_a_dose():
    # Review round 1, #2143: the generic lines told the bot to describe it from the
    # snippets and to "ask Dr. Glen before taking it".
    b = app._named_product_facts("Is CDS safe in pregnancy?")
    block = b.split("### CDS Water Purifier & Activator", 1)[1]
    for bad in ("from the snippets", "taking it", "has no caution", "directions"):
        assert bad not in block, bad


@pytest.mark.parametrize("follow", ["Can I drink it?", "Is it safe to drink?", "how many drops?",
                                    "How do I drink it?", "How often?",
                                    "What dilution do I use for parasites?"])
def test_a_use_follow_up_carries_the_rule(follow):
    for prior in ("How much CDS should I drink?", "Tell me about MMS"):
        b = app.named_product_facts_block(follow, prior_user_turns=[prior])
        assert app._USE_RULES[SLUG] in b, (prior, follow)


def test_a_use_follow_up_does_not_carry_another_product():
    # Only a use-rule product is carried by the wide test; Night Vision keeps the narrow one.
    assert app.named_product_facts_block(
        "Can I drink it?", prior_user_turns=["Can I order Night Vision?"]) == ""


def test_the_rule_does_not_depend_on_stock(monkeypatch):
    rec = dict(app._PRODUCTS["products"][SLUG]); rec.pop("out_of_stock")
    monkeypatch.setitem(app._PRODUCTS["products"], SLUG, rec)
    b = app._named_product_facts("how much CDS should I drink")
    assert app._USE_RULES[SLUG] in b and "out of stock" not in b


def test_the_product_cap_keeps_it():
    b = app._named_product_facts(
        "How much Brain Cleanse, Sleep Syntropy and AngiogenX and CDS should I take?")
    assert app._USE_RULES[SLUG] in b


@pytest.mark.parametrize("q", ["Do you sell meditation CDs?", "I listen to healing CDs",
                               "Does my city water use chlorine dioxide?",
                               "I burned two cds of music"])
def test_ordinary_words_are_not_taken_for_it(q):
    assert SLUG not in app._named_product_spans(q, app._PRODUCTS["products"])


def test_a_cds_only_answer_does_not_get_the_caution_only_header():
    b = app._named_product_facts("How much CDS should I drink?")
    assert "Repeat its full caution" not in b
