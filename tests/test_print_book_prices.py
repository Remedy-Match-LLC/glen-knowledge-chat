"""Print book prices, Glen's ruling of 2026-10-03.

All print books were hand-added on 2026-07-10 at a flat $19.97 placeholder. Glen set
the eye books and EMF Pollution Solutions at $30, and Refreshing Vision, Materia Medica
and Anima Medica at $50. Both are already whole dollars. Reads products.json by path,
so a DATA_DIR set by another test cannot redirect it.
"""
import json
from pathlib import Path

PRODUCTS = json.loads((Path(__file__).resolve().parent.parent / "data" / "products.json").read_text())["products"]

RULED = {
    "book-dry-eye-relief": 3000,
    "book-cataract-solutions": 3000,
    "book-healing-glaucoma": 3000,
    "book-macular-regeneration": 3000,
    "book-emf-pollution-solutions": 3000,
    "book-refreshing-vision": 5000,
    "book-materia-medica": 5000,
    "book-anima-medica": 5000,
}


def test_each_ruled_print_book_has_its_price():
    for slug, cents in RULED.items():
        p = PRODUCTS[slug]
        assert p["price_cents"] == cents, slug
        assert p["bottle_type"] == "book", slug
        assert not p.get("digital"), slug
