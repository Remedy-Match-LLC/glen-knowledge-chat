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


# The approved caution and panel note in full (review round 2). The last test checks them
# against the vault spec when it is on this machine.
WARNING = (
    OPENS + " Do not combine with additional caffeine or other stimulants, such as energy "
    "drinks, decongestants or ADHD medicine. Stop use and get medical help right away if you "
    "notice chest pain or an irregular heartbeat. Stop use if you notice a racing heartbeat or "
    "dizziness. Consult your health professional before use if you have high blood pressure or "
    "a heart condition, or take any prescription medicine. " + GINKGO + " If you are planning "
    "surgery, tell your surgical team you take it and ask when to stop. " + LIVER + " Do not "
    "use if you have liver disease. Stop use and consult your physician if you notice yellowing "
    "of the skin or eyes, dark urine, or abdominal pain. " + ENDS)
BLEND = (
    "Phosphatides (Helianthus annuus), humic and fulvic acids, moringa 100:1 (Moringa "
    "oleifera), gingerol 10% (Zingiber officinale), piperine (Piper nigrum), EGb 761 (Ginkgo "
    "biloba), niacin 5 mg, vinpocetine (Vinca minor), licorice (Glycyrrhiza glabra) and Spirit "
    "Minerals.")
PANEL_NOTE = (
    "Bioavailability Blend, 34.5 mg: " + BLEND + " Other ingredients: organic pullulan "
    "vegicaps, Healing Energy, Love and Prayer. 30 vegicaps. Vegetarian and vegan. Contains "
    "less than 1 mg of caffeine per capsule.")   # v5, Glen 2026-11-25
SPEC = os.path.expanduser("~/AI-Training/production/05 Formulations/thermogenesis/"
                          "2026-10-31/thermogenesis-store-page.md")


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
    # Word for word (review round 2): a dropped sentence or a changed count fails here.
    assert p["warning"] == WARNING and p["chat_caution"] is True
    assert p["warning"].startswith(OPENS) and p["warning"].endswith(ENDS)
    assert p["panel_note"] == PANEL_NOTE
    # Newer than any cached research text, so the page regenerates from this record
    # rather than the old store scrape with kelp and "caffeine-free" (review round 1).
    assert p["content_since"] >= "2026-11-25"


def test_the_old_values_are_gone():
    p = _load("products.json")["thermogenesis"]
    s = json.dumps(p)
    assert "120 mcg" not in s and "Kelp" not in s and "Price: $70" not in s
    assert "about 3 mg caffeine" not in s   # superseded by v5


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
    assert block.count(WARNING) == 1   # the whole approved caution, once (review round 2)
    assert block.count(GINKGO) == 1 and block.count(LIVER) == 1


def test_the_store_page_shows_the_caution(appmod, monkeypatch):
    appmod.app.config["TESTING"] = True
    monkeypatch.setattr(appmod, "_product_card", lambda p: {"description": "", "ingredients": [],
                                                           "benefits": []})
    monkeypatch.setattr(appmod, "_product_how", lambda p: "")
    d = appmod.app.test_client().get("/begin/product-page-data/thermogenesis").get_json()
    body = next(s for s in d["sections"] if s["id"] == "caution")["body"]
    assert body == _load("products.json")["thermogenesis"]["warning"]


@pytest.mark.skipif(not os.path.exists(SPEC), reason="the vault spec is not on this machine")
def test_the_pinned_text_is_the_approved_spec_text():
    spec = open(SPEC, encoding="utf-8").read()
    plain = spec.replace("*", "")
    assert '"' + WARNING + '"' in spec
    assert BLEND in plain
    assert "Other ingredients: organic pullulan vegicaps, Healing Energy, Love and Prayer." in plain
    assert "30 vegicaps. Vegetarian and vegan. Contains less than 1 mg of caffeine per capsule." in plain
    assert INTRO in spec and ("Sacramental Healing use: " + DIRECTIONS) in plain
