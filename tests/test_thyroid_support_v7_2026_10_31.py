"""Thyroid Support: the store page 117 panel and caution v7 (Glen, 2026-10-31).

Source: marketing/04 Copy/thyroid-support-panel-2026-10-31/, description-after-v7.html for
the panel and caution-v7.txt for the caution. The text is embedded here so the test does not
read the vault. Iodine is 150 mcg from potassium iodide; the old row said "Potassium Iodide
KI" 194 mg, a wrong unit. The rows, directions and description are pinned, so the chat
carries the caution too (chat_caution).
"""
import copy
import json
import os
import re
import shutil

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SLUG = "thyroid-support"
CAUTION = (
    "Do not use if you are pregnant or breastfeeding. Not for anyone 18 or under. Do not use "
    "if you have hormone-sensitive prostate cancer. Do not use if you have an overactive "
    "thyroid (hyperthyroidism or Graves' disease). Keep out of reach of children. Rare cases "
    "of liver injury have been linked to ashwagandha. Do not use if you have had liver injury "
    "from ashwagandha. If you have liver disease, consult your physician before use. Stop use "
    "and see your physician if you notice dark urine or yellowing skin or eyes. Each capsule "
    "contains about 150 mcg of iodine, the adult daily requirement. If you have any other "
    "thyroid condition or take other iodine products, consult your health professional before "
    "use. Ashwagandha may raise thyroid hormone levels and change thyroid test results. If you "
    "take a thyroid hormone medicine, consult your physician before use. Ginger may increase "
    "the risk of bleeding. If you take a blood thinner or antiplatelet medicine, such as "
    "warfarin, aspirin or clopidogrel, or have a bleeding disorder, consult your physician "
    "before use. If you are planning surgery, tell your surgical team you take this product "
    "and ask when to stop. Ashwagandha may add to the effect of sedatives or seizure medicines "
    "and may interact with medicines for diabetes, blood pressure or the immune system. Ginger "
    "may lower blood sugar too far when taken with diabetes medicines. If you take any "
    "prescription medicine, or have an autoimmune condition, consult your physician before "
    "use. Zinc can reduce how well quinolone and tetracycline antibiotics and penicillamine "
    "are absorbed. If you take one of these, ask your pharmacist how far apart to take them. "
    "If you take other zinc or selenium supplements, keep your total from food and supplements "
    "at or below 40 mg of zinc and 400 mcg of selenium a day. Ashwagandha may cause "
    "drowsiness, stomach upset, diarrhea or vomiting. Do not drive or operate machinery if it "
    "makes you drowsy. If these effects are severe, stop use and see your physician. "
    "Ashwagandha may be safe for up to three months. Ask your physician before using it "
    "longer.")
ROWS = [("Gingerols 20% (Zingiber officinale)", "100 mg"),
        ("Zinc (Zinc Bisglycinate)", "15 mg"),
        ("Selenium (Methylselenocysteine)", "100 mcg"),
        ("Iodine (Potassium Iodide)", "150 mcg"),
        ("Thymoquinone 20% (Nigella sativa)", "25 mg"),
        ("Withanolides 20% (Withania somnifera)", "50 mg"),
        ("Vitamin B8: Myo-Inositol", "200 mg"),
        ("Vitamin D3: Cholecalciferol", "24 mcg (960 IU)")]
DIRECTIONS = "1 capsule a day with food, or as guided."
PINS = ["ingredients", "directions", "description"]
GRAVES = "Do not use if you have an overactive thyroid (hyperthyroidism or Graves' disease)."


def _load(name):
    d = json.load(open(os.path.join(ROOT, "data", name), encoding="utf-8"))
    return d.get("products", d)


def _check(p):
    assert p["warning"] == CAUTION
    assert p["chat_caution"] is True
    assert [(i["name"], i["dose"]) for i in p["ingredients"]] == ROWS
    iodine = [i for i in p["ingredients"] if i["name"].startswith("Iodine")]
    assert [i["dose"] for i in iodine] == ["150 mcg"]
    for i in p["ingredients"]:
        if "iodide" in i["name"].lower():
            assert not re.search(r"\bmg\b", i["dose"]), i
    assert p["directions"] == DIRECTIONS and p["directions"].endswith("or as guided.")
    assert p["copy_pinned"] == PINS
    assert "$" not in p["description"] and "Price" not in p["description"]
    assert p["content_since"].endswith("+00:00")


