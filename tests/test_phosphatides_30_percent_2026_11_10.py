"""The sunflower phosphatides raw is 30%, per Glen reading the bag on 2026-11-10.

FileMaker raw 5009 says 30% too. "50%" had been copied label to label into 16 live
products, their pins, the enrichment files and the worklist script.
"""
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
FILES = ("products.json", "products-manual-corrections.json",
         "products-enrich-clean.json", "products-enrich-candidate.json")
FIFTY = re.compile(r"phosphatid\w*\s*50\s*%\s*\(from sunflower|phosphatides\s*50\s*%", re.I)
TOUCHED = ["acetaldehyde-detox", "apoptogenesis", "bioavailability-blend", "curcu-guard",
           "dht-blocker", "immune-intelligence", "lens-zyme", "migrafree", "muscle-mass",
           "nerve-pulse", "night-vision", "rescue", "seaamino-syntropy", "stone-solvent",
           "vitamin-c-syntropy", "vitreous-vitality",
           "vagus-nerve-support"]  # Glen 2026-11-10 "yes": its sunflower line is the same raw


def _load(name):
    d = json.loads((DATA / name).read_text())
    return d["products"] if name == "products.json" else d


def _strings(o):
    if isinstance(o, str):
        yield o
    elif isinstance(o, dict):
        for k, v in o.items():
            yield k
            yield from _strings(v)
    elif isinstance(o, list):
        for v in o:
            yield from _strings(v)


def _phos_rows(entry):
    return [(i["name"], i["dose"]) for i in (entry.get("ingredients") or [])
            if isinstance(i, dict) and "phosphatides" in i["name"].lower()]


def test_no_decoded_string_in_any_catalogue_file_says_50_percent():
    for name in FILES:
        hits = [s for s in _strings(json.loads((DATA / name).read_text())) if FIFTY.search(s)]
        assert hits == [], (name, hits[:3])
    assert not FIFTY.search((ROOT / "scripts" / "apply_worklist.py").read_text())


def test_parsed_candidate_rows_carry_30_not_50():
    def walk(o):
        if isinstance(o, dict):
            if "hosphatides" in str(o.get("raw", "")) and o.get("unit") == "%":
                assert o["qty"] == 30.0, o
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
    walk(_load("products-enrich-candidate.json"))


def test_pins_match_live_and_a_replay_cannot_bring_back_50_percent(tmp_path, monkeypatch):
    # The enrichment files already differ from live on main (pins override them), so the
    # check is the replay itself: rebuilding products.json from clean + pins yields 30% only.
    import contextlib
    import io
    import shutil
    import sys
    live, pin = _load("products.json"), _load("products-manual-corrections.json")
    for slug in TOUCHED:
        if (pin.get(slug) or {}).get("ingredients"):
            assert _phos_rows(pin[slug]) == _phos_rows(live[slug]), slug
    copy = tmp_path / "products.json"
    shutil.copy(DATA / "products.json", copy)
    sys.path.insert(0, str(ROOT / "scripts"))
    import apply_enrichment
    monkeypatch.setattr(apply_enrichment, "PRODUCTS", str(copy))
    with contextlib.redirect_stdout(io.StringIO()):
        apply_enrichment.main()
    replayed = json.loads(copy.read_text())["products"]
    assert [s for s in _strings(replayed) if FIFTY.search(s)] == []
    for slug in ("vitreous-vitality", "curcu-guard", "migrafree"):
        assert _phos_rows(replayed[slug]) == _phos_rows(live[slug]), slug


def test_vitreous_and_stone_solvent_read_30_percent_at_13_and_21_mg():
    live, pin = _load("products.json"), _load("products-manual-corrections.json")
    assert ("Phosphatides 30% (Helianthus annuus)", "13 mg") in _phos_rows(live["vitreous-vitality"])
    assert _phos_rows(pin["vitreous-vitality"]) == _phos_rows(live["vitreous-vitality"])
    assert ("Phosphatides 30% (Helianthus annuus)", "21 mg") in _phos_rows(live["stone-solvent"])


def test_drafts_written_before_the_fix_are_reset():
    live, pin = _load("products.json"), _load("products-manual-corrections.json")
    for slug in TOUCHED:
        assert live[slug]["content_since"] >= "2026-11-10T17", slug
        if slug in pin:
            assert pin[slug]["content_since"] == live[slug]["content_since"], slug


def test_every_phosphatides_row_lands_on_one_of_the_two_pages_the_redirects_target():
    # Neither page is in FileMaker's names; both exist only through catalogue rows, so an
    # "in the index" check built from products.json passes for any slug (review, #2251).
    # Pin the two pages the old phosphatides-50 addresses redirect to (#2252, #2254) instead.
    from dashboard.ingredients import resolve, slugify
    pages = {"phosphatides-30-helianthus-annuus", "vitamin-b13-phosphatides-30-helianthus-a"}
    uses = {}
    for slug, p in _load("products.json").items():
        for ing in p.get("ingredients") or []:
            if isinstance(ing, dict) and "phosphatides" in ing["name"].lower():
                uses.setdefault(ing.get("page_slug") or slugify(ing["name"]), set()).add(slug)
    assert set(uses) <= pages, set(uses) - pages
    for page in pages:
        assert resolve(page), page
        assert len(uses.get(page, ())) >= 2, (page, uses.get(page))
