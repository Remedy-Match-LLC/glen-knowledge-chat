from pathlib import Path


def test_mark_all_fulfilled_persists_instead_of_only_filling_inputs():
    html = (Path(__file__).resolve().parents[1] / "static" / "console-orders.html").read_text()

    assert "Mark all fulfilled" in html
    assert "function fulfillAllLines(id)" in html
    assert "fulfillLines(id);" in html
    assert "function fillAllLines(id)" not in html
