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
