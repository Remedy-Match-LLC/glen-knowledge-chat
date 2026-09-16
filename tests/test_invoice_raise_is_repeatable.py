"""Glen, 2026-09-16, on the Add-remedies button: "make sure that clicking a second
time will check to add any new additions and update any changed quantities, but not
duplicate what is already added."

Most of that already held. The gap was a remedy appearing on MORE THAN ONE LAYER:
build_invoice_lines emitted one line per layer, so Steve Fox's own 15 September
report (Neuroprotect on three layers) would have raised three Neuroprotect lines.
"""
from dashboard.biofield_invoice import build_invoice_lines, merge_manual_invoice_lines

CATALOG = [{"slug": "neuroprotect", "name": "Neuroprotect"},
           {"slug": "brain-boost", "name": "Brain Boost"},
           {"slug": "transform", "name": "Transform"}]


def _lines(remedies, include_fee=False):
    return build_invoice_lines({}, remedies, CATALOG, include_fee=include_fee)["lines"]


def test_one_remedy_on_several_layers_makes_one_line():
    lines = _lines([{"name": "Neuroprotect", "qty": 1},
                    {"name": "Brain Boost", "qty": 2},
                    {"name": "Neuroprotect", "qty": 1}])
    assert [l["slug"] for l in lines] == ["neuroprotect", "brain-boost"]


def test_a_repeated_remedy_takes_the_larger_quantity_not_the_sum():
    """Two layers calling for the same remedy is one remedy taken once, so the
    bottle count is the larger of the two, never both added together."""
    lines = _lines([{"name": "Neuroprotect", "qty": 1},
                    {"name": "Neuroprotect", "qty": 3}])
    assert lines == [{"slug": "neuroprotect", "qty": 3, "source": "biofield"}]


def test_first_appearance_sets_the_order():
    lines = _lines([{"name": "Transform", "qty": 1},
                    {"name": "Neuroprotect", "qty": 1},
                    {"name": "Transform", "qty": 5}])
    assert [l["slug"] for l in lines] == ["transform", "neuroprotect"]
    assert lines[0]["qty"] == 5


def test_second_click_adds_new_remedies_and_keeps_manual_lines():
    existing = [{"slug": "neuroprotect", "qty": 1, "source": "biofield"},
                {"slug": "fungifuge", "qty": 1, "source": "self"}]
    merged = merge_manual_invoice_lines(
        _lines([{"name": "Neuroprotect", "qty": 1}, {"name": "Brain Boost", "qty": 1}]),
        existing)
    assert [l["slug"] for l in merged] == ["neuroprotect", "brain-boost", "fungifuge"]


def test_second_click_updates_a_changed_quantity():
    existing = [{"slug": "neuroprotect", "qty": 1, "source": "biofield"}]
    merged = merge_manual_invoice_lines(_lines([{"name": "Neuroprotect", "qty": 4}]),
                                        existing)
    assert merged == [{"slug": "neuroprotect", "qty": 4, "source": "biofield"}]


def test_second_click_drops_a_remedy_no_longer_recommended():
    existing = [{"slug": "neuroprotect", "qty": 1, "source": "biofield"},
                {"slug": "transform", "qty": 1, "source": "biofield"}]
    merged = merge_manual_invoice_lines(_lines([{"name": "Neuroprotect", "qty": 1}]),
                                        existing)
    assert [l["slug"] for l in merged] == ["neuroprotect"]


def test_clicking_twice_with_nothing_changed_changes_nothing():
    lines = _lines([{"name": "Neuroprotect", "qty": 2}, {"name": "Brain Boost", "qty": 1}])
    once = merge_manual_invoice_lines(lines, [])
    twice = merge_manual_invoice_lines(lines, once)
    assert twice == once
