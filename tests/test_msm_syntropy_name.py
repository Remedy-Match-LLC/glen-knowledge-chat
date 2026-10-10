"""MSM Syntropy Powder's served copy and atlas concept carry its own name.

The live intro opened "MSM Synergy delivers..." (marketing-ef via formulation-9d,
2026-10-09). The product is MSM Syntropy Powder; the old name stays only as an atlas
alias so old searches still match.
"""
import json


def test_the_product_record_no_longer_says_msm_synergy():
    p = json.load(open("data/products.json"))["products"]["msm-syntropy"]
    assert "MSM Synergy" not in json.dumps(p)
    assert p["description"].startswith("MSM Syntropy Powder")


def test_the_atlas_concept_is_labelled_with_the_current_name():
    for path in ("data/atlas-concepts.json", "data/atlas-seed-input.json"):
        data = json.load(open(path))
        concepts = data["concepts"] if isinstance(data, dict) else data
        c = next(x for x in concepts if x["id"] == "msm-synergy")
        assert c["label"] == "MSM Syntropy Powder", path
        assert "msm synergy" in c["aliases"], path
        assert all(l.get("title") != "MSM Synergy" for l in c.get("links") or []), path
