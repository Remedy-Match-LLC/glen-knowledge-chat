"""Nerve Repair lists the shelf batch, FileMaker run 114, per Glen 2026-11-10 ("yes").

Clinical found vinpocetine missing from the ingredient list while the warning named it.
The app had carried the April 2026 next-batch recipe, which has no Bioavailability Blend.
"""
import json
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data"
LIVE = json.loads((DATA / "products.json").read_text())["products"]["nerve-repair"]
PIN = json.loads((DATA / "products-manual-corrections.json").read_text())["nerve-repair"]
DIRECTIONS = "1 capsule up to 5 times daily with food, or as guided."


def _mg(dose):
    v, u = dose.split()
    return float(v) / (1000 if u == "mcg" else 1)


def test_vinpocetine_is_listed_at_1_mg_in_live_and_pinned_copy():
    for entry in (LIVE, PIN):
        by = {i["name"]: i["dose"] for i in entry["ingredients"]}
        assert by["Vinpocetine 99% (Vinca minor), in the Bioavailability Blend"] == "1 mg"
        assert "vinpocetine" in entry.get("warning", LIVE["warning"])


def test_run_114_actives_and_the_34_5_mg_blend_add_up():
    for entry in (LIVE, PIN):
        blend = [i for i in entry["ingredients"] if i["name"].endswith("in the Bioavailability Blend")]
        assert len(blend) == 11
        assert round(sum(_mg(i["dose"]) for i in blend), 2) == 34.5
        assert round(sum(_mg(i["dose"]) for i in entry["ingredients"]), 2) == 485.0
        names = json.dumps(entry["ingredients"])
        assert "ALCAR" in names and "Tocotrienols" not in names and "Copper" not in names


def test_dose_ends_or_as_guided_and_a_rerun_cannot_restore_the_april_recipe():
    assert LIVE["directions"] == PIN["directions"] == DIRECTIONS
    clean = json.loads((DATA / "products-enrich-clean.json").read_text())["nerve-repair"]
    assert clean["ingredients"] == LIVE["ingredients"]
    assert "nerve-repair" not in json.loads((DATA / "products-enrich-candidate.json").read_text())
