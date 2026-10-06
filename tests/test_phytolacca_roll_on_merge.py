"""Phytolacca Oil and the Phytolacca americana Oil Roll-On become one listing.

Glen, 2026-09-24: "new formula for the existing product", priced $69.97 with "$80 crossed
out", label and intro approved. Spec:
production/05 Formulations/phytolacca-oil-roll-on/2026-09-24/listing-spec.md

The roll-on slug survives: it already carried FileMaker 95 and the exact FileMaker name,
which the invoice bottle count matches on. phytolacca-oil retires with a redirect, so its
legacy store link (id 425), its alias and old reports land on the survivor.
"""
import importlib
import json

import pytest

from dashboard import biofield_invoice
from dashboard import legacy_store_links as lsl
from dashboard import product_sales
from dashboard import products as products_mod

OLD = "phytolacca-oil"
NEW = "phytolacca-americana-oil-roll-on"


@pytest.fixture(scope="module")
def catalog():
    return json.load(open("data/products.json"))["products"]


def test_the_old_entry_is_retired_and_points_at_the_roll_on(catalog):
    assert catalog[OLD].get("inactive") is True
    assert catalog[OLD].get("superseded_by") == NEW
    assert products_mod.superseded_slug(OLD, catalog) == NEW
    # Its old name stays for old reports; invoices reach the survivor via _RETIRED_NAMES.
    assert catalog[OLD]["name"] == "Phytolacca Oil"


def test_the_roll_on_is_live_terminal_and_priced(catalog):
    p = catalog[NEW]
    assert not p.get("inactive")
    assert products_mod.superseded_slug(NEW, catalog) == NEW
    assert p["name"] == "Phytolacca americana Oil Roll-On"   # FileMaker 95, exactly
    assert p["fmp_id"] == "95"
    assert p["price_cents"] == 7000
    assert p["regular_cents"] == 8000
    # qty_pricing brings the $50 minimum unit price; turning it off to dodge capsule
    # formats would let 12 units reach $49.68 each (spec, review round 2).
    assert p["qty_pricing"] is True
    assert p["bottle_type"] == "30roll"
    assert p["no_groovekart"] is True


def test_the_roll_on_carries_the_label_formula(catalog):
    got = [(i["name"], i["dose"]) for i in catalog[NEW]["ingredients"]]
    assert got == [
        ("Poke Root 20:1 (Phytolacca americana)", "500 mg"),
        ("Ozonated Castor Oil (Ricinus communis)", "90% of the base"),
        ("Black Seed Oil (Nigella sativa)", "10% of the base"),
    ]
    # An untrusted source would drop this formula from Biofield letters.
    assert catalog[NEW]["ingredients_source"] == "production-spec-2026-09-24"
    text = json.dumps(catalog[NEW]).lower()
    assert "decandra" not in text


def test_the_roll_on_carries_the_approved_intro(catalog):
    assert catalog[NEW]["description"] == (
        "Phytolacca americana Oil Roll-On carries 500 mg of a 20:1 poke root extract in a "
        "base of ozonated castor oil and black seed oil. It is for external use only. "
        "Shake well, then roll on 3 times a day, or as guided.")


def test_the_intro_and_label_lines_are_pinned(catalog):
    """Production, 2026-09-29: the live intro was an AI draft and the panel's directions and
    warning were empty. Glen's intro is pinned so no AI draft replaces it."""
    p = catalog[NEW]
    assert p["intro"] == p["description"]
    assert {"intro", "description"} <= set(p["copy_pinned"])
    assert p["directions"] == "Shake well. Roll on 3 times a day, or as guided."
    assert p["warning"] == "For external use only."


def test_the_page_serves_the_pinned_intro_and_label_lines(a, catalog, monkeypatch):
    _no_ai(a, monkeypatch)
    # With AI copy on (as in prod), an unpinned intro is marked "ai" and replaced.
    monkeypatch.setattr(a, "_SALES_AI_COPY_ENABLED", True)
    data = a.app.test_client().get("/begin/product-page-data/" + NEW).get_json()
    secs = {s["id"]: s for s in data["sections"]}
    assert secs["intro"]["body"] == catalog[NEW]["description"]
    assert "ai" not in secs["intro"]
    blob = json.dumps(data)
    assert "Shake well. Roll on 3 times a day, or as guided." in blob
    assert "For external use only." in blob


