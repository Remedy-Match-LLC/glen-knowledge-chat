"""One listing per infoceutical, under the FileMaker name (Glen, production's tab, 2026-10-03).

168 live listings stood for 83 infoceuticals. The mapping is production's data file, copied
to tests/fixtures/infoceutical-merge-2026-10-03.json and applied by
scripts/merge_infoceutical_twins.py.
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ROWS = json.loads((ROOT / "tests" / "fixtures" / "infoceutical-merge-2026-10-03.json")
                  .read_text(encoding="utf-8"))


def _products():
    return json.loads((ROOT / "data" / "products.json").read_text(encoding="utf-8"))["products"]


def test_83_survivors_are_live_and_named_as_infoceuticals():
    P = _products()
    assert len(ROWS) == 83
    for r in ROWS:
        p = P[r["survivor"]]
        assert not p.get("inactive"), r["survivor"]
        assert p["name"] == r["name"], r["survivor"]
        assert "Infoceutical" in p["name"], r["survivor"]


def test_every_retired_twin_points_at_a_live_survivor():
    P = _products()
    retired = [(t, r["survivor"]) for r in ROWS for t in r["retire"]]
    assert len(retired) == 85
    for t, s in retired:
        assert P[t].get("inactive") is True, t
        assert P[t].get("superseded_by") == s, t
        assert not P[s].get("inactive"), s


def test_no_survivor_is_retired():
    survivors = {r["survivor"] for r in ROWS}
    assert not survivors & {t for r in ROWS for t in r["retire"]}


def test_survivors_carry_the_filemaker_id_and_no_live_product_shares_one():
    P = _products()
    for r in ROWS:
        if r["fmp_id"]:
            assert P[r["survivor"]].get("fmp_id") == str(r["fmp_id"]), r["survivor"]
    assert P["es1-lymph"].get("fmp_id") == "245"
    held = {}
    for s, p in P.items():
        if isinstance(p, dict) and p.get("fmp_id"):
            held.setdefault(str(p["fmp_id"]), []).append(s)
    ids = {str(r["fmp_id"]) for r in ROWS if r["fmp_id"]} | {"245"}
    shared = {i: held[i] for i in ids if len(held.get(i, [])) > 1}
    assert not shared, shared


def test_old_names_still_resolve_to_the_survivor():
    from dashboard import biofield_authoring as B
    for f in ("_catalog_exact_aliases", "_catalog_alias_map", "_superseded_name_map",
              "_deprecated_catalog_names", "_active_catalog_names"):
        getattr(B, f).cache_clear()
    names = {**B._catalog_alias_map(), **B._superseded_name_map()}
    for r in ROWS:
        for a in r["aliases"]:
            assert names.get(B._norm_name(a)) == r["name"], (a, names.get(B._norm_name(a)))


def test_the_merge_changes_no_price():
    P = _products()
    for r in ROWS:
        for t in r["retire"]:
            assert P[t]["price_cents"] == P[r["survivor"]]["price_cents"], t


def test_a_retired_twin_follows_to_its_survivor():
    from dashboard.products import superseded_slug
    P = _products()
    for r in ROWS:
        for t in r["retire"]:
            assert superseded_slug(t, P) == r["survivor"], t


def test_survivors_keep_the_bottle_and_the_quickbooks_item():
    P = _products()
    for r in ROWS:
        s = P[r["survivor"]]
        for t in r["retire"]:
            for k in ("bottle_type", "qbo_item_id"):
                if P[t].get(k):
                    assert s.get(k), (r["survivor"], k)
    assert P["ei8-microbes-liver-meridian-energetic-integrator-infoceutical"]["qbo_item_id"] == "30" or \
        P["ei8-microbes-liver-meridian-energetic-integrator-infoceutical"]["qbo_item_id"] == 30
    assert P["bfa-big-field-aligner"]["bottle_type"] == "30ml"
