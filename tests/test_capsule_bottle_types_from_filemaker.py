"""Six volume-priced products carried no bottle_type, so capsule packaging formats
(refill packs, larger bottles) could not apply to them and their shipping went to
"needs measuring". Production matched each to FileMaker on 2026-09-25: sold_size 30,
a capsule type, "1 capsule" dosage, active. Comfort was confirmed by Glen the same day. Serenity stays capsules until its stock
is used up (Glen). Transform is capsules (FileMaker 881).
"""
import json
from pathlib import Path

P = json.load(open(Path(__file__).resolve().parent.parent / "data" / "products.json"))["products"]

CONFIRMED = {   # slug: FileMaker record
    "gi-repair": 357, "lens-zyme": 412, "shields-up": 469,
    "c15-syntropy-pentadecanoic-acid": 880, "vitamin-c-syntropy": 333,
    "seaamino-syntropy": 381,
    "comfort": 586,   # Comfort Synovial Syntropy; Glen "yes" in production's tab, 2026-09-25
}


def test_the_six_confirmed_capsule_products_are_30_caps():
    assert {s: P[s].get("bottle_type") for s in CONFIRMED} == {s: "30 Caps" for s in CONFIRMED}


def test_serenity_is_capsules_until_the_stock_runs_out():
    """Glen, 2026-09-25 (platform tab): capsules stay live until used up; a powder
    reformulation comes at the next production. No powder record yet, so no fmp_id."""
    assert P["serenity"].get("bottle_type") == "30 Caps"
    assert "fmp_id" not in P["serenity"]


def test_transform_is_capsules():
    """FileMaker 881 "Transform": 30, pullulan, 1 capsule. The old 30 g came from the
    retired transform-powder twin (production, 2026-09-25)."""
    assert P["transform"]["bottle_type"] == "30 Caps"
