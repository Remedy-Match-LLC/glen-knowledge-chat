"""The three sublingual powders carry FileMaker's names, so a Biofield invoice finds them.

Glen, 2026-09-19, on Sharon Connour's invoice: "We need the name fixed in inventory and
code fixed where necessary, so adding her products to the invoice they all show up."

FileMaker renamed the three on 2026-09-16 (313, 338, 323). The invoice resolves a remedy
to a catalog slug by EXACT name, so the catalog must carry the same names. Each "...
Powder" twin retires onto its survivor, as #1708 does for the rest of the pairs.
"""
import json
import pathlib

from dashboard import biofield_invoice as bi

ROOT = pathlib.Path(__file__).resolve().parents[1]
CAT = json.loads((ROOT / "data" / "products.json").read_text())["products"]
RENAMED = {
    "adrenal-syntropy": ("Adrenal Syntropy Sublingual Powder", "313"),
    "endocrine-restore": ("Endocrine Restore Sublingual Powder", "338"),
    # Glen, 2026-09-19: "Vitamin B12 Sublingual Powder is the new name - update elsewhere".
    "sublingual-b12": ("Vitamin B12 Sublingual Powder", "323"),
}


def _live_catalog():
    return [{"slug": k, "name": v.get("name")} for k, v in CAT.items()
            if not v.get("inactive") and not v.get("superseded_by")]


def test_each_survivor_carries_filemakers_name_and_keeps_its_slug():
    for slug, (name, fmp) in RENAMED.items():
        assert CAT[slug]["name"] == name
        assert CAT[slug]["fmp_id"] == fmp
        assert not CAT[slug].get("inactive")


def test_the_invoice_resolves_each_new_name_to_its_survivor():
    for slug, (name, _) in RENAMED.items():
        assert bi.resolve_line_slug(name, _live_catalog()) == slug, name


def test_each_twin_retires_onto_its_survivor():
    for slug in RENAMED:
        twin = CAT[slug + "-powder"]
        assert twin.get("inactive") is True and twin.get("superseded_by") == slug


def test_price_and_was_price_are_the_twins():
    for slug in RENAMED:
        assert CAT[slug]["price_cents"] == 6997
        assert CAT[slug]["regular_cents"] == CAT[slug + "-powder"]["regular_cents"] == 8000


def test_the_chat_alias_points_at_the_live_b12_name():
    """product-aliases.json maps the clinical name to a catalog name; it had kept the
    pre-#1750 name, which no live product carried."""
    aliases = json.loads((ROOT / "data" / "product-aliases.json").read_text())["aliases"]
    assert aliases["Sublingual B12"]["catalog_name"] == CAT["sublingual-b12"]["name"]


def test_cistus_carries_glens_new_name_and_its_alias_follows():
    """Glen, 2026-09-19: "cistus now named Cistus Shield ImmuniTea - update elsewhere".
    The retired twin keeps "Cistus Synergy", which is how old references redirect."""
    aliases = json.loads((ROOT / "data" / "product-aliases.json").read_text())["aliases"]
    assert CAT["cistus-shield"]["name"] == "Cistus Shield ImmuniTea"
    assert CAT["cistus-shield"]["fmp_id"] == "1193"
    assert not CAT["cistus-shield"].get("inactive")
    assert CAT["cistus-syntropy-immunitea"]["name"] == "Cistus Synergy"
    assert CAT["cistus-syntropy-immunitea"]["superseded_by"] == "cistus-shield"
    assert aliases["Cistus Synergy"]["catalog_name"] == "Cistus Shield ImmuniTea"