@pytest.mark.parametrize("name", ["products.json", "products-manual-corrections.json"])
def test_both_files_carry_v7(name):
    _check(_load(name)[SLUG])


def test_the_replay_restores_v7_onto_the_old_record():
    # The record as it was on main before v7: the 194 mg iodide row and no caution.
    from scripts.apply_enrichment import apply_corrections
    old = copy.deepcopy(_load("products.json")[SLUG])
    old["ingredients"] = [{"name": "MSC", "dose": "244 mcg"},
                          {"name": "Potassium Iodide KI", "dose": "194 mg"}]
    old["ingredients_source"] = "db-formulations-2026-06-25"
    old["description"] = "Thyroid Support . Price: $70."
    for k in ("directions", "warning", "chat_caution", "copy_pinned", "content_since",
              "enrichment_note"):
        old.pop(k, None)
    products = {SLUG: old}
    apply_corrections(products, {SLUG: _load("products-manual-corrections.json")[SLUG]})
    _check(products[SLUG])
    assert products[SLUG]["description"] == _load("products.json")[SLUG]["description"]


def test_a_full_rebuild_keeps_v7(tmp_path, monkeypatch):
    from scripts import apply_enrichment as ae
    for name, attr in (("products.json", "PRODUCTS"), ("products-enrich-clean.json", "CLEAN"),
                       ("products-manual-corrections.json", "CORRECTIONS")):
        shutil.copy(os.path.join(ROOT, "data", name), tmp_path / name)
        monkeypatch.setattr(ae, attr, str(tmp_path / name))
    ae.main()
    _check(json.load(open(tmp_path / "products.json"))["products"][SLUG])


@pytest.fixture
def appmod(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    import importlib
    import app as a
    importlib.reload(a)
    a.app.config["TESTING"] = True
    return a


def test_the_page_shows_the_rows_directions_and_caution(appmod):
    d = appmod.app.test_client().get(f"/begin/product-page-data/{SLUG}").get_json()
    secs = {s["id"]: s for s in d["sections"]}
    body = secs["ingredients"]["body"]
    assert [(i["name"], i["dose"]) for i in body["ingredients"]] == ROWS
    assert body["directions"] == DIRECTIONS and secs["caution"]["body"] == CAUTION


def test_the_chat_facts_carry_the_graves_line(appmod):
    block = appmod._named_product_facts(
        "Can I take Thyroid Support if I have Graves' disease?") or ""
    assert "### Thyroid Support" in block
    assert GRAVES in block
    assert CAUTION in block
    assert f"Directions: {DIRECTIONS}" in block
    assert "Iodine (Potassium Iodide): 150 mcg" in block
    assert "194 mg" not in block


def test_the_ampersand_is_a_plain_character(appmod):
    """The page source wrote "Love &amp; Prayer"; the record and the chat carry "&"."""
    for name in ("products.json", "products-manual-corrections.json"):
        assert "Love & Prayer." in _load(name)["thyroid-support"]["description"]
    block = appmod._named_product_facts("What is Thyroid Support?", gated=True) or ""
    assert "Love & Prayer." in block and "&amp;" not in block


@pytest.mark.parametrize("q", ["I have Graves' disease. Can I take Thyroid Support?",
                               "Can I take Thyroid Support with levothyroxine?"])
def test_before_the_terms_the_caution_is_written_by_code(appmod, q):
    """Knowledge read-back 2648: the model added a mechanism paragraph before the Terms."""
    reply = appmod._pre_terms_caution_reply(q, [])
    assert reply.startswith(appmod._PRE_TERMS_CAUTION_LEAD.format(name="Thyroid Support"))
    assert CAUTION in reply and "1 capsule a day" not in reply
