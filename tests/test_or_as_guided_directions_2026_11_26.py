"""The 19 Directions lines production sent on 26 Nov 2026 (Glen "approve" in the production
tab): every dose line ends "or as guided" (Glen, 2026-10-31)."""
import copy
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
P = json.loads((ROOT / "data/products.json").read_text())["products"]
C = json.loads((ROOT / "data/products-manual-corrections.json").read_text())

APPROVED = {
    "b17-max": "Start with a quarter of a level scoop once a day with cooked food, or as guided. Increase slowly, as guided, up to 1 level scoop once a day. Do not exceed 1 level scoop a day. If you also take B17 Syntropy, take no more than 1 capsule a day, and count it as half a scoop toward the 1-scoop limit.",
    "brain-cleanse": "1 capsule at bedtime, or as guided. Turn the lights out within 30 minutes, and avoid turning lights on during the night.",
    "microbiome": "1 capsule on rising, or as guided.\n\nIt is beneficial to open the capsule and take a small amount of the powder straight into your mouth.\n\nThen close the capsule and swallow it, so the rest of the powder reaches your intestines.",
    "sleep-syntropy": "1 capsule in the middle of the night, to help you fall back to sleep, or as guided. You may also take 1 at bedtime, or at both times.",
    "soothe-powder": "1 scoop (500 mg) 2 times a day in drinks, or as guided.",
    "vitamin-a-syntropy": "1 capsule daily with food, or as guided.",
    "vitamin-c-syntropy": "1 capsule daily before food, or as guided.",
    "vitreous-vitality": "1 capsule daily with food, or as guided.",
    "allerfree-homeoenergetic-drops": "10 drops 3 times a day or as needed, or as guided.",
    "cistus-shield": "Build up to 2 capsules 3 times a day according to tolerance, with food, or as guided.",
    "curcu-guard": "1 capsule up to 3 times a day with food, or as guided.",
    "estro-clear": "Take 1 capsule 1 to 2 times daily with oils or a fatty meal, or as guided. Keep the bottle tightly closed and dry.",
    "gerd-guard": "Take 1 capsule before each meal that could cause symptoms, or even when symptoms begin, or as guided.",
    "gerd-guard-powder": "Stir 1 level scoop into a little water and sip slowly before each meal that could cause symptoms, or even when symptoms begin, or as guided.",
    "moisture-eyes-night-oil": "Shake well, then apply topically around eyes, or as guided.",
    "ocuheal-eye-drops": "1 drop in each eye 2 times a day, or as guided.",
    "ocuheal-plus-eye-drops": "1 drop in each eye 2 times a day, or as guided.",
    "reverse-age": "1 to 3 capsules a day with food, or as guided.",
    "vagus-nerve-support": "10 drops up to 3 times a day before meals, or as guided.",
}


@pytest.mark.parametrize("slug", sorted(APPROVED))
def test_both_files_hold_the_approved_line(slug):
    assert P[slug]["directions"] == APPROVED[slug]
    assert C[slug]["directions"] == APPROVED[slug]


def test_a_rebuild_keeps_the_lines_and_each_ingredient_source():
    # apply_enrichment replays an entry without ingredients_source as "manual"
    # (Glen-verified), so a directions-only entry would wrongly clear gk_stale.
    from scripts.apply_enrichment import apply_corrections
    products = copy.deepcopy(P)
    # Start from stale upstream values, so only the replay can restore them (round 2).
    for slug in APPROVED:
        products[slug]["directions"] = "STALE"
        products[slug]["ingredients_source"] = "STALE-SOURCE"
    apply_corrections(products, C)
    for slug, line in APPROVED.items():
        assert products[slug]["directions"] == line, slug
        assert products[slug].get("ingredients_source") == P[slug].get("ingredients_source"), slug


def test_every_directions_line_now_says_or_as_guided():
    missing = [s for s, p in P.items()
               if (p.get("directions") or "").strip() and "or as guided" not in p["directions"]]
    assert missing == []
