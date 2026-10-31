"""Comfort: two app records become one (Glen, 2026-10-30, formulation tab: "yes").

comfort-synovial-syntropy carries the label's name and FileMaker 586, so it stays. comfort
retires onto it, and connective-tissue-support points at it directly. The survivor takes the
January 2026 label's rows (Comfort_0126_1.88x6in.ai), with the 135 mg polyphenol complex as
one row and its five items in panel_note, plus Glen's directions and description ("approve").
Spec: production/05 Formulations/_store-updates/2026-10-31-comfort-merge.md.
"""
import importlib
import json
import os
import shutil

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KEEP = "comfort-synovial-syntropy"
LEGACY = "https://remedymatch.com/remedies/syntropy/428-comfort"
ROWS = [
    ("Oxindole Alkaloids 5% (Uncaria tomentosa)", "33 mg"),
    ("Ginger Gingerols 10% (Zingiber officinalis)", "33 mg"),
    ("MSM (Methylsulfonylmethane)", "65 mg"),
    ("Polyphenol Complex: Proprietary Formula", "135 mg"),
    ("Hyaluronic Acid (Vegan)", "33 mg"),
    ("Black Seed Thymoquinone 20% (Nigella sativa)", "33 mg"),
    ("Notoginsenosides 80% (Panax notoginseng)", "33 mg"),
    ("Hemp CBD (Cannabidiol) (Cannabis sativa)", "7 mg"),
    ("Frankincense Boswellic Acid (Boswellia serrata)", "100 mg"),
]
PANEL = ("Polyphenol Complex: Proprietary Formula contains: Turmeric Tetrahydrocurcumin "
         "(Curcuma longa), Pine OPCs (Oligomeric Proanthocyanidins) (Pinus pinaster), Scholar "
         "Tree EMIQ (Enzymatically Modified Isoquercetin) (Sophora japonica), Pterostilbene, "
         "Trans-Resveratrol (Polygonum cuspidatum).")
DIRECTIONS = "1 to 3 capsules daily with food, or as guided."
DESCRIPTION = ("Each vegicap of Comfort Synovial Syntropy combines frankincense boswellic acid, "
               "MSM, ginger, cat's claw, black seed, notoginseng and vegan hyaluronic acid. It "
               "adds a 135 mg polyphenol complex of tetrahydrocurcumin, pine OPCs, EMIQ, "
               "pterostilbene and trans-resveratrol, and hemp CBD.")
GONE = ("collagen", "eggshell", "avocado", "paeoniflorin", "akba")


def _load(name):
    d = json.load(open(os.path.join(ROOT, "data", name), encoding="utf-8"))
    return d.get("products", d)


def _assert_merged(products):
    assert products["comfort"]["inactive"] is True
    assert products["comfort"]["superseded_by"] == KEEP
    assert "legacy_store_url" not in products["comfort"]   # moved, not copied (round 2)
    assert products["connective-tissue-support"]["superseded_by"] == KEEP
    s = products[KEEP]
    assert not s.get("inactive") and not s.get("superseded_by")
    assert s["legacy_store_url"] == LEGACY
    assert [(r["name"], r["dose"]) for r in s["ingredients"]] == ROWS
    assert s["ingredients_source"] == "label-read-2026-10-31"
    assert (s["directions"], s["description"], s["panel_note"]) == (DIRECTIONS, DESCRIPTION, PANEL)
    assert s["content_since"].endswith("+00:00")


def test_the_catalog_holds_one_live_comfort():
    _assert_merged(_load("products.json"))


def test_only_the_survivor_has_a_corrections_entry():
    c = _load("products-manual-corrections.json")
    assert "comfort" not in c and "connective-tissue-support" not in c
    e = c[KEEP]
    assert [(r["name"], r["dose"]) for r in e["ingredients"]] == ROWS
    assert (e["directions"], e["description"], e["panel_note"]) == (DIRECTIONS, DESCRIPTION, PANEL)
    assert e["content_since"] == _load("products.json")[KEEP]["content_since"]


def test_no_row_the_label_lacks_survives():
    text = json.dumps(_load("products.json")[KEEP]["ingredients"]).lower()
    for word in GONE:
        assert word not in text, word