def test_filemaker_95_maps_to_the_roll_on_only(catalog):
    assert json.load(open("data/fmp_slug_map.json"))["resolved"]["95"] == NEW
    # product_sales lets the last entry carrying an fmp_id win, so only one may carry 95.
    assert [s for s, p in catalog.items() if str(p.get("fmp_id") or "") == "95"] == [NEW]
    assert product_sales.slug_map_from_products_json("data/products.json")["95"] == NEW


def test_the_phytolacca_oil_alias_lands_on_the_roll_on():
    aliases = json.load(open("data/product-aliases.json"))
    entry = next(v for k, v in _walk(aliases) if k == "Phytolacca Oil")
    assert entry["slug"] == NEW
    assert entry["url"].endswith("/begin/product/" + NEW)


def test_the_related_products_list_moved_to_the_roll_on():
    related = json.load(open("data/related-harvested.json"))
    assert OLD not in related
    assert "lymph-flow" in related[NEW]


def test_the_old_store_link_opens_the_roll_on(catalog):
    out = lsl.rewrite_text("https://remedymatch.com/remedies/syntropy/425-phytolacca-oil",
                           "https://myhealingoasis.com", catalog)
    assert "/begin/product/" + NEW in out


@pytest.fixture
def a(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake")
    monkeypatch.setenv("PINECONE_API_KEY", "pcsk_fake")
    import app as appmod
    importlib.reload(appmod)
    appmod.app.config["TESTING"] = True
    return appmod


def _no_ai(a, monkeypatch):
    """The product-data route writes cached AI cards on first view; never call out."""
    monkeypatch.setattr(a, "_product_card",
                        lambda p: {"description": "", "ingredients": [], "benefits": []})
    monkeypatch.setattr(a, "_product_how", lambda p: "")


def test_the_roll_on_offers_no_capsule_bottles_or_refills(a, catalog, monkeypatch):
    _no_ai(a, monkeypatch)
    p = {**catalog[NEW], "slug": NEW}
    assert a._capsule_formats_ok(p) is False
    data = a.app.test_client().get("/begin/product-data/" + NEW).get_json()
    assert data.get("formats") is None
    assert data["price_cents"] == 7000
    # A stale or hand-made cart line asking for a capsule format is cleaned to none.
    for fmt in ("refill", "larger", " Refill "):
        assert a._clean_format(p, fmt) == ""


def test_the_old_address_serves_the_roll_on(a, catalog, monkeypatch):
    """An emailed /begin/product/phytolacca-oil link must still open a sellable page."""
    _no_ai(a, monkeypatch)
    c = a.app.test_client()
    r = c.get("/begin/product/" + OLD + "?utm_source=x")
    assert r.status_code == 302   # superseded slugs redirect to the survivor (Aller-Free spec)
    assert r.headers["Location"].endswith("/begin/product/" + NEW + "?utm_source=x")
    assert c.get("/begin/product/" + NEW).status_code == 200
    data = c.get("/begin/product-data/" + OLD).get_json()
    assert data["name"] == "Phytolacca americana Oil Roll-On"
    assert data["price_cents"] == 7000
    assert data.get("formats") is None


def test_volume_pricing_never_goes_below_the_50_dollar_minimum(a, catalog):
    p = {**catalog[NEW], "slug": NEW}
    from dashboard import pricing
    s = pricing.load_settings(None)
    twelve = a._inhouse_ff_unit_cents(p, 12, s, line_qty=12)
    assert twelve < 6997, "qty_pricing must give the roll-on volume tiers"
    for qty in (12, 24, 100):
        assert a._inhouse_ff_unit_cents(p, qty, s, line_qty=qty) >= 5000
        assert a._inhouse_ff_unit_cents(p, qty, s, program_member=True, line_qty=qty) >= 5000
    assert pricing.unit_floor_cents(p, 6997, s, "discount") >= 5000


def test_old_biofield_lines_named_phytolacca_oil_still_invoice(catalog):
    """The Intake app sends only active products, so the retired name must map here."""
    active = [{**p, "slug": s} for s, p in catalog.items() if not p.get("inactive")]
    assert biofield_invoice.resolve_line_slug("Phytolacca Oil", active) == NEW
    assert biofield_invoice.resolve_line_slug("phytolacca oil ", active) == NEW
    assert biofield_invoice.resolve_line_slug("Phytolacca americana Oil Roll-On", active) == NEW


def _walk(node):
    if isinstance(node, dict):
        for k, v in node.items():
            yield k, v
            yield from _walk(v)
