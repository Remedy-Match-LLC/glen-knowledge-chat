"""Thermogenesis carries the store page Glen approved on 25 Nov 2026 (production tab,
"approve": v4, production/05 Formulations/thermogenesis/2026-10-31/thermogenesis-store-page.md).

The catalogue held raw FileMaker names, chromium as 120 mcg of polynicotinate (15 mcg of
chromium), kelp where run 63 used fucoxanthin, the placeholder "Thermogenesis . Price: $70."
and the old two-line caution. The chat quotes the catalogue's caution, so it must be right
first. Each field is pinned in the corrections file so an enrichment rerun keeps it.
"""
import importlib
import json
import os

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INTRO = "A Healing Sacrament, handcrafted with Aloha on the Healing Island of Hawaiʻi."
DIRECTIONS = "1 to 3 capsules daily, 30 to 60 minutes before meals, or as guided."
OPENS = ("Do not use if you are pregnant or breastfeeding, or if you are under 18. Synephrine, "
         "from bitter orange, may raise heart rate and blood pressure.")
GINKGO = ("This formula contains ginkgo, which may increase bleeding with blood thinners such "
          "as warfarin, aspirin or clopidogrel.")
LIVER = "Green tea extract and garcinia are rare causes of liver injury."
ENDS = ("Each capsule contains about 150 mcg of iodine, the adult daily requirement. If you "
        "have a thyroid condition or take other iodine products, consult your health "
        "professional before use. This formula contains vinpocetine, so do not use it if you "
        "are pregnant or could become pregnant.")
PANEL = [
    ("Chromium (as GTF chromium polynicotinate)", "15 mcg"),
    ("Iodine (as potassium iodide)", "150 mcg"),
    ("Vanadium (as amino acid chelate)", "100 mcg"),
    ("L-carnitine", "100 mg"),
    ("Lipase (Aspergillus niger)", "5 mg"),
    ("Hydroxycitric acid 60% (Garcinia cambogia)", "100 mg"),
    ("Green tea polyphenols 98%, EGCG 50% (Camellia sinensis)", "50 mg"),
    ("Bai zhu 30:1 (Atractylodes macrocephala) (root)", "50 mg"),
    ("Naringin 98% (Citrus spp.)", "50 mg"),
    ("Rhodiola: rosavins 3%, salidroside 1% (Rhodiola rosea) (root)", "40 mg"),
    ("Hesperidin methyl chalcone 98% (Citrus aurantium)", "10 mg"),
    ("Synephrine 99% (Citrus aurantium)", "2 mg"),
    ("Fucoxanthin 85% (brown seaweed, Laminaria spp.)", "2 mg"),
    ("Bioavailability Blend", "34.5 mg"),
]


def _load(name):
    d = json.load(open(os.path.join(ROOT, "data", name), encoding="utf-8"))
    return d.get("products", d)


@pytest.fixture
def appmod(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake")
    monkeypatch.setenv("PINECONE_API_KEY", "pcsk_fake")
    import app as a
    importlib.reload(a)
    return a


@pytest.mark.parametrize("name", ["products.json", "products-manual-corrections.json"])
def test_record_and_pin_carry_the_approved_page(name):
    p = _load(name)["thermogenesis"]
    assert [(i["name"], i["dose"]) for i in p["ingredients"]] == PANEL
    assert p["ingredients_source"] == "manual"
    assert p["description"] == INTRO and p["intro"] == INTRO
    assert p["directions"] == DIRECTIONS
    w = p["warning"]
    assert w.startswith(OPENS) and w.endswith(ENDS) and GINKGO in w and LIVER in w
    assert "—" not in w and p["chat_caution"] is True
    assert "Contains about 3 mg caffeine per capsule" in p["panel_note"]
    assert "EGb 761 (Ginkgo biloba)" in p["panel_note"]


def test_the_old_values_are_gone():
    p = _load("products.json")["thermogenesis"]
    s = json.dumps(p)
    assert "120 mcg" not in s and "Kelp" not in s and "Price: $70" not in s


def test_the_replay_restores_every_field():
    import sys
    sys.path.insert(0, os.path.join(ROOT, "scripts"))
    import apply_enrichment as ae
    products = json.loads(json.dumps(_load("products.json")))
    want = json.loads(json.dumps(products["thermogenesis"]))
    t = products["thermogenesis"]
    for k in ("ingredients", "description", "intro", "directions", "warning", "panel_note"):
        t[k] = "stale" if k != "ingredients" else [{"name": "Kelp", "dose": "2 mg"}]
    t["ingredients_source"] = "db-formulations-2026-06-25"
    ae.apply_corrections(products, _load("products-manual-corrections.json"))
    for k in ("ingredients", "ingredients_source", "description", "intro", "directions",
              "warning", "panel_note", "chat_caution"):
        assert products["thermogenesis"][k] == want[k], k


def test_the_chat_quotes_the_new_caution_once(appmod):
    block = appmod.named_product_facts_block("What is Thermogenesis?")
    assert "### Thermogenesis" in block
    assert block.count(GINKGO) == 1 and block.count(LIVER) == 1


def test_the_store_page_shows_the_caution(appmod, monkeypatch):
    appmod.app.config["TESTING"] = True
    monkeypatch.setattr(appmod, "_product_card", lambda p: {"description": "", "ingredients": [],
                                                           "benefits": []})
    monkeypatch.setattr(appmod, "_product_how", lambda p: "")
    d = appmod.app.test_client().get("/begin/product-page-data/thermogenesis").get_json()
    body = next(s for s in d["sections"] if s["id"] == "caution")["body"]
    assert body == _load("products.json")["thermogenesis"]["warning"]
