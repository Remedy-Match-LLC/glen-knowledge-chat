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
