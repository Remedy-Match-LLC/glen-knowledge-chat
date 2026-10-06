"""Print book prices, Glen's ruling of 2026-10-03.

All print books were hand-added on 2026-07-10 at a flat $19.97 placeholder. Glen set
the eye books and EMF Pollution Solutions at $30, and Refreshing Vision, Materia Medica
and Anima Medica at $50. Both are already whole dollars. Reads products.json by path,
so a DATA_DIR set by another test cannot redirect it.

Glen, 2026-10-06: the three large-format books sell for $40 with the $50 SRP shown
crossed out, and $20 wholesale. Their ebooks stay at $9.97.
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
    "book-refreshing-vision": 4000,
    "book-materia-medica": 4000,
    "book-anima-medica": 4000,
}

LARGE_FORMAT = ("book-refreshing-vision", "book-materia-medica", "book-anima-medica")


def test_each_ruled_print_book_has_its_price():
    for slug, cents in RULED.items():
        p = PRODUCTS[slug]
        assert p["price_cents"] == cents, slug
        assert p["bottle_type"] == "book", slug
        assert not p.get("digital"), slug


def test_large_format_books_show_the_50_dollar_srp_crossed_out():
    for slug in LARGE_FORMAT:
        assert PRODUCTS[slug]["regular_cents"] == 5000, slug


def test_large_format_books_wholesale_at_20_dollars():
    from dashboard.wholesale_pricing import order_quote
    for slug in LARGE_FORMAT:
        out = order_quote([{"slug": slug, "qty": 1}], {"modules_completed": 0},
                                    catalog=PRODUCTS)
        assert out["lines"][0]["unit_price_cents"] == 2000, slug


def test_large_format_ebooks_stay_at_997():
    for slug in LARGE_FORMAT:
        assert PRODUCTS[slug + "-ebook"]["price_cents"] == 997, slug
