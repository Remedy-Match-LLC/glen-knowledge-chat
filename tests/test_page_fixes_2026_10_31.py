"""Page fixes for four eye formulas (Glen "approve", communication tab, 2026-10-31).

Source: communication/drafts/2026-10-31-page-fixes/changes.md, version 2, after three
blind reviews. Every dose line ends ", or as guided", and Scar Solve's zinc line names
AREDS eye vitamins. Scar Silk's directions field, which the page and the chat read, now
says the same two sentences as its description.
"""
import json
import os
import shutil

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FILES = ("products.json", "products-manual-corrections.json")
WANT = {
    "macular-wellness-lycopene": ["Take 1 vegicap a day with food, or as guided."],
    "lipid-cleanse": ["Take 1 vegicap a day with food, or as guided."],
    "scar-solve": [
        "Take 1 to 2 vegicaps a day with food, or as guided.",
        "Even one vegicap taken with an AREDS or AREDS2 eye vitamin can go over that limit.",
        "Other eye vitamins vary, so check the zinc on the label."],
    "scar-silk": [
        "Take 1 vegicap a day between meals, or as guided.",
        "Up to 3 vegicaps a day may be used, or as guided."],
}
GONE = {
    "macular-wellness-lycopene": ["Take 1 vegicap a day with food."],
    "lipid-cleanse": ["Take 1 vegicap a day with food."],
    "scar-solve": ["Take 1 to 2 vegicaps a day with food.", "Two vegicaps taken with"],
    "scar-silk": ["Take 1 vegicap a day between meals.", "Up to 3 a day may be used."],
}
SILK_DIRECTIONS = ("Take 1 vegicap a day between meals, or as guided. Up to 3 vegicaps a day "
                   "may be used, or as guided.")


def _data(path):
    doc = json.load(open(path, encoding="utf-8"))
    return doc.get("products", doc)


def _check(products):
    for slug, lines in WANT.items():
        paras = products[slug]["description"].split("\n\n")
        for line in lines:
            assert paras.count(line) == 1, (slug, line)
        for old in GONE[slug]:
            assert not any(p == old or p.startswith(old) for p in paras), (slug, old)
    assert products["scar-silk"]["directions"] == SILK_DIRECTIONS
    # The zinc lines follow the dose line directly (changes.md).
    paras = products["scar-solve"]["description"].split("\n\n")
    i = paras.index(WANT["scar-solve"][1])
    assert paras[i + 1] == WANT["scar-solve"][2]
    assert paras[i - 1].startswith("The US upper limit")


@pytest.mark.parametrize("name", FILES)
def test_both_files_carry_the_approved_lines(name):
    _check(_data(os.path.join(ROOT, "data", name)))


def test_lipid_zyme_is_unchanged():
    for name in FILES:
        assert _data(os.path.join(ROOT, "data", name))["lipid-zyme"]["directions"] == (
            "1 capsule 1 to 3 times daily between meals, or as guided.")


def test_a_rebuild_keeps_them(tmp_path, monkeypatch):
    from scripts import apply_enrichment as ae
    for name, attr in (("products.json", "PRODUCTS"), ("products-enrich-clean.json", "CLEAN"),
                       ("products-manual-corrections.json", "CORRECTIONS")):
        shutil.copy(os.path.join(ROOT, "data", name), tmp_path / name)
        monkeypatch.setattr(ae, attr, str(tmp_path / name))
    ae.main()
    _check(_data(tmp_path / "products.json"))
