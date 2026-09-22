"""A short infoceutical name must be dosed as the infoceutical, not a look-alike product.

Glen, 2026-09-22: Peach Goddard's layer 9 recommended "Energy" at "10 drops 3 times a
day". Energy is an infoceutical. The dosing auto-fill found no FileMaker product named
exactly "Energy", then took the shortest product whose name starts "Energy ": Energy Flow
Flower Essence in Terrain Restore. The alias that would have named the right record was
only consulted when that loose match found nothing. Measured over the catalog, Night and
Sleep went the same way (to Night Vision and Sleep Syntropy, a capsule formula).
"""
import sqlite3

import pytest

from dashboard.biofield_authoring import remedy_dosing

_INFO = ("build up 1 drop a day to 15 drops stirred into a little water "
         "without touching metal", "daily", "on rising")


@pytest.fixture
def cx():
    c = sqlite3.connect(":memory:")
    c.execute("CREATE TABLE fmp_snap_products (product_name TEXT, dosage TEXT, "
              "dosage_freq TEXT, dosage_timing TEXT, doses_per_bottle TEXT)")
    c.executemany("INSERT INTO fmp_snap_products VALUES (?,?,?,?,?)", [
        ("Energy Flow Flower Essence in Terrain Restore", "10 drops", "3 times a day",
         "30 minutes before food", "100"),
        ("Energy/Source Infoceutical Feelgood", *_INFO, "30"),
        ("Night Vision", "1 capsule", "daily", "", "30"),
        ("Night Infoceutical", *_INFO, "30"),
        ("Sleep Syntropy", "1 capsule", "daily", "", "30"),
        ("Sleep Infoceutical", *_INFO, "30"),
        ("Adrenal Syntropy Powder", "1 scoop", "daily", "", "30"),
    ])
    return c


@pytest.mark.parametrize("name", ["Energy", "Night", "Sleep"])
def test_a_short_infoceutical_gets_the_infoceutical_protocol(cx, name):
    d = remedy_dosing(cx, name)
    assert (d["dosage"], d["frequency"], d["timing"]) == _INFO, (
        f"REGRESSION: {name} was dosed as a different product")


def test_the_prefix_match_still_serves_names_with_no_alias(cx):
    """'Adrenal Syntropy' -> 'Adrenal Syntropy Powder' is the prefix match's real job."""
    assert remedy_dosing(cx, "Adrenal Syntropy")["dosage"] == "1 scoop"


def test_an_exact_name_is_unaffected(cx):
    assert remedy_dosing(cx, "Sleep Syntropy")["dosage"] == "1 capsule"
