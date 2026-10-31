"""Curcumin trial on the atlas (teaching, 2026-10-31).

Glen's rule (teaching tab, 31 October): a condition entry may summarise a study, and an
ingredient entry that links products carries no disease wording, only a plain reference.
So the trial summary goes on drusen, and curcumin gets the citation as a link. No link is
removed. The drawer writes the summary as raw HTML (static/atlas.js), so the approved
sentences are joined by spaces and the Source line is a link, not markdown.
"""
import json
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
URL = "https://pubmed.ncbi.nlm.nih.gov/42954735/"
TITLE = ("The Effect of Resveratrol, Quercetin, and Curcumin on Drusen Volume in Age-Related "
         "Macular Degeneration: A Randomized Clinical Trial")
REF = ("Pfahler NM, et al. 2026. " + TITLE + ". Transl Vis Sci Technol. " + URL)
DRUSEN = ("Retinal deposits linked to macular degeneration, addressed via lipase, protease, "
          "and anti-glycation strategies. A 2026 trial of 38 people compared resveratrol, "
          "quercetin and curcumin together against curcumin alone. Central drusen volume "
          "changed \"-13.4% vs. +6.9%\" over two years. The trial was small and open-label, "
          "and it had no placebo group.")
VIDEOS = ("https://youtu.be/BmoP8F5e_Bg?t=264s", "https://youtu.be/FoRE1QhSaLE?t=391s")
DISEASE = re.compile(r"\b(macular degeneration|amd|armd|drusen|glaucoma|cataract)\b", re.I)


def _concepts():
    d = json.load(open(os.path.join(ROOT, "data", "atlas-concepts.json"), encoding="utf-8"))
    return {c["id"]: c for c in d["concepts"]}


def test_drusen_carries_the_trial_and_its_source():
    c = _concepts()["drusen"]
    assert c["summary"] == DRUSEN
    assert {"source": "pubmed", "title": TITLE, "type": "article", "url": URL} in c["links"]
    assert "\n" not in c["summary"] and "](" not in c["summary"]


def test_curcumin_has_only_the_plain_reference():
    c = _concepts()["curcumin"]
    assert {"source": "pubmed", "title": REF, "type": "article", "url": URL} in c["links"]
    assert "drusen volume changed" not in json.dumps(c).lower()
    # Its own text names no disease. The citation is the trial's title, as the rule allows.
    own = [c["summary"]] + [l["title"] for l in c["links"] if l["url"] != URL]
    assert not any(DISEASE.search(t) for t in own), own


def test_no_curcumin_link_is_removed():
    c = _concepts()["curcumin"]
    urls = [l["url"] for l in c["links"]]
    assert "https://myhealingoasis.com/begin/product/curcumin" in urls
    assert all(u in urls for u in VIDEOS) and len(urls) == 4
    assert [l["title"] for l in c["links"] if l["url"] in VIDEOS] == (
        ["Dr. Glen Swartwout on eye health"] * 2)


def test_drusen_names_no_product_neighbour():
    c = _concepts()
    products = json.load(open(os.path.join(ROOT, "data", "products.json"),
                              encoding="utf-8"))["products"]
    assert not [n for n in c["drusen"]["neighbors"] if n in products]
