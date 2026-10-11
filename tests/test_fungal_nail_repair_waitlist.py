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


def test_the_wording_is_the_approved_wording():
    """Glen approved this wording on 2026-10-10 (platform tab). Pinned literally, so an
    edit to any field, email subject and body included, fails here (review round 3)."""
    from dashboard import product_waitlist as pw
    assert pw.TEXTS["fungal-nail-repair"] == {
        "product": "Fungal Nail Repair",
        "consent": "We will email you when Fungal Nail Repair is ready to order. "
                   "Joining this list does not add you to any other mailing list.",
        "intro": "Fungal Nail Repair is not ready to order yet. Join the waiting list and "
                 "we will email you when it is ready. No card, no charge.",
        "reserve_line": "",
        "subject": "Confirm your Fungal Nail Repair email",
        "body_lead": "Please confirm you'd like an email when Fungal Nail Repair is ready to order:",
        "page_title": "Confirm your Fungal Nail Repair email",
        "success": "We'll email you when Fungal Nail Repair is ready.",
    }
    assert pw.LISTS["fungal-nail-repair"] == "fungal-nail-repair-waitlist"
