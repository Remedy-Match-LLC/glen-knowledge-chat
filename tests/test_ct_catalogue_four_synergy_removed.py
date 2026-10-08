"""Four inactive Synergy products are out of the Clinical Theory catalogue (Glen "yes", production, 2026-10-08).

Spec: production/05 Formulations/_store-updates/2026-10-08-ct-catalogue-remove-four-synergy.md.
Copper, ATP, SAMe and Glucoraphanin Synergy are not sold. TMG Syntropy Powder (FileMaker 368)
is the SAMe remedy, to support SAMe recycling. The unserved snapshot fields are left alone.
"""
import json

GONE = ("Copper Synergy", "ATP Synergy", "SAMe Synergy", "Glucoraphanin Synergy")


def _entries():
    return {e["slug"]: e for e in json.load(open("data/clinical_theory_catalog.json"))["dimensions"][3]["entries"]}


def _served(entry):
    return json.dumps({k: v for k, v in entry.items() if not k.startswith("description_snapshot")})


def test_no_served_field_names_the_four():
    for slug, e in _entries().items():
        for name in GONE:
            assert name not in _served(e), (slug, name)


def test_same_recommends_tmg_syntropy_powder():
    e = _entries()["s-adenosyl-methionine"]
    assert e["remedies"] == [{"name": "TMG Syntropy Powder",
                              "url": "https://myhealingoasis.com/begin/product/tmg-syntropy-powder"}]
    assert "so TMG Syntropy Powder supplies Methyl donors that recycle Homocysteine" in e["description"]


def test_the_remaining_remedies_stay():
    e = _entries()
    assert e["copper"]["remedies"] == []
    assert [r["name"] for r in e["phosphorus"]["remedies"]] == ["Bone Builder"]
    assert [r["name"] for r in e["sulfur"]["remedies"]] == ["Sulfur Syntropy", "Glutathione Syntropy", "Rescue"]
    assert "Zinc Syntropy" in e["chlorine"]["description_live"]
    assert "Zinc Synergy" not in e["chlorine"]["description_live"]
