"""Mitochondrial Biogenesis follows the formula and label Glen approved on 2026-11-09."""
import json
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data"
LIVE = json.loads((DATA / "products.json").read_text())["products"]["mitochondrial-biogenesis"]
PIN = json.loads((DATA / "products-manual-corrections.json").read_text())["mitochondrial-biogenesis"]

# (name, dose) pairs from the approved sheet, ~/YouCloned/Production/mitochondrial-biogenesis-40-bottles-updated.pdf
PAIRS = [
    ("Rhodiola rosea (root), 3% Salidroside", "155 mg"),
    ("Nicotinamide Riboside (Vitamin B3)", "66 mg"),
    ("Coenzyme Q10 (Ubiquinone)", "66 mg"),
    ("Bioavailability Blend (proprietary formula)", "34.5 mg"),
    ("NMN (Nicotinamide Mononucleotide)", "34 mg"),
    ("Na RALA (Stabilized Bio-enhanced Na R-Lipoate)", "33 mg"),
    ("Luteolin (Sophora japonica)", "30 mg"),
    ("Pterostilbene", "20 mg"),
    ("PQQ (Pyrroloquinoline Quinone)", "7 mg"),
    ("Riboflavin-5-Phosphate (Vitamin B2)", "5 mg"),
    ("Vitamin B12 (as Adenosylcobalamin, Methylcobalamin)", "60 mcg"),
]


def _mg(dose):
    v, u = dose.split()
    return float(v) / (1000 if u == "mcg" else 1)


def test_eleven_actives_total_450_56_mg_in_live_and_pinned_copy():
    for entry in (LIVE, PIN):
        assert [(i["name"], i["dose"]) for i in entry["ingredients"]] == PAIRS
        assert round(sum(_mg(i["dose"]) for i in entry["ingredients"]), 2) == 450.56


def test_nmn_plus_nr_is_100_mg_and_dose_ends_or_as_guided():
    by = {i["name"]: _mg(i["dose"]) for i in LIVE["ingredients"]}
    assert by["NMN (Nicotinamide Mononucleotide)"] + by["Nicotinamide Riboside (Vitamin B3)"] == 100
    assert LIVE["directions"] == PIN["directions"] == "Take 1 to 3 capsules daily with food, or as guided."
