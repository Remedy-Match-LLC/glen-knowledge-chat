"""Nerve Repair lists the shelf batch, FileMaker run 114, per Glen 2026-11-10 ("yes").

Clinical found vinpocetine missing from the ingredient list while the warning named it.
The app had carried the April 2026 next-batch recipe, which has no Bioavailability Blend.
The last 11 rows are that 34.5 mg blend, amounts from blend run 107.
"""
import json
import re
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data"
LIVE = json.loads((DATA / "products.json").read_text())["products"]["nerve-repair"]
PIN = json.loads((DATA / "products-manual-corrections.json").read_text())["nerve-repair"]
CLEAN = json.loads((DATA / "products-enrich-clean.json").read_text())["nerve-repair"]

DIRECTIONS = "1 capsule up to 5 times daily with food, or as guided."
WARNING = "This formula contains vinpocetine, so do not use it if you are pregnant or could become pregnant."

# (name, dose, page_slug). A page_slug sends the row to the ingredient page other products use.
ROWS = [
    ("ALCAR (Acetyl L-Carnitine)", "160 mg", None),
    ("Lion's Mane Polysaccharides 30% (Hericium erinaceus)", "160 mg", None),
    ("Na RALA (Stabilized Bio-enhanced Na R-Lipoate)", "60 mg", None),
    ("Vitamin B1 (as Benfotiamine)", "60 mg", None),
    ("Vitamin B6 (as Pyridoxal 5-Phosphate)", "10 mg", None),
    ("Vitamin B12 (as Methylcobalamin)", "500 mcg", "vitamin-b12-methylcobalamin"),
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


def _mg(dose):
    m = re.fullmatch(r"(\d+(?:\.\d+)?) (mg|mcg)", dose)
    assert m, f"unexpected dose {dose!r}"
    return float(m.group(1)) / (1000 if m.group(2) == "mcg" else 1)


def _rows(entry):
    return [(i["name"], i["dose"], i.get("page_slug")) for i in entry["ingredients"]]


def test_every_row_dose_and_page_matches_in_live_pinned_and_clean_copies():
    for entry in (LIVE, PIN, CLEAN):
        assert _rows(entry) == ROWS


def test_six_actives_450_5_mg_plus_blend_34_5_mg():
    mg = [_mg(i["dose"]) for i in LIVE["ingredients"]]
    assert round(sum(mg[:6]), 3) == 450.5
    assert round(sum(mg[6:]), 3) == 34.5


def test_warning_and_directions_are_pinned_in_full():
    assert LIVE["warning"] == PIN["warning"] == WARNING
    assert LIVE["directions"] == PIN["directions"] == DIRECTIONS


def test_old_drafts_reset_and_a_replay_cannot_flag_or_restore_the_april_recipe():
    assert LIVE["content_since"] == PIN["content_since"] >= "2026-11-10"
    assert LIVE["enrichment_note"] == PIN["note"]
    for key in ("gk_stale", "stale_reason", "added_on_gk", "missing_on_gk"):
        assert key not in CLEAN
    assert "gk_stale" not in LIVE
    assert "nerve-repair" not in json.loads((DATA / "products-enrich-candidate.json").read_text())


def test_no_row_opens_a_new_ingredient_page():
    """Each row lands on a page that FileMaker or another product already gives a slug."""
    from dashboard.ingredients import _fmp_records, slugify
    known = {slugify((r.get("name") or "").strip().replace("\n", " "))
             for r in _fmp_records().values() if isinstance(r, dict)}
    products = json.loads((DATA / "products.json").read_text())["products"]
    for slug, p in products.items():
        if slug == "nerve-repair":
            continue
        for ing in p.get("ingredients") or []:
            if isinstance(ing, dict):
                known.add(ing.get("page_slug") or slugify(ing.get("name") or ""))
    for name, _, slug in ROWS:
        assert (slug or slugify(name)) in known, name