def test_a_rebuild_keeps_the_merge(tmp_path, monkeypatch):
    """The spec's replay test: main() on a copy keeps the retirement, both redirects, the
    moved store address, the new rows, the directions and the description."""
    from scripts import apply_enrichment as ae
    for name, attr in (("products.json", "PRODUCTS"), ("products-enrich-clean.json", "CLEAN"),
                       ("products-manual-corrections.json", "CORRECTIONS")):
        dst = tmp_path / name
        shutil.copy(os.path.join(ROOT, "data", name), dst)
        monkeypatch.setattr(ae, attr, str(dst))
    ae.main()
    _assert_merged(json.load(open(tmp_path / "products.json"))["products"])


def test_the_other_files_name_the_survivor():
    assert json.load(open(os.path.join(ROOT, "data", "fmp_slug_map.json")))["resolved"]["586"] == KEEP
    related = json.load(open(os.path.join(ROOT, "data", "related-harvested.json")))
    assert related[KEEP] == related["comfort"]
    recs = json.dumps(json.load(open(os.path.join(ROOT, "data", "practice_recommendations.json"))))
    assert f'"slug": "{KEEP}"' in recs and '"slug": "comfort"' not in recs


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    import app as a
    importlib.reload(a)
    a.app.config["TESTING"] = True
    return a


@pytest.mark.parametrize("old", ["comfort", "connective-tissue-support"])
def test_both_old_pages_land_on_the_survivor(client, old):
    r = client.app.test_client().get(f"/begin/product/{old}?utm_source=x")
    assert r.status_code == 302
    assert r.headers["Location"].endswith(f"/begin/product/{KEEP}?utm_source=x")


def test_old_store_id_428_reaches_the_survivor():
    from dashboard import legacy_store_links as lsl
    products = _load("products.json")
    assert lsl.build_map(products)["428"] == lsl._page_for(KEEP, products)


def test_the_survivor_page_shows_the_label_rows_and_new_directions(client, monkeypatch):
    monkeypatch.setattr(client, "_product_card",
                        lambda p: {"description": "AI CARD MARKER", "ingredients": [],
                                   "benefits": ["AI BENEFIT MARKER"]})
    monkeypatch.setattr(client, "_product_how", lambda p: "AI HOW MARKER")
    d = client.app.test_client().get(f"/begin/product-page-data/{KEEP}").get_json()
    secs = {s["id"]: s for s in d["sections"]}
    body = secs["ingredients"]["body"]
    assert [(r["name"], r["dose"]) for r in body["ingredients"]] == ROWS
    assert body["directions"] == DIRECTIONS
    page = json.dumps(d).lower()
    assert "1 capsule daily with food" not in page
    for word in GONE:
        assert word not in page, word


OLD_DRAFT = "2026-10-30T12:00:00+00:00"   # before the merge: describes the old rows


@pytest.mark.parametrize("generated_at,kept", [(OLD_DRAFT, False), ("2099-01-01T00:00:00+00:00", True)])
def test_the_cutoff_drops_a_cached_draft_written_before_it(monkeypatch, generated_at, kept):
    """Round 2: the cache must really discard text written before content_since."""
    from dashboard import product_content as pc
    product = dict(_load("products.json")[KEEP], slug=KEEP)
    assert OLD_DRAFT < product["content_since"]
    hit = {"content": {"text": "OLD: collagen and eggshell"}, "sources": [],
           "generated_at": generated_at, "cached": True}
    monkeypatch.setattr(pc, "_cache_get", lambda slug, ctype: hit)
    monkeypatch.setattr(pc, "_cache_put", lambda *a: None)
    monkeypatch.setattr(pc, "_page_text", lambda p: None)
    monkeypatch.setattr(pc, "_page_text_from_product", lambda p: None)
    monkeypatch.setattr(pc, "_generate_how_it_works", lambda p, page: {"text": "NEW"})
    got = pc.get_or_generate(product, "how_it_works")
    assert (got is hit) is kept


def test_the_sales_page_drafts_written_before_the_cutoff_are_reset():
    import sqlite3
    from dashboard import sales_pages as sp
    cx = sqlite3.connect(":memory:")
    sp.upsert_section(cx, KEEP, "how", "OLD: collagen and eggshell")
    cx.execute("UPDATE sales_pages SET generated_at=? WHERE product_slug=?", (OLD_DRAFT, KEEP))
    assert sp.reset_if_older(cx, KEEP, _load("products.json")[KEEP]["content_since"]) is True
    assert sp.get_page(cx, KEEP)["content"] == {}
