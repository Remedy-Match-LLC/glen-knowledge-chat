"""TDD for dashboard.orders.pack_breakdown — splits an order's shippable units
into bottle_units vs cello_pack_units by per-line `format`, mirroring
physical_units' shippable/bundle/membership rules exactly. Pure, no db/app
import (bare pytest)."""
from dashboard.orders import pack_breakdown, physical_units

CATALOG = {
    "bottle-a": {"slug": "bottle-a", "name": "Bottle A"},
    "bottle-b": {"slug": "bottle-b", "name": "Bottle B"},
    "svc-consult": {"slug": "svc-consult", "name": "Consult", "service": True},
    "pdf-guide": {"slug": "pdf-guide", "name": "PDF Guide", "info_only": True},
    "bundle-x": {
        "slug": "bundle-x", "name": "Bundle X", "bundle": True,
        "bundle_component_slugs": [
            {"slug": "bottle-a", "qty": 2},
            {"slug": "bottle-b", "qty": 1},
        ],
    },
}


def test_split_by_format_and_total_unchanged():
    items = [{"slug": "bottle-a", "qty": 2}, {"slug": "bottle-a", "qty": 3, "format": "refill"},
             {"slug": "svc-consult", "qty": 1}]
    assert {k: v for k, v in pack_breakdown(items, CATALOG).items() if k != "by_size"} == {"bottle_units": 2, "cello_pack_units": 3}
    # physical_units stays the COMBINED shippable total (reorder demand), unchanged
    assert physical_units(items, CATALOG) == 5


def test_membership_and_service_are_zero_for_both():
    items = [{"slug": "svc-consult", "qty": 1}, {"slug": "membership:month", "qty": 1},
             {"slug": "x", "kind": "membership", "qty": 1}]
    assert {k: v for k, v in pack_breakdown(items, CATALOG).items() if k != "by_size"} == {"bottle_units": 0, "cello_pack_units": 0}


def test_format_is_case_and_whitespace_insensitive():
    items = [{"slug": "bottle-a", "qty": 4, "format": "  Refill  "}]
    assert {k: v for k, v in pack_breakdown(items, CATALOG).items() if k != "by_size"} == {"bottle_units": 0, "cello_pack_units": 4}


def test_bundle_expands_and_sums_to_physical_units():
    # bundle-x = 2x bottle-a + 1x bottle-b = 3 bottles per bundle unit; qty 2 -> 6.
    # Bundles are not cello-formatted here, so all 6 land on the bottle side, and the
    # invariant bottle_units + cello_pack_units == physical_units must hold.
    items = [{"slug": "bundle-x", "qty": 2, "format": "refill"}]
    result = pack_breakdown(items, CATALOG)
    assert {k: result[k] for k in ("bottle_units", "cello_pack_units")} == \
        {"bottle_units": 0, "cello_pack_units": 6}
    assert result["bottle_units"] + result["cello_pack_units"] == physical_units(items, CATALOG)


def test_mixed_order_invariant_holds():
    items = [
        {"slug": "bottle-a", "qty": 2},
        {"slug": "bottle-b", "qty": 1, "format": "refill"},
        {"slug": "bundle-x", "qty": 1},
        {"slug": "svc-consult", "qty": 1},
        {"slug": "pdf-guide", "qty": 5},
    ]
    result = pack_breakdown(items, CATALOG)
    assert result["bottle_units"] + result["cello_pack_units"] == physical_units(items, CATALOG)


def test_by_size_counts_each_bottle_type_and_names_an_unset_one():
    """Glen, 2026-09-19: bottles of each size, so shipping can be settled before payment.
    A product with no bottle_type is named, never folded into the packer's fallback."""
    cat = {"j": {"slug": "j", "bottle_type": "30 g"},
           "d": {"slug": "d", "bottle_type": "Dropper 50 mL"},
           "u": {"slug": "u"}}
    items = [{"slug": "j", "qty": 2}, {"slug": "d", "qty": 3}, {"slug": "u", "qty": 1},
             {"slug": "j", "qty": 1}]
    assert pack_breakdown(items, cat)["by_size"] == {"30 g": 3, "Dropper 50 mL": 3,
                                                      "size not set": 1}


def test_by_size_sums_to_the_unit_counts():
    items = [{"slug": k, "qty": 1} for k in CATALOG]
    r = pack_breakdown(items, CATALOG)
    assert sum(r["by_size"].values()) == r["bottle_units"] + r["cello_pack_units"]
