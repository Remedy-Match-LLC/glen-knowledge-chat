"""Drusen, dry AMD, wet AMD and curcumin carry no product neighbors (2026-10-31).

Communication's drusen brief (communication/handoffs/2026-10-31-drusen-education-brief.md)
keeps products off education entries about these conditions. Glen said yes in the marketing
tab to the drusen and AMD neighbors and the new link titles
(marketing/04 Copy/atlas-amd-video-titles-2026-10-31/), and in the teaching tab to the
curcumin neighbors. Only neighbors and link titles change, and one link is removed until
someone confirms it does not promote a paid program. The live graph is on Render's disk;
`00 System/scripts/atlas-patch-from-branch.py` writes the same values there.
"""
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NEIGHBORS = {
    "drusen": ["dry-armd"],
    "dry-armd": ["wet-armd", "drusen"],
    "wet-armd": ["dry-armd", "dry-eye", "golden-book"],
    "curcumin": ["tetrahydrocurcumin", "curcumin-meriva"],
}
TITLES = {
    "drusen": [("https://youtu.be/z0bkpSNttOs?t=152s",
                "Dr. Glen Swartwout discusses wet macular degeneration"),
               ("https://youtu.be/vZFm8uruXEE?t=1006s", "Dr. Glen Swartwout discusses drusen")],
    "dry-armd": [("https://youtu.be/z0bkpSNttOs?t=2924s",
                  "Dr. Glen Swartwout discusses wet and dry macular degeneration"),
                 ("https://youtu.be/FoRE1QhSaLE?t=391s",
                  "Dr. Glen Swartwout discusses macular degeneration"),
                 ("https://youtu.be/BmoP8F5e_Bg?t=264s",
                  "Dr. Glen Swartwout discusses macular degeneration")],
    "wet-armd": [("https://youtu.be/z0bkpSNttOs?t=152s",
                  "Dr. Glen Swartwout discusses wet macular degeneration"),
                 ("https://youtu.be/z0bkpSNttOs?t=2924s",
                  "Dr. Glen Swartwout discusses wet and dry macular degeneration")],
}
REMOVED = "https://youtu.be/90jArwgmOhE"


def _concepts():
    d = json.load(open(os.path.join(ROOT, "data", "atlas-concepts.json"), encoding="utf-8"))
    return {c["id"]: c for c in d["concepts"]}


def test_the_neighbors_are_conditions_and_education_only():
    c = _concepts()
    for cid, want in NEIGHBORS.items():
        assert c[cid]["neighbors"] == want, cid


def test_the_link_titles_and_urls():
    c = _concepts()
    for cid, want in TITLES.items():
        assert [(l["url"], l["title"]) for l in c[cid]["links"]] == want, cid


def test_the_heal_amd_link_is_off_these_entries():
    c = _concepts()
    for cid in TITLES:
        assert REMOVED not in json.dumps(c[cid]["links"]), cid
