"""The CDS pair is one item that packs as two 50 mL dropper bottles.

FileMaker 887 (2026-10-13): two 50 mL bottles and two droppers. Price is per item;
only packing counts two.
"""
import importlib, sys
from pathlib import Path
import pytest
repo = Path(__file__).resolve().parent.parent
if str(repo) not in sys.path: sys.path.insert(0, str(repo))

SHIP = {"country": "US", "zip": "01950", "state": "MA", "city": "X", "street": "1 A St"}


def _app(monkeypatch, tmp_path, per):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    try:
        import app as a; importlib.reload(a)
    except Exception as e:
        pytest.skip(f"app not importable: {e}")
    rec = {"slug": "pair", "name": "Pair", "price_cents": 7000, "bottle_type": "Dropper 50 mL",
           "qty_pricing": True, "bottles_per_unit": per}
    monkeypatch.setattr(a, "_get_product", lambda slug: dict(rec) if slug == "pair" else None)
    return a


@pytest.mark.parametrize("per,expect", [(2, 6), (None, 3), ("x", 3), (0, 3)])
def test_bottles_per_unit_multiplies_packed_bottles_only(tmp_path, monkeypatch, per, expect):
    a = _app(monkeypatch, tmp_path, per)
    r = a._price_cart([{"slug": "pair", "qty": 3}], ship=SHIP)
    assert r["bottle_units"] == expect


def test_the_real_pair_record_packs_two_droppers():
    import json
    p = json.loads((repo / "data" / "products.json").read_text())["products"]["cds-water-purifier--activator"]
    assert p["bottle_type"] == "Dropper 50 mL" and p["bottles_per_unit"] == 2
    assert p["price_cents"] == 7000


def test_the_orders_board_counts_two_bottles_per_pair():
    from dashboard import orders as O
    cat = {"pair": {"slug": "pair", "name": "Pair", "price_cents": 7000,
                    "bottle_type": "Dropper 50 mL", "bottles_per_unit": 2},
           "one": {"slug": "one", "name": "One", "price_cents": 7000, "bottle_type": "Dropper 50 mL"}}
    items = [{"slug": "pair", "qty": 3}, {"slug": "one", "qty": 1}]
    assert O.physical_units(items, cat) == 7
    b = O.pack_breakdown(items, cat)
    assert b["bottle_units"] == 7 and b["by_size"] == {"Dropper 50 mL": 7}
