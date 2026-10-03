"""The 7 Mithreal listings carry the copy Glen approved in rnd on 2026-10-02.

The old intros said 80% of autoimmune issues cleared with head shielding, and two said
1918 deaths were due to bacteria on face masks. Neither is in the cited sources
(rnd/review/2026-10-02-claim-rewording). Each intro is pinned so AI copy cannot
replace it, and is written in plain text because the page sets it with textContent.
"""
import json
from pathlib import Path

PRODUCTS = Path(__file__).resolve().parent.parent / "data" / "products.json"

HEAD = ("mithreal-knit-beanie-with-44-silver",
        "mithreal-silver-head-face-shield",
        "mithreal-silver-hoodie-hat-with-42-silver")
BODY = ("mithreal-silver-long-sleeve-shirt",
        "mithreal-silver-socks",
        "mithreal-silver-t-shirt",
        "mithreal-t-shirt-with-70-silver")

QUOTE = ("“A full 90 % of the 64 patients reported a “Definite” "
         "or “Strong” change in their symptoms.”")


def _products():
    return json.loads(PRODUCTS.read_text(encoding="utf-8"))["products"]


def test_no_mithreal_intro_repeats_the_retired_claims():
    for slug in HEAD + BODY:
        intro = _products()[slug]["intro"]
        assert "80%" not in intro, slug
        assert "1918" not in intro, slug
        assert "&trade;" not in intro, slug


def test_every_mithreal_intro_is_pinned():
    for slug in HEAD + BODY:
        assert "intro" in _products()[slug].get("copy_pinned", []), slug


def test_head_items_quote_the_study_verbatim_with_source_and_breaks():
    for slug in HEAD:
        intro = _products()[slug]["intro"]
        assert QUOTE in intro, slug
        assert "There was no comparison group." in intro, slug
        assert "https://pmc.ncbi.nlm.nih.gov/articles/PMC5406447/" in intro, slug
        assert intro.count("\n\n") == 2, slug


def test_body_intros_are_whole_sentences():
    for slug in BODY:
        intro = _products()[slug]["intro"].rstrip(".")
        assert intro and intro[-1].isalpha(), slug
        assert _products()[slug]["intro"].endswith("."), slug
