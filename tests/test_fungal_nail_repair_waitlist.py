"""Fungal Nail Repair is waitlist-only (2026-10-10).

It has never been made. Glen, formulation tab via formulation-1b: "the formulation needs
completion and production", and yes to a waiting list. Same mechanism as Scar Soft Drink
(#1956). The slug and URL stay. Its three ingredient lines stay visible.
"""
import json

import pytest

import app
from dashboard import product_waitlist as pw

SLUG = "fungal-nail-repair"


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setattr(app, "LOG_DB", str(tmp_path / "chat_log.db"))
    app._init_people_table()
    monkeypatch.setattr(app, "_founding_enabled", lambda: False)
    app.app.config["TESTING"] = True
    return app.app.test_client()


def test_the_record_is_waitlist_only_and_has_a_list():
    p = json.load(open("data/products.json"))["products"][SLUG]
    assert p["waitlist_only"] is True
    assert pw.LISTS[SLUG] == "fungal-nail-repair-waitlist"
    assert set(pw.TEXTS[SLUG]) == set(pw.TEXTS["scar-soft-drink"])
    assert len(p["ingredients"]) == 3


def test_the_page_sells_nothing_and_offers_the_list(client):
    d = client.get(f"/begin/product-page-data/{SLUG}").get_json()
    assert d["waitlist_only"] is True
    assert d["price"] == "" and d["price_cents"] is None
    assert "cta_url" not in d
    assert d["waitlist"]["slug"] == SLUG
    assert d["waitlist"]["intro"] == pw.TEXTS[SLUG]["intro"]
    assert "Scar" not in json.dumps(d["waitlist"])


def test_checkout_refuses_it():
    assert app._cart_waitlist_refusal([{"slug": SLUG, "qty": 1}], skip_zero_qty=False)
