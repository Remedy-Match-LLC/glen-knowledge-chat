"""Six volume-priced products carried no bottle_type, so capsule packaging formats
(refill packs, larger bottles) could not apply to them and their shipping went to
"needs measuring". Production matched each to FileMaker on 2026-09-25: sold_size 30,
a capsule type, "1 capsule" dosage, active. Comfort was confirmed by Glen the same day. Serenity is the drink mix (Glen), a
30 g powder, so it gets no capsule formats.
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


def test_serenity_is_the_drink_mix_not_capsules():
    """Glen, 2026-09-25 (production's tab): the drink mix, FileMaker 1081, 30 g powder."""
    assert (P["serenity"].get("bottle_type"), P["serenity"].get("fmp_id")) == ("30 g", "1081")
