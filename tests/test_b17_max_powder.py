"""B17 Max is the powder now (Glen, 2026-09-27; released "yes", formulation tab, 2026-10-25).

Spec: production/05 Formulations/b17-max/2026-09-27/store-update-spec.md. The capsule copy
told buyers to "decrease the dosage if lightheaded". Lightheadedness is an early sign of
cyanide toxicity, so it must not be a dosing signal. These are the spec's own checks.
"""
import json
import os

import pytest

import app

SLUG = "b17-max"
DATA = os.path.join(os.path.dirname(__file__), "..", "data")
INGREDIENTS = [{"name": "Amygdalin 98% (Prunus armeniaca) (semen)", "dose": "450 mg"},
               {"name": "Pangamic Acid 99%", "dose": "50 mg"}]
INTRO = ("B17 Max is a vegan two-ingredient powder, amygdalin from apricot seed with "
         "pangamic acid, and nothing else.")
DESCRIPTION = (
    "Each level 500 mg scoop supplies 450 mg amygdalin 98% and 50 mg pangamic acid 99%. "
    "There are no fillers or excipients.\n\n"
    "Other ingredients: Healing Energy, Love, and Prayer. 30 g jar, 60 servings. Store "
    "tightly closed, cool and dry.\n\n"
    "Caution: amygdalin can release cyanide in the body. Read the full warning on the label "
    "and the product page before use, and do not combine with apricot kernels, fruit pits "
    "or high-dose vitamin C.")
DIRECTIONS = ("Start with a partial scoop and increase slowly, up to 2 level scoops daily in "
              "divided doses with food, with B17 Syntropy, or as guided.")
WARNING = (
    "Amygdalin can release cyanide in the body. Stop at the first sign of headache, "
    "dizziness, lightheadedness, weakness, nausea, vomiting, confusion, chest tightness or "
    "unusual breathing, and call Poison Control, 1-800-222-1222. Call 911 for breathing "
    "difficulty, fainting, seizure, bluish lips or loss of consciousness. Do not combine "
    "with apricot kernels, bitter almonds, fruit pits, beta-glucosidase or high-dose "
    "vitamin C. Not for use in pregnancy, breastfeeding or children. Consult a physician "
    "and pharmacist before use with any medication or cancer treatment. Keep out of reach "
    "of children.")


def _load(name):
    return json.load(open(os.path.join(DATA, name)))


@pytest.fixture()
def client():
    app.app.config["TESTING"] = True
    return app.app.test_client()


def test_the_catalog_record_is_the_powder():
    p = _load("products.json")["products"][SLUG]
    assert p["ingredients"] == INGREDIENTS
    assert (p["intro"], p["description"], p["directions"], p["warning"]) == (
        INTRO, DESCRIPTION, DIRECTIONS, WARNING)
    assert p["bottle_type"] == "30 g" and p["ingredients_source"] == "label-2026-09-27"
    assert p["benefits"] == [] and p["how_it_works"] == ""
    assert p["copy_pinned"] == ["ingredients", "intro", "description", "benefits", "research"]
    assert (p["name"], p["fmp_id"], p["price_cents"]) == ("B17 Max", "890", 7000)


def test_the_correction_carries_the_powder_so_enrichment_cannot_restore_capsules():
    c = _load("products-manual-corrections.json")[SLUG]
    assert c == {"ingredients": INGREDIENTS, "ingredients_source": "label-2026-09-27",
                 "description": DESCRIPTION, "directions": DIRECTIONS, "warning": WARNING,
                 "bottle_type": "30 g"}


def test_no_capsule_dosing_survives_anywhere_in_its_data():
    records = [json.dumps(_load("products.json")["products"][SLUG]),
               json.dumps(_load("products-manual-corrections.json")[SLUG])]
    records += [open(os.path.join(DATA, n)).read()
                for n in ("atlas-concepts.json", "atlas-seed-input.json")]
    for text in records:
        assert "decrease the dosage if lightheaded" not in text
    assert all("Laetrile" not in t and "capsule" not in t.lower() for t in records[:2])
    for name in ("atlas-concepts.json", "atlas-seed-input.json"):
        assert "B17 Max is a proprietary formulation" not in open(os.path.join(DATA, name)).read()


def test_the_product_page_shows_the_pinned_powder_copy(client, monkeypatch, tmp_path):
    # Live runs with AI copy on. With it off no section carries `ai`, and the pin check
    # below would pass with the pin lost.
    monkeypatch.setattr(app, "_SALES_AI_COPY_ENABLED", True)
    monkeypatch.setattr(app, "LOG_DB", str(tmp_path / "chat_log.db"))
    d = client.get(f"/begin/product-page-data/{SLUG}").get_json()
    sec = {s.get("id"): s for s in d["sections"] if isinstance(s, dict)}
    assert sec["caution"]["body"] == WARNING
    body = sec["ingredients"]["body"]
    assert [(i["name"], i["dose"]) for i in body["ingredients"]] == [
        (i["name"], i["dose"]) for i in INGREDIENTS]
    assert body["directions"] == DIRECTIONS
    assert sec["description"]["body"] == DESCRIPTION
    assert sec["intro"]["body"] == INTRO
    assert sec["research"]["body"]["how_it_works"] == ""
    for key in ("intro", "description", "research"):
        assert "ai" not in sec[key], key


def test_product_data_pins_an_empty_benefits_list(client, monkeypatch, tmp_path):
    # A generated card would carry benefits; the pin must keep them off.
    monkeypatch.setattr(app, "LOG_DB", str(tmp_path / "chat_log.db"))
    monkeypatch.setattr(app, "_product_card",
                        lambda p: {"benefits": ["Generated benefit"], "description": "x"})
    d = client.get(f"/begin/product-data/{SLUG}").get_json()
    assert "benefits" in d and d["benefits"] == []


def test_the_learn_page_is_the_powder_description(client, monkeypatch, tmp_path):
    monkeypatch.setattr(app, "LOG_DB", str(tmp_path / "chat_log.db"))
    md = client.get(f"/begin/learn-data/{SLUG}").get_json()["markdown"]
    assert md == DESCRIPTION
    assert "capsule" not in md.lower() and "lightheaded" not in md.lower()


@pytest.mark.parametrize("q", ["i feel lightheaded on b17 max, what should i do?",
                               "how much b17 max should i take?", "B17 max dose?"])
def test_any_casing_brings_the_warning(q):
    b = app._named_product_facts(q)
    assert "### B17 Max" in b and "Poison Control, 1-800-222-1222" in b
