"""Electrolyte Mineral Manna is buyable but never volunteered.

Glen, 2026-09-14: "not discontinued but not being promoted by chat. OK to list", then
"Electrolyte Mineral Manna: buyable". It had carried `inactive: true`, so the new store could
neither list nor sell it. It now sits with AllerFree: sold, linked when asked for by name,
and kept out of every automatic recommendation.
"""
import json

import app
from dashboard.related_products import DO_NOT_RECOMMEND

SLUG = "electrolyte-mineral-manna"
NAME = "Electrolyte Mineral Manna"


def test_it_is_active_in_the_catalog():
    p = json.load(open("data/products.json"))["products"][SLUG]
    assert p["name"] == NAME
    assert not p.get("inactive")
    assert p["price_cents"] > 0


def test_a_client_asking_by_name_gets_its_product_page():
    directive = app.build_product_directive(query_text=f"where can I buy {NAME}")
    row = [l for l in directive.splitlines() if l.strip().startswith(f"• {NAME} ")]
    assert row, f"{NAME} missing from the link table"
    assert "DESCRIBE-ONLY" not in row[0], row[0]
    assert f"/begin/product/{SLUG}" in row[0], row[0]


def _sellable_not_recommended_rule():
    prompt = app.get_system_prompt("self-healing")
    rule = [l for l in prompt.splitlines() if l.startswith("- SELLABLE BUT NOT RECOMMENDED")]
    assert len(rule) == 1, "the SELLABLE BUT NOT RECOMMENDED rule is missing from the chat prompt"
    return rule[0]


def test_the_chat_prompt_says_never_volunteer_it_and_never_call_it_retired():
    rule = _sellable_not_recommended_rule()
    assert f'"{NAME}" is in the same position' in rule
    tail = rule.split(f'"{NAME}"', 1)[1]
    assert "do NOT volunteer or recommend it" in tail
    assert "NEVER call it retired, discontinued, or unavailable" in tail
    assert "product page link" in tail


def test_no_deprecated_rule_calls_it_discontinued():
    prompt = app.get_system_prompt("self-healing")
    deprecated = [l for l in prompt.splitlines() if l.startswith("- DEPRECATED PRODUCTS")]
    assert deprecated and NAME not in deprecated[0]


def test_automatic_recommenders_still_exclude_it():
    assert app._ff_auto_excluded(NAME)
    assert SLUG in DO_NOT_RECOMMEND
