"""GastroZyme: the December 2024 label on the shelf bottles (2026-10-31).

Spec: production/05 Formulations/_store-updates/2026-10-31-gastrozyme-contents.md, section
"App record gastrozyme (platform)". Glen read the bottle on 31 October and ruled protease
150 mg; the label misprints 444 mg. The label's "Zingiber officinalis" is written as the
botanical "officinale". The iodine line is the one Glen approved on 31 October (production
tab). The rows are pinned, so the chat must carry the caution too (chat_caution).
"""
import copy
import json
import os
import shutil

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SLUG = "gastrozyme"
ROWS = [("Zinc (as Zinc Carnosine)", "5 mg"), ("Iodine 10% (in Kelp spp.)", "100 mcg"),
        ("Protease (600,000 units)", "150 mg"), ("Lipase (1,000 FIP units)", "50 mg"),
        ("Betaine HCl (Beta vulgaris)", "30 mg"), ("Gentian (Gentiana lutea)", "20 mg"),
        ("L-Carnosine (as Zinc Carnosine)", "16.7 mg"),
        ("Gingerol 40% (Zingiber officinale)", "10 mg")]
DIRECTIONS = "1 capsule with each heavier meal, or as guided."
WARNING = ("Each capsule contains about 100 mcg of iodine, two thirds of the adult daily "
           "requirement. If you have a thyroid condition or take other iodine products, "
           "consult your health professional before use.")
NOTE = ("December 2024 label (Glen read the bottle 31 Oct 2026). FileMaker 322 aligned "
        "31 Oct. Protease 150 mg per Glen 31 Oct; label misprints 444 mg.")
DROPPED = ("Butyrate", "Berberine", "Dandelion", "Artichoke", "Lemon Balm", "Adenosine")


def _load(name):
    d = json.load(open(os.path.join(ROOT, "data", name), encoding="utf-8"))
    return d.get("products", d)


def _check(p):
    assert [(i["name"], i["dose"]) for i in p["ingredients"]] == ROWS
    assert p["directions"] == DIRECTIONS and p["warning"] == WARNING
    assert p["chat_caution"] is True and p["copy_pinned"] == ["ingredients", "directions"]
    assert p["ingredients_source"] == "label-read-2026-10-31"
    assert p["content_since"].endswith("+00:00")
    assert not any(w.lower() in json.dumps(p["ingredients"]).lower() for w in DROPPED)


@pytest.mark.parametrize("name", ["products.json", "products-manual-corrections.json"])
def test_both_files_carry_the_label(name):
    _check(_load(name)[SLUG])


def test_the_note_records_the_label_read():
    assert _load("products-manual-corrections.json")[SLUG]["note"] == NOTE
    assert _load("products.json")[SLUG]["enrichment_note"] == NOTE


def test_the_replay_restores_it_onto_the_old_record():
    # Start from the 2023 rows with no flags, as the spec asks.
    from scripts.apply_enrichment import apply_corrections
    old = copy.deepcopy(_load("products.json")[SLUG])
    old["ingredients"] = [{"name": "Iodine", "dose": "1 mg"}, {"name": "Berberine", "dose": "20 mg"}]
    old["ingredients_source"] = "db-formulations-2026-06-25"
    for k in ("directions", "warning", "chat_caution", "copy_pinned", "content_since",
              "enrichment_note"):
        old.pop(k, None)
    products = {SLUG: old}
    apply_corrections(products, {SLUG: _load("products-manual-corrections.json")[SLUG]})
    _check(products[SLUG])
    assert products[SLUG]["enrichment_note"] == NOTE


def test_a_full_rebuild_keeps_it(tmp_path, monkeypatch):
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
    monkeypatch.setattr(a, "_product_card",
                        lambda p: {"description": "AI CARD MARKER", "ingredients": [],
                                   "benefits": ["AI BENEFIT MARKER"]})
    monkeypatch.setattr(a, "_product_how", lambda p: "AI HOW MARKER")
    return a


def test_the_page_shows_the_rows_directions_and_caution(appmod):
    d = appmod.app.test_client().get(f"/begin/product-page-data/{SLUG}").get_json()
    secs = {s["id"]: s for s in d["sections"]}
    body = secs["ingredients"]["body"]
    assert [(i["name"], i["dose"]) for i in body["ingredients"]] == ROWS
    assert body["directions"] == DIRECTIONS and secs["caution"]["body"] == WARNING


def test_the_chat_facts_carry_the_rows_and_the_caution(appmod):
    block = appmod._named_product_facts("What is in GastroZyme?") or ""
    for n, dose in ROWS:
        assert f"{n} {dose}" in block or f"{n}: {dose}" in block or f"{n} ({dose})" in block, n
    assert DIRECTIONS in block
    assert WARNING in block
    assert not any(w in block for w in DROPPED)
