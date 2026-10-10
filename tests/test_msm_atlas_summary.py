"""The msm-synergy atlas summary is Glen's approved sentence (2026-10-10)."""
import json


def test_the_atlas_summary_is_glens_approved_sentence():
    # Glen, 2026-10-10, formulation tab. The old summary's three eye claims came from
    # the 06-03 auto-extract and were never approved.
    want = ("An MSM drink powder with vitamin C, tetrahydrocurcumin and biotin, "
            "for comfort, scar reduction and cleansing.")
    for path in ("data/atlas-concepts.json", "data/atlas-seed-input.json"):
        data = json.load(open(path))
        concepts = data["concepts"] if isinstance(data, dict) else data
        c = next(x for x in concepts if x["id"] == "msm-synergy")
        assert c["summary"] == want, path
