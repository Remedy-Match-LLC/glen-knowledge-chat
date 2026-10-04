"""Vitamin P Polyphenols is now Vascular Integrity: Vitamin P Plus TECA (FMP 374).

Glen, 2026-10-03: "Vitamin P Polyphenols are gone, replaced by Vascular Integrity. It
needs to change everywhere including on Agnes' report and invoice." Minimum Program had
put the old name on her chain. The old name matched no FMP product, so the layer had no
dosing and no schedule slot. It also matched no catalog name exactly, so the invoice
skipped it.

The catalog record keeps its slug, gains fmp_id 374, and opts its old names in through
`report_aliases`. Program resolves the name before it looks up dosing, so the old name
now lands as the new one with 1 capsule daily with food, at Breakfast.
"""
import json
import sqlite3
from pathlib import Path

import pytest

import dashboard.biofield_authoring as ba
from dashboard.biofield_authoring import resolve_remedy_name, remedy_dosing
from dashboard.biofield_invoice import resolve_line_slug
from dashboard.biofield_schedule import build_schedule

ROOT = Path(__file__).resolve().parent.parent
SLUG = "vitamin-p-polyphenols"
NEW = "Vascular Integrity: Vitamin P Plus TECA"
OLD = "Vitamin P Polyphenols"
_CACHED = ("_deprecated_catalog_names", "_active_catalog_names", "_superseded_name_map",
           "_catalog_alias_map", "_catalog_exact_aliases")


@pytest.fixture(autouse=True)
def _clear_caches():
    def clear():
        for fn in _CACHED:
            f = getattr(ba, fn, None)
            if f is not None and hasattr(f, "cache_clear"):
                f.cache_clear()
    clear()
    yield
    clear()


@pytest.fixture
def cx():
    c = sqlite3.connect(":memory:")
    c.execute("CREATE TABLE fmp_snap_products(id_pk TEXT, product_name TEXT, active TEXT, "
              "type TEXT, dosage TEXT, dosage_freq TEXT, dosage_timing TEXT)")
    c.execute("INSERT INTO fmp_snap_products VALUES('374',?,'1','Supplement',"
              "'1 capsule','daily','with food')", (NEW,))
    return c


def _catalog():
    return json.loads((ROOT / "data" / "products.json").read_text())["products"]


def test_catalog_record_carries_the_fmp_id_and_old_name():
    p = _catalog()[SLUG]
    assert p["name"] == NEW
    assert p["fmp_id"] == "374"
    assert OLD in p["aliases"]
    assert p["report_aliases"] is True


@pytest.mark.parametrize("spoken", [OLD, OLD.lower(), "Vitamin P: Polyphenols", NEW])
def test_old_name_resolves_to_the_new_one(cx, spoken):
    assert resolve_remedy_name(cx, spoken) == NEW


def test_resolved_name_gets_dosing_and_a_breakfast_slot(cx):
    d = remedy_dosing(cx, resolve_remedy_name(cx, OLD))
    assert d == {"dosage": "1 capsule", "frequency": "daily", "timing": "with food"}
    (entry,) = build_schedule([{"name": NEW, **d}])["entries"]
    assert entry["slots"] == ["Breakfast"]
    assert entry["as_directed"] is False


def test_invoice_bills_both_names():
    cat = [dict(v, slug=k) for k, v in _catalog().items()]
    assert resolve_line_slug(NEW, cat) == SLUG
    assert resolve_line_slug(OLD, cat) == SLUG


def test_name_maps_carry_the_new_name():
    aliases = json.loads((ROOT / "data" / "product-aliases.json").read_text())
    assert NEW in json.dumps(aliases)
    assert f'"catalog_name": "{OLD}"' not in (ROOT / "data" / "product-aliases.json").read_text()
    theory = (ROOT / "data" / "clinical_theory_catalog.json").read_text()
    assert f'"name": "{OLD}"' not in theory


def test_scan_reveal_with_the_old_name_lands_as_the_new_one(tmp_path):
    """The remote reveal still names the old product. Coverage and the chain row must
    both carry the live name, or the balancing panel loses the match."""
    from dashboard.biofield_authoring import create_test, init_auth_tables
    from dashboard.biofield_reveal_import import build_coverage, import_layers_to_test
    c = sqlite3.connect(str(tmp_path / "c.db"))
    init_auth_tables(c)
    c.execute("CREATE TABLE fmp_snap_products(id_pk TEXT, product_name TEXT, dosage TEXT, "
              "dosage_freq TEXT, dosage_timing TEXT)")
    c.execute("INSERT INTO fmp_snap_products VALUES('374',?,'1 capsule','daily','with food')",
              (NEW,))
    layers = [{"n": 1, "title": "Vessels", "most_affected": "Capillaries",
               "remedy_name": OLD, "codes": ["ED7"]}]
    assert build_coverage(layers, c) == {NEW.lower(): {"ED7"}}
    tid = create_test(c, "J", "j@x.com", "2026-10-03")
    import_layers_to_test(c, tid, layers)
    row = c.execute("SELECT remedy, dosage, frequency, timing FROM biofield_auth_chain").fetchone()
    assert tuple(row) == (NEW, "1 capsule", "daily", "with food")


def _fmp(*names):
    c = sqlite3.connect(":memory:")
    c.execute("CREATE TABLE fmp_snap_products(product_name TEXT)")
    c.executemany("INSERT INTO fmp_snap_products VALUES(?)", [(n,) for n in names])
    return c


def test_unknown_names_pass_through_unchanged():
    from dashboard.biofield_authoring import exact_canonical_name
    c = _fmp(NEW)
    assert exact_canonical_name(c, "  Neuro Magnesium ") == "Neuro Magnesium"
    assert exact_canonical_name(c, "") == ""
    assert exact_canonical_name(c, None) == ""
    assert exact_canonical_name(None, OLD) == OLD          # no snapshot, no redirect


def test_terrain_restore_suffix_survives_the_redirect():
    from dashboard.biofield_authoring import exact_canonical_name
    assert exact_canonical_name(_fmp(NEW), OLD + " in Terrain Restore") == NEW + " in Terrain Restore"
    assert exact_canonical_name(_fmp(NEW), OLD.upper() + " in terrain restore") == NEW + " in Terrain Restore"


def test_a_name_filemaker_still_sells_is_never_rewritten():
    """Round 1: every retired name redirecting moved 12 live FileMaker names, 8 onto
    different dosing. Each of these has its own FileMaker row and must stay itself."""
    from dashboard.biofield_authoring import exact_canonical_name, _superseded_name_map
    from dashboard.biofield_authoring import _catalog_exact_aliases
    moved = list(_superseded_name_map()) + list(_catalog_exact_aliases())
    assert moved, "no redirects loaded, so this test proves nothing"
    c = _fmp(*moved, *_superseded_name_map().values(), *_catalog_exact_aliases().values())
    for n in moved:
        assert exact_canonical_name(c, n) == n


def test_no_redirect_when_filemaker_lacks_the_target():
    """ES1's live catalog name has no FileMaker row, so redirecting would lose dosing."""
    from dashboard.biofield_authoring import exact_canonical_name
    assert exact_canonical_name(_fmp(), "ES1 Lymph Energetic Star Infoceutical") \
        == "ES1 Lymph Energetic Star Infoceutical"
    assert exact_canonical_name(_fmp(), OLD) == OLD
