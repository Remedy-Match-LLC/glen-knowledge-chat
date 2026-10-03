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


def test_only_the_dosing_line_is_carried_as_a_description():
    """Production, 2026-10-03: FileMaker's dosage field is Glen's directions. Anything
    else a twin held (ES10's product copy) is not carried."""
    P = _products()
    dosing = [r["survivor"] for r in ROWS if (P[r["survivor"]].get("description") or "")
              .startswith("build up 1 drop a day to 15 drops")]
    assert len(dosing) >= 73, len(dosing)
    assert not P["es10-video"].get("description")


def test_source_is_a_30ml_bottle():
    assert _products()["source"]["bottle_type"] == "30ml"


def test_the_cart_resolver_prefers_an_exact_name_over_a_shorter_title():
    """Review round 1: "ES1" (es1-lymph's title) is inside every ES1x name, and the
    substring pass met es1-lymph first."""
    from dashboard.practitioner_portal import name_to_slug
    P = {s: p for s, p in _products().items() if not p.get("inactive")}
    for r in ROWS:
        assert name_to_slug(r["name"], P) == r["survivor"], r["name"]


def test_an_invoice_line_under_an_old_name_reaches_the_survivor():
    from dashboard.biofield_authoring import _catalog_exact_aliases
    from dashboard.biofield_invoice import resolve_line_slug
    _catalog_exact_aliases.cache_clear()
    P = _products()
    catalog = [{"slug": s, **p} for s, p in P.items() if not p.get("inactive")]
    for old, slug in (("BFA Big Field Aligner", "bfa-big-field-aligner"),
                      ("PL Polarity", "pl-polarity"), ("ED1 Source Driver", "ed1-source-driver")):
        assert resolve_line_slug(old, catalog) == slug, old
    assert resolve_line_slug("BFA", catalog) is None   # stays for Rae


def test_the_fmp_matcher_skips_retired_twins():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "m", ROOT / "scripts" / "match_products_to_fmp.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    twin = {"name": "BFA Big Field Aligner Infoceutical", "inactive": True}
    got = m.match_products({"t": twin}, {"bfa big field aligner infoceutical": {"id_pk": "198"}})
    assert got["matched"] == {}


def test_the_bare_code_rule_covers_remedy_codes_only():
    import re
    from dashboard import biofield_invoice as BI
    src = open(BI.__file__, encoding="utf-8").read()
    pat = re.search(r'_re\.fullmatch\(r"([^"]+)", k\)', src).group(1)
    for code in ("es1", "mb 1", "bfa", "ed15", "pl"):
        assert re.fullmatch(pat, code), code
    for name in ("nac", "dha", "tmg"):
        assert not re.fullmatch(pat, name), name
