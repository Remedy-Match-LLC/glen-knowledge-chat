"""Thermogenesis matches production's approved app spec (Glen "send", production tab,
25 Nov 2026: production/05 Formulations/_store-updates/2026-11-25-thermogenesis-app.md,
v3, after three blind rounds and a confirm round). Store page v5 is live on remedymatch.com.

The app listed "Chromium Polynicotinate 120 mcg" (15 mcg of chromium), "Potassium Iodide
KI 195 mcg" and "Kelp", had no directions, and its caution held two lines. Only eight keys
change, in both data files, so a rebuild cannot drop them.
"""
import copy
import importlib
import json
import os
import re
import subprocess

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SPEC = os.path.expanduser("~/AI-Training/production/05 Formulations/_store-updates/"
                          "2026-11-25-thermogenesis-app.md")
ROWS = [
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
DIRECTIONS = "1 to 3 capsules daily, 30 to 60 minutes before meals, or as guided."
WARNING = (
    "Do not use if you are pregnant or breastfeeding, or if you are under 18. Synephrine, from "
    "bitter orange, may raise heart rate and blood pressure. Do not combine with additional "
    "caffeine or other stimulants, such as energy drinks, decongestants or ADHD medicine. Stop "
    "use and get medical help right away if you notice chest pain or an irregular heartbeat. "
    "Stop use if you notice a racing heartbeat or dizziness. Consult your health professional "
    "before use if you have high blood pressure or a heart condition, or take any prescription "
    "medicine. This formula contains ginkgo, which may increase bleeding with blood thinners "
    "such as warfarin, aspirin or clopidogrel. If you are planning surgery, tell your surgical "
    "team you take it and ask when to stop. Green tea extract and garcinia are rare causes of "
    "liver injury. Do not use if you have liver disease. Stop use and consult your physician if "
    "you notice yellowing of the skin or eyes, dark urine, or abdominal pain. Each capsule "
    "contains about 150 mcg of iodine, the adult daily requirement. If you have a thyroid "
    "condition or take other iodine products, consult your health professional before use. "
    "This formula contains vinpocetine, so do not use it if you are pregnant or could become "
    "pregnant.")
PANEL_NOTE = (
    "Bioavailability Blend contains: Phosphatides (Helianthus annuus), humic and fulvic acids, "
    "moringa 100:1 (Moringa oleifera), gingerol 10% (Zingiber officinale), piperine (Piper "
    "nigrum), EGb 761 (Ginkgo biloba), niacin 5 mg, vinpocetine (Vinca minor), licorice "
    "(Glycyrrhiza glabra) and Spirit Minerals. Other ingredients: organic pullulan vegicaps, "
    "Healing Energy, Love and Prayer. 30 vegicaps. Vegetarian and vegan. Contains less than 1 "
    "mg of caffeine per capsule.")
KEYS = {"ingredients", "ingredients_source", "directions", "warning", "copy_pinned",
        "panel_note", "content_since", "chat_caution"}


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
    a.app.config["TESTING"] = True
    return a


@pytest.mark.parametrize("name", ["products.json", "products-manual-corrections.json"])
def test_both_files_carry_the_eight_keys(name):
    p = _load(name)["thermogenesis"]
    assert [(i["name"], i["dose"]) for i in p["ingredients"]] == ROWS
    assert p["ingredients_source"] == "store-page-2026-11-25"
    assert p["directions"] == DIRECTIONS
    assert p["warning"] == WARNING
    assert p["copy_pinned"] == ["directions", "ingredients"]
    assert p["panel_note"] == PANEL_NOTE
    assert p["content_since"] >= "2026-11-25T"
    assert p["chat_caution"] is True


def _main(name):
    try:
        base = subprocess.run(["git", "show", f"origin/main:data/{name}"], cwd=ROOT,
                              capture_output=True, text=True, check=True).stdout
    except Exception:
        pytest.skip("origin/main not available")
    d = json.loads(base)
    return d.get("products", d)


@pytest.mark.parametrize("name", ["products.json", "products-manual-corrections.json"])
def test_no_other_product_or_key_changes(name):
    # Review round 2: compare every product, not only this one.
    old, new = _main(name), _load(name)
    assert set(old) == set(new)
    # Later approved changes to other records (Nerve Pulse, Glen "yes" 2026-11-26).
    later = {"nerve-pulse"}
    for slug in old:
        if slug != "thermogenesis" and slug not in later:
            assert old[slug] == new[slug], slug
    changed = {k for k in set(old["thermogenesis"]) | set(new["thermogenesis"])
               if old["thermogenesis"].get(k) != new["thermogenesis"].get(k)}
    assert changed <= KEYS, changed


def test_a_rebuild_restores_all_eight_keys_from_the_pin():
    # Review round 2: start from the old raw record with every approved key gone, as a
    # rebuild from the store scrape leaves it, and require the replay to restore each.
    import sys
    sys.path.insert(0, os.path.join(ROOT, "scripts"))
    import apply_enrichment as ae
    corr = _load("products-manual-corrections.json")
    want = _load("products.json")["thermogenesis"]
    raw = {k: v for k, v in want.items() if k not in KEYS}
    raw["ingredients"] = [{"name": "Chromium Polynicotinate", "dose": "120 mcg"},
                          {"name": "Kelp", "dose": "2 mg"}]
    raw["ingredients_source"] = "db-formulations-2026-06-25"
    products = {"thermogenesis": copy.deepcopy(raw)}
    ae.apply_corrections(products, corr)
    for k in sorted(KEYS):
        assert products["thermogenesis"].get(k) == want[k], k
    # Each pinned key is what protects its field: drop it from the pin and it is lost.
    for k in sorted(KEYS - {"chat_caution"}):
        thin = copy.deepcopy(corr)
        del thin["thermogenesis"][k]
        products = {"thermogenesis": copy.deepcopy(raw)}
        ae.apply_corrections(products, thin)
        assert products["thermogenesis"].get(k) != want[k], k


def test_the_product_page_data_carries_the_approved_page(appmod):
    d = appmod.app.test_client().get("/begin/product-page-data/thermogenesis").get_json()
    s = json.dumps(d, ensure_ascii=False)
    assert next(x for x in d["sections"] if x["id"] == "caution")["body"] == WARNING
    body = next(x for x in d["sections"] if x["id"] == "ingredients")["body"]
    assert [(i["name"], i["dose"]) for i in body["ingredients"]] == ROWS
    assert body["directions"] == DIRECTIONS
    assert body["note"] == PANEL_NOTE
    for want in ("15 mcg", DIRECTIONS, "Fucoxanthin", "less than 1 mg of caffeine"):
        assert want in s, want
    for gone in ("120 mcg", "195 mcg", "Kelp", "caffeine-free"):
        assert gone not in s, gone


def test_the_product_data_route_lists_the_fourteen_rows(appmod):
    d = appmod.app.test_client().get("/begin/product-data/thermogenesis").get_json()
    s = json.dumps(d, ensure_ascii=False)
    assert "15 mcg" in s and "120 mcg" not in s
    rows = [(i["name"], i["dose"]) for i in d["ingredients"]]
    assert rows == ROWS
    for absent in ("directions", "panel_note", "warning"):
        assert absent not in d, absent


# The spec also names "thermogenesis dose". Lowercase "thermogenesis" is an everyday biology
# word, so _VINPOCETINE_EVERYDAY_NAMES names the product in lowercase only for a safety
# question (2026-10-28). Widening that is a matcher ruling, not this data change; reported
# to production. The capitalised form with a cue word counts.
@pytest.mark.parametrize("q", ["How do I take Thermogenesis?", "Thermogenesis dose?"])
def test_the_chat_block_holds_the_directions_and_the_caution(appmod, q):
    block = appmod.named_product_facts_block(q)
    assert "### Thermogenesis" in block
    assert DIRECTIONS in block
    assert WARNING in block


@pytest.mark.skipif(not os.path.exists(SPEC), reason="the vault spec is not on this machine")
def test_the_pinned_text_is_the_spec_text():
    spec = open(SPEC, encoding="utf-8").read()
    assert "> " + WARNING + "\n" in spec
    assert "> " + PANEL_NOTE + "\n" in spec
    assert f"| `directions` | `{DIRECTIONS}` |" in spec
    rows = [(n.replace("*", ""), d) for n, d in
            re.findall(r"^\d+\. (.+), ([\d.]+ (?:mcg|mg))$", spec, re.M)]
    assert rows == ROWS
