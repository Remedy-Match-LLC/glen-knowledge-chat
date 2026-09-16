"""Glen, 2026-09-16: "When remedies show in Biofield Intake, can you show with a
number how many bottles have been purchased in the past?"

The panel counted ORDERS containing a remedy, not bottles: a set per order meant
three bottles on one invoice counted once. And fmp_orders_for threw the FileMaker
quantities away when reshaping, which matters because most of a long-standing
client's history is in FileMaker (Debra Herndon: 2 orders here, 8 there).
"""
from dashboard.biofield_dispensed import fmp_orders_for, frequency


def _order(email, when, items, status="done"):
    return {"email": email, "created_at": when, "status": status, "items": items}


def test_bottles_sum_quantities_not_orders():
    rows = frequency([
        _order("a@b.co", "2026-01-01", [{"name": "Neuroprotect", "qty": 3}]),
        _order("a@b.co", "2026-02-01", [{"name": "Neuroprotect", "qty": 2}]),
    ], "a@b.co")
    assert len(rows) == 1
    assert rows[0]["count"] == 2        # two orders contained it
    assert rows[0]["bottles"] == 5      # five bottles in total


def test_several_bottles_on_one_order_all_count():
    rows = frequency([_order("a@b.co", "2026-01-01",
                             [{"name": "Neuroprotect", "qty": 4}])], "a@b.co")
    assert rows[0]["count"] == 1
    assert rows[0]["bottles"] == 4


def test_a_line_with_no_quantity_counts_as_one():
    rows = frequency([_order("a@b.co", "2026-01-01",
                             [{"name": "Neuroprotect"},
                              {"name": "Brain Boost", "qty": ""}])], "a@b.co")
    assert {r["name"]: r["bottles"] for r in rows} == {"Neuroprotect": 1, "Brain Boost": 1}


def test_a_zero_quantity_line_counts_as_none():
    """Real orders carry qty 0 lines — Rebecca Navo's #166 has six. They were
    listed, not bought."""
    rows = frequency([_order("a@b.co", "2026-01-01",
                             [{"name": "Neuroprotect", "qty": 0}])], "a@b.co")
    assert rows[0]["bottles"] == 0
    assert rows[0]["count"] == 1


def test_a_cancelled_order_contributes_no_bottles():
    rows = frequency([
        _order("a@b.co", "2026-01-01", [{"name": "Neuroprotect", "qty": 2}]),
        _order("a@b.co", "2026-02-01", [{"name": "Neuroprotect", "qty": 9}],
               status="cancelled"),
    ], "a@b.co")
    assert rows[0]["bottles"] == 2


def test_filemaker_quantities_survive_the_reshape():
    history = [{"client": {"name": "Debra Herndon"},
                "orders": [{"date": "2025-03-01", "status": "done",
                            "items": [{"description": "Neuroprotect 60ct", "qty": 3}]}]}]
    out = fmp_orders_for(history, "Debra Herndon", "d@h.co")
    assert out and out[0]["items"][0]["qty"] == 3


def test_bottles_combine_both_histories():
    history = [{"client": {"name": "Debra Herndon"},
                "orders": [{"date": "2025-03-01", "status": "done",
                            "items": [{"description": "Neuroprotect", "qty": 3}]}]}]
    orders = [_order("d@h.co", "2026-01-01", [{"name": "Neuroprotect", "qty": 1}])]
    rows = frequency(orders + fmp_orders_for(history, "Debra Herndon", "d@h.co"), "d@h.co")
    assert rows[0]["bottles"] == 4


def test_the_panel_shows_the_bottle_count():
    from dashboard.biofield_report_html import render_dispensed_panel
    h = render_dispensed_panel([{"name": "Neuroprotect", "slug": "neuroprotect",
                                 "count": 2, "bottles": 5, "orders_considered": 7,
                                 "pct": 29, "conditions": []}])
    assert "5 bottles" in h
    assert "2 of 7" in h


def test_one_bottle_is_singular():
    from dashboard.biofield_report_html import render_dispensed_panel
    h = render_dispensed_panel([{"name": "Neuroprotect", "slug": "neuroprotect",
                                 "count": 1, "bottles": 1, "orders_considered": 3,
                                 "pct": 33, "conditions": []}])
    assert "1 bottle" in h and "1 bottles" not in h


def test_a_row_without_the_field_does_not_crash_the_panel():
    """Older cached rows predate the field."""
    from dashboard.biofield_report_html import render_dispensed_panel
    h = render_dispensed_panel([{"name": "Neuroprotect", "slug": "", "count": 1,
                                 "orders_considered": 1, "pct": 100, "conditions": []}])
    assert "Neuroprotect" in h
