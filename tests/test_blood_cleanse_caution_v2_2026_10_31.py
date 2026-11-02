"""Blood Cleanse: store caution v2 and pinned directions (2026-10-31).

Source: marketing/04 Copy/blood-cleanse-niacin-2026-10-31/change-v2.md, the "After:" text.
It replaces the one-line vinpocetine warning. The niacin dose is open with Glen, so no
ingredient row changes. Only the directions are pinned, so the chat gets the directions and
the caution but no scraped description.
"""
import json
import os

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SLUG = "blood-cleanse"
WARNING = (
    "This formula contains vinpocetine. Do not use if you are pregnant, could become "
    "pregnant, or are breastfeeding. It is not for children. Consult your physician before "
    "use if you take any medicine, including aspirin or other pain relievers. This matters "
    "most with blood pressure, heart, blood thinning, blood sugar, cholesterol or "
    "immune-suppressing medicines. Also consult first if you have liver disease, gout, a "
    "stomach ulcer, low blood pressure, a bleeding disorder, an autoimmune condition or a "
    "seizure disorder, or are planning surgery. Niacin may cause a temporary flush. Stop use "
    "and consult your physician if you notice yellowing of the skin or eyes, or dark urine. "
    "This formula may change blood pressure and blood sugar, affect bleeding, and change how "
    "some medications are processed.")
DIRECTIONS = "1 to 4 capsules daily in divided doses with food, or as guided."
# The rows on origin/main before this change (commit 7daba4fb), unchanged here.
ROWS = [
    {"name": "Niacin", "dose": "45 mg"},
    {"name": "Pyridoxal 5-Phosphate, P-5-P, P5P, Vitamin B6", "dose": "2 mg"},
    {"name": "5-MTHF", "dose": "100 mcg"},
    {"name": "TMG, Trimethylglycine", "dose": "90 mg"},
    {"name": "Magnesium Ascorbate Alkaline/Buffered Vitamin C", "dose": "63.9 mg"},
    {"name": "Vitamin D3: Cholecalciferol", "dose": "1.33 mg"},
    {"name": "Coenzyme Q10, CoQ10, Ubiquinone", "dose": "5 mg"},
    {"name": "Pyrroloquinoline Quinone PQQ", "dose": "5 mg"},
    {"name": "Watermellon", "dose": "50 mg"},
    {"name": "ALCAR", "dose": "5 mg"},
    {"name": "Allicin 25% (Allium sativum)", "dose": "120 mg"},
    {"name": "Flavonoids", "dose": "20 mg"},
    {"name": "Grape Seed PCO Procyanidolic oligomers Vitis vinifera", "dose": "10 mg"},
    {"name": "OPC 95% Pine Bark Maritime", "dose": "10 mg"},
    {"name": "Chinese pagoda tree Styphnolobium japonicum Japanese pagoda tree Chinese "
             "Scholar Sophora japonica Huai mi", "dose": "10 mg"},
    {"name": "Berberine", "dose": "20 mg"},
    {"name": "Black goji anthocyanins 25% (Lycium ruthenicum)", "dose": "10 mg",
     "page_slug": "black-goji"},
    {"name": "Cat's Claw Alkaloids Uncaria Tomentosa", "dose": "20 mg"},
    {"name": "Bioavailability Blend", "dose": "34.5 mg"},
]


def _load(name):
    d = json.load(open(os.path.join(ROOT, "data", name), encoding="utf-8"))
    return d.get("products", d)


@pytest.mark.parametrize("name", ["products.json", "products-manual-corrections.json"])
def test_the_warning_is_v2_verbatim(name):
    p = _load(name)[SLUG]
    assert p["warning"] == WARNING
    assert p["chat_caution"] is True
    assert p["directions"] == DIRECTIONS and p["directions"].endswith("or as guided.")
    assert p["copy_pinned"] == ["directions"]


def test_the_rows_and_their_source_are_unchanged():
    p, c = _load("products.json")[SLUG], _load("products-manual-corrections.json")[SLUG]
    assert p["ingredients"] == ROWS and c["ingredients"] == ROWS
    # The entry keeps the record's source, so a replay does not mark the rows Glen-verified
    # and clear gk_stale.
    assert c["ingredients_source"] == p["ingredients_source"] == "db-formulations-2026-06-25"
    assert p["gk_stale"] is True


@pytest.fixture
def appmod(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    import importlib
    import app as a
    importlib.reload(a)
    a.app.config["TESTING"] = True
    return a


def test_the_chat_facts_carry_the_breastfeeding_line(appmod):
    block = appmod._named_product_facts("Is Blood Cleanse safe while breastfeeding?") or ""
    assert "### Blood Cleanse" in block
    assert "or are breastfeeding" in block
    assert WARNING in block
    assert f"Directions: {DIRECTIONS}" in block
