"""Pure powders are sold, never a Remedy Match, never promoted by chat.

Glen, 2026-10-03, relayed by production: the rule covers the single-ingredient store
powders and the food powders ("food powders too"). 19 live slugs, plus 3 inactive twins.
A second batch of 48 live slugs came from production the same day.
A formula that contains MSM or quercetin must stay unaffected.
"""
import json
import pathlib
import re

from dashboard import reveal_screen
from dashboard.pure_powders import PURE_POWDER_NAMES, PURE_POWDER_SLUGS, is_pure_powder

REPO = pathlib.Path(__file__).resolve().parent.parent
CATALOG = json.loads((REPO / "data" / "products.json").read_text(encoding="utf-8"))["products"]


def _norm(t):
    return re.sub(r"\s+", " ", (t or "").strip().lower())


def test_every_slug_is_in_the_catalog_and_67_are_live():
    missing = sorted(s for s in PURE_POWDER_SLUGS if s not in CATALOG)
    assert not missing, missing
    live = [s for s in PURE_POWDER_SLUGS if not CATALOG[s].get("inactive")]
    # 19 first batch, 48 second, less serrapeptase: off sale 2026-10-07, because Glen
    # ruled it is never sold as a powder (it needs an enteric capsule).
    assert len(live) == 66, sorted(live)


def test_names_are_exactly_the_catalog_names_of_the_slugs():
    assert {_norm(CATALOG[s]["name"]) for s in PURE_POWDER_SLUGS} == PURE_POWDER_NAMES


def test_they_stay_sellable():
    for s in PURE_POWDER_SLUGS:
        if s in ("hydrolyzed-collagen-powder", "quercetin-dihydrate-powder-60-grams",
                 "seaamino-powder", "serrapeptase"):
            continue
        assert not CATALOG[s].get("inactive"), s


def test_formulas_with_msm_or_quercetin_are_unaffected():
    hits = []
    for s, v in CATALOG.items():
        if not isinstance(v, dict) or s in PURE_POWDER_SLUGS:
            continue
        if re.search(r"msm|quercetin", json.dumps(v.get("ingredients", "")).lower()):
            hits.append(s)
            assert not is_pure_powder(slug=s, name=v.get("name")), s
    assert len(hits) >= 10, "fixture lost: no MSM or quercetin formulas found"


def test_a_word_inside_a_name_does_not_match():
    for n in ("MSM Powder Blend", "Quercetin Syntropy", "Comfort with MSM", "TMG Plus"):
        assert not is_pure_powder(name=n), n
    assert is_pure_powder(name="  MSM   powder ")
    assert is_pure_powder(slug="TMG")


def test_the_reveal_screen_drops_them():
    row = {"layers": [{"remedy": {"slug": "msm-powder", "name": "MSM Powder"}}],
           "remedies": [{"slug": "", "name": "Bone Broth powder"},
                        {"slug": "vitreous-vitality", "name": "Vitreous Vitality"}]}
    out = reveal_screen.screen(row)
    assert out["layers"][0]["remedy"] is None
    assert [r["name"] for r in out["remedies"]] == ["Vitreous Vitality"]


def test_the_matcher_excludes_them():
    src = (REPO / "app.py").read_text(encoding="utf-8")
    body = src[src.index("def _ff_auto_excluded("):src.index("def _parse_ff_rank(")]
    code = "\n".join(l for l in body.splitlines() if not l.strip().startswith("#"))
    assert "_not_promoted_powder(name=nl)" in code


def test_the_chat_prompt_says_do_not_volunteer_them():
    src = (REPO / "app.py").read_text(encoding="utf-8")
    assert "The pure powders (" in src and "do not volunteer or recommend them" in src


def test_the_matcher_drops_them_and_keeps_formulas():
    import importlib
    import app as a
    importlib.reload(a)
    for n in ("MSM Powder", "Quercetin Dihydrate", "Bone Broth powder", "TMG", "SeaAmino Powder"):
        assert a._ff_auto_excluded(n), n
    for n in ("Vitreous Vitality", "Immune Modulation", "MSM Powder Blend"):
        assert not a._ff_auto_excluded(n), n


def test_the_pricing_helper_is_not_shadowed():
    import app as a
    assert a._is_pure_powder({"name": "Sumac Bran 50:1 Pure Powder", "slug": "x"})
    assert not a._is_pure_powder({"name": "MSM Powder", "slug": "msm-powder"})


def test_second_batch_examples():
    for s in ("curcumin", "coq10", "piperine", "licorice-omnipotent", "hydrolyzed-whey"):
        assert is_pure_powder(slug=s), s
    for n in ("Curcumin", "CoQ10", "Licorice Omnipotent", "Pea Protein Aminos"):
        assert is_pure_powder(name=n), n
    # A formula that names one of these ingredients is not matched
    for n in ("Curcumin Syntropy", "CoQ10 Plus", "Spike Shield"):
        assert not is_pure_powder(name=n), n


def test_no_unlisted_product_is_caught():
    caught = [s for s, v in CATALOG.items() if isinstance(v, dict)
              and s not in PURE_POWDER_SLUGS and is_pure_powder(slug=s, name=v.get("name"))]
    assert not caught, caught
