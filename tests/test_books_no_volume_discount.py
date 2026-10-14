# tests/test_books_no_volume_discount.py
"""Books take no quantity discount at checkout. Glen, 2026-10-13: "no discount on books".

_engine_item marks any non-formula, non-powder product volume-eligible, so without the
explicit flag a $50 book cost $44 each for six. The catalogue is read by path, not through
_get_product, so a leaked DATA_DIR cannot swap in another catalogue."""
import json
from pathlib import Path

import app as appmod
from dashboard import pricing

CATALOG = Path(__file__).resolve().parent.parent / "data" / "products.json"


def _books():
    products = json.loads(CATALOG.read_text(encoding="utf-8"))["products"]
    return {slug: dict(p, slug=slug) for slug, p in products.items() if slug.startswith("book-")}


def test_every_book_is_off_the_volume_curve():
    books = _books()
    assert len(books) >= 27
    assert [s for s, p in books.items() if p.get("volume_eligible") is not False] == []


def test_six_books_cost_six_times_list():
    p = _books()["book-natural-eye-care"]
    out = pricing.compute([appmod._engine_item(p, 6)], settings=pricing.load_settings({}))
    assert out["discount_cents"] == 0
    assert out["total_cents"] == 6 * p["price_cents"]
