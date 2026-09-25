"""Six volume-priced products carried no bottle_type, so capsule packaging formats
(refill packs, larger bottles) could not apply to them and their shipping went to
"needs measuring". Production matched each to FileMaker on 2026-09-25: sold_size 30,
a capsule type, "1 capsule" dosage, active. Comfort and Serenity are probable but
unconfirmed and stay unset until Glen rules.
"""
import json
from pathlib import Path

P = json.load(open(Path(__file__).resolve().parent.parent / "data" / "products.json"))["products"]

CONFIRMED = {   # slug: FileMaker record
    "gi-repair": 357, "lens-zyme": 412, "shields-up": 469,
    "c15-syntropy-pentadecanoic-acid": 880, "vitamin-c-syntropy": 333,
    "seaamino-syntropy": 381,
}


def test_the_six_confirmed_capsule_products_are_30_caps():
    assert {s: P[s].get("bottle_type") for s in CONFIRMED} == {s: "30 Caps" for s in CONFIRMED}


def test_the_unconfirmed_two_stay_unset():
    assert not P["comfort"].get("bottle_type")
    assert not P["serenity"].get("bottle_type")
