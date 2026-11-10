"""Blood Cleanse and Man Manna state what is in the capsule (Glen, 2026-11-10, "yes").

Clinical asked for Fred Kotowitz. Blood Cleanse's warning named vinpocetine, but its
panel listed only "Bioavailability Blend 34.5 mg". Its D3 read 1.33 mg, a premix
weight. Man Manna's zinc read 55 mg of the bisglycinate, not the elemental zinc.
Sources: FileMaker run 34 (Blood Cleanse), run 143 (Man Manna), blend run 107.
"""
import json
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data"
LIVE = json.loads((DATA / "products.json").read_text())["products"]
PIN = json.loads((DATA / "products-manual-corrections.json").read_text())

BLEND = [
    ("Phosphatides 30% (Helianthus annuus)", "13 mg", None),
    ("Ginkgo European Standard 10:1 (Ginkgo biloba)", "8 mg", "ginkgo-european-std-ginkgo-biloba"),
    ("Vitamin B3 (as Niacin)", "5 mg", None),
    ("Moringa 100:1 (Moringa oleifera)", "3 mg", None),
    ("DGL Licorice (Glycyrrhiza glabra)", "2 mg", None),
    ("Vinpocetine 99% (Vinca minor)", "1 mg", "vinpocetine-vinca-minor"),
    ("Gingerols 10% (Zingiber officinale)", "0.5 mg", "gingerols-10-zingiber-officinalis"),
    ("Piperine 95% (Piper nigrum)", "0.5 mg", None),
    ("Humic Acid", "0.5 mg", None),
    ("Fulvic Acid", "0.5 mg", None),
    ("Spirit Minerals (ORMUS)", "0.5 mg", "11-spirit-minerals"),
]


def _rows(entry):
    return [(i["name"], i["dose"], i.get("page_slug")) for i in entry["ingredients"]]


def test_blood_cleanse_lists_the_blend_line_by_line_with_vinpocetine_1_mg():
    for entry in (LIVE["blood-cleanse"], PIN["blood-cleanse"]):
        rows = _rows(entry)
        assert rows[-11:] == BLEND
        assert len(rows) == 29
        assert not any(n == "Bioavailability Blend" for n, _, _ in rows)
    assert LIVE["blood-cleanse"]["ingredients"] == PIN["blood-cleanse"]["ingredients"]
    assert LIVE["blood-cleanse"]["warning"].startswith("This formula contains vinpocetine.")


def test_blood_cleanse_d3_reads_665_iu_from_the_premix():
    for entry in (LIVE["blood-cleanse"], PIN["blood-cleanse"]):
        d3 = [i for i in entry["ingredients"] if i["name"] == "Vitamin D3: Cholecalciferol"]
        assert d3 == [{"name": "Vitamin D3: Cholecalciferol", "dose": "16.6 mcg (665 IU)",
                       "grade": "500,000 IU/g", "compound_mg": 1.33}]
        # 1.33 mg x 500 IU/mg = 665 IU; 40 IU = 1 mcg
        assert round(1.33 * 500) == 665 and round(665 / 40, 1) == 16.6


def test_man_manna_zinc_is_elemental_and_pinned():
    for entry in (LIVE["man-manna"], PIN["man-manna"]):
        zinc = [i for i in entry["ingredients"] if "inc" in i["name"]]
        assert zinc == [{"name": "Zinc (as Zinc Bisglycinate, 55 mg)", "dose": "11 mg",
                         "page_slug": "zinc-glycinate"}]
    assert LIVE["man-manna"]["ingredients"] == PIN["man-manna"]["ingredients"]


def test_old_drafts_reset_and_notes_match():
    for slug in ("blood-cleanse", "man-manna"):
        assert LIVE[slug]["content_since"] == PIN[slug]["content_since"] >= "2026-11-10T17"
        assert LIVE[slug]["enrichment_note"] == PIN[slug]["note"]


def test_no_row_opens_a_new_ingredient_page():
    from dashboard.ingredients import _fmp_records, slugify
    known = {slugify((r.get("name") or "").strip().replace("\n", " "))
             for r in _fmp_records().values() if isinstance(r, dict)}
    for slug, p in LIVE.items():
        if slug in ("blood-cleanse", "man-manna"):
            continue
        for ing in p.get("ingredients") or []:
            if isinstance(ing, dict):
                known.add(ing.get("page_slug") or slugify(ing.get("name") or ""))
    for slug in ("blood-cleanse", "man-manna"):
        for ing in LIVE[slug]["ingredients"]:
            assert (ing.get("page_slug") or slugify(ing["name"])) in known, (slug, ing["name"])
