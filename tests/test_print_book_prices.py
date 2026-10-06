"""Print book prices, Glen's ruling of 2026-10-03.

All print books were hand-added on 2026-07-10 at a flat $19.97 placeholder. Glen set
the eye books and EMF Pollution Solutions at $30, and Refreshing Vision, Materia Medica
and Anima Medica at $50. Both are already whole dollars. Reads products.json by path,
so a DATA_DIR set by another test cannot redirect it.

Glen, 2026-10-06: the three large-format books sell for $40 with the $50 SRP shown
crossed out, and $20 wholesale. Their ebooks stay at $9.97.

Glen, 2026-10-06, typed in the marketing tab: "The SRP is $30. We can sell it for
$20, and $10 wholsale." Said of Dry Eye Relief; Glen extended it to all five $30
print books in money's tab the same day.
"""
import json
from pathlib import Path

PRODUCTS = json.loads((Path(__file__).resolve().parent.parent / "data" / "products.json").read_text())["products"]

RULED = {
    "book-dry-eye-relief": 2000,
    "book-cataract-solutions": 2000,
    "book-healing-glaucoma": 2000,
    "book-macular-regeneration": 2000,
    "book-emf-pollution-solutions": 2000,
    "book-refreshing-vision": 4000,
    "book-materia-medica": 4000,
    "book-anima-medica": 4000,
}

LARGE_FORMAT = ("book-refreshing-vision", "book-materia-medica", "book-anima-medica")
STANDARD = ("book-dry-eye-relief", "book-cataract-solutions", "book-healing-glaucoma",
            "book-macular-regeneration", "book-emf-pollution-solutions")


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


def test_large_format_ebooks_are_10_dollars():
    """#1919 left the ebooks at $9.97; whole dollars (Glen, 2026-10-01) take them to $10."""
    for slug in LARGE_FORMAT:
        assert PRODUCTS[slug + "-ebook"]["price_cents"] == 1000, slug


def test_standard_books_show_the_30_dollar_srp_crossed_out():
    for slug in STANDARD:
        assert PRODUCTS[slug]["regular_cents"] == 3000, slug


def test_standard_books_wholesale_at_10_dollars():
    from dashboard.wholesale_pricing import order_quote
    for slug in STANDARD:
        for qty in (1, 3, 40):
            for modules in (0, 5, 12):
                out = order_quote([{"slug": slug, "qty": qty}],
                                  {"modules_completed": modules}, catalog=PRODUCTS)
                line = out["lines"][0]
                assert line["unit_price_cents"] == 1000, (slug, qty, modules)
                assert line["line_total_cents"] == 1000 * qty, (slug, qty, modules)


def test_standard_books_retail_at_20_dollars_in_the_cart():
    import app
    for slug in STANDARD:
        for qty in (1, 3):
            out = app._price_cart([{"slug": slug, "qty": qty}],
                                  ship={"state": "CA", "country": "US"})
            assert out["subtotal_list_cents"] == 2000 * qty, (slug, qty)
            if qty == 1:  # several copies take the cart-wide volume discount, by design
                assert out["discount_cents"] == 0, slug


def test_product_page_shows_20_with_the_30_srp_crossed_out(monkeypatch, tmp_path):
    """The page reads "regular" for the struck-through price. Round 3 found nothing pinned it."""
    import app
    monkeypatch.setattr(app, "LOG_DB", str(tmp_path / "chat_log.db"))
    app._init_people_table()
    app.app.config["TESTING"] = True
    c = app.app.test_client()
    for slug in STANDARD:
        d = c.get(f"/begin/product-page-data/{slug}").get_json()
        assert (d["price"], d["regular"]) == ("$20.00", "$30.00"), slug
