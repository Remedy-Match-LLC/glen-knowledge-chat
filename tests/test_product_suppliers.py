"""Somewhere to store what we pay for the products we sell.

Glen, 2026-09-18: "Can you add a way to store the products we sell?"

WHAT WAS ALREADY THERE, AND WHY IT COULD NOT ANSWER HIM. `product_suppliers` existed,
with a bulk FMP importer and a `list_product_suppliers` helper. Two things made it unable
to hold what he asked for:

  THE KEY DID NOT REACH THE PRODUCTS. Its only key was `fmp_product_id`, and the products
  in question have none. Measured against the live catalog: the tuning forks, the
  molecular hydrogen bottle and every Living Water ionizer are fmp_id NULL, as are 319 of
  the 1,075 live products. It is keyed on the catalog SLUG now, which every product has.

  THE READ PATH WAS DORMANT. `list_product_suppliers` had no caller outside tests, so
  nothing in the app had ever shown one of these rows. A dormant path is unproven.

WHAT PROMPTED IT. 25 held FMSP46 quotes turned out to be things Glen sells rather than
equipment: colour therapy eyewear at nine suppliers spanning $456 to $3,468, and the
5000 Hz tuning fork at five spanning $165 to $365. That is his cost basis for products in
his own catalogue, and it had nowhere to live.

BLANK IS NOT A VALUE. A blank price stores NULL, never 0, and sorts LAST rather than
first. Three FMSP46 quotes read $0.0000 because the cell was empty, and a blank supplier
counted as a supplier inflated a coverage figure by four materials. Same mistake, three
columns.
"""
import os
import sqlite3
import tempfile

import pytest

from dashboard.ingredient_catalog import init_ingredients_schema
from dashboard.materials_catalog import (add_product_supplier, init_materials_schema,
                                         list_suppliers_for_product)

FORK = "mind-tuning-fork-5000hz"


@pytest.fixture()
def db(monkeypatch):
    d = tempfile.mkdtemp()
    monkeypatch.setenv("DATA_DIR", d)
    path = os.path.join(d, "chat_log.db")
    with sqlite3.connect(path) as cx:
        init_ingredients_schema(cx)
        init_materials_schema(cx)
    return path


def test_the_slug_column_exists_and_is_indexed(db):
    with sqlite3.connect(db) as cx:
        cols = {r[1] for r in cx.execute("PRAGMA table_info(product_suppliers)")}
        idx = {r[1] for r in cx.execute("PRAGMA index_list(product_suppliers)")}
    assert "product_slug" in cols, "without this the table cannot key to 319 live products"
    assert "idx_prodsup_slug" in idx


def test_the_fmp_key_is_kept(db):
    """The FMP-imported rows still key on fmp_product_id. Adding a key must not remove one."""
    with sqlite3.connect(db) as cx:
        cols = {r[1] for r in cx.execute("PRAGMA table_info(product_suppliers)")}
    assert "fmp_product_id" in cols


def test_a_quote_can_be_stored_and_read_back(db):
    new_id = add_product_supplier(FORK, {"supplier_name": "Panther Surgical Instruments",
                                         "price": 165}, db_path=db)
    rows = list_suppliers_for_product(FORK, db_path=db)
    assert any(r["id"] == new_id for r in rows), "the insert reported an id it did not store"
    assert rows[0]["supplier_name"] == "Panther Surgical Instruments"
    assert rows[0]["price"] == 165.0


def test_the_cheapest_real_quote_comes_first(db):
    for name, price in (("Arkay Pak Instruments", 365), ("Panther Surgical", 165),
                        ("Jiyo Surgical", 340)):
        add_product_supplier(FORK, {"supplier_name": name, "price": price}, db_path=db)
    rows = list_suppliers_for_product(FORK, db_path=db)
    assert [r["price"] for r in rows] == [165.0, 340.0, 365.0]


def test_a_blank_price_is_null_and_sorts_last(db):
    """The whole point. A quote with no price is not the cheapest one, and three FMSP46
    rows read $0.0000 purely because the cell was empty."""
    add_product_supplier(FORK, {"supplier_name": "Priced", "price": 300}, db_path=db)
    add_product_supplier(FORK, {"supplier_name": "No price", "price": ""}, db_path=db)
    rows = list_suppliers_for_product(FORK, db_path=db)
    assert rows[0]["supplier_name"] == "Priced", "a blank price sorted ahead of a real one"
    assert rows[-1]["price"] is None, "a blank price was stored as a number"
    assert rows[-1]["price"] != 0, "0 would read as free"


def test_a_blank_supplier_name_is_null_not_empty_string(db):
    add_product_supplier(FORK, {"supplier_name": "   ", "price": 10}, db_path=db)
    assert list_suppliers_for_product(FORK, db_path=db)[0]["supplier_name"] is None


def test_preferred_wins_over_price(db):
    """Glen may pick a dearer supplier on quality. The flag must beat the sort."""
    add_product_supplier(FORK, {"supplier_name": "Cheap", "price": 100}, db_path=db)
    pid = add_product_supplier(FORK, {"supplier_name": "Chosen", "price": 900}, db_path=db)
    with sqlite3.connect(db) as cx:
        cx.execute("UPDATE product_suppliers SET preferred=1 WHERE id=?", (pid,))
        cx.commit()
    assert list_suppliers_for_product(FORK, db_path=db)[0]["supplier_name"] == "Chosen"


def test_quotes_for_one_product_do_not_leak_into_another(db):
    add_product_supplier(FORK, {"supplier_name": "A", "price": 1}, db_path=db)
    add_product_supplier("spirit-tuning-fork-172hz", {"supplier_name": "B", "price": 2},
                         db_path=db)
    assert len(list_suppliers_for_product(FORK, db_path=db)) == 1
    assert len(list_suppliers_for_product("spirit-tuning-fork-172hz", db_path=db)) == 1


def test_the_insert_uses_returning_not_lastrowid():
    """prod is Postgres and the db shim RAISES on lastrowid. This fails only in
    production, which is exactly the kind of bug worth a test that reads the source."""
    import pathlib
    src = (pathlib.Path(__file__).resolve().parents[1]
           / "dashboard" / "materials_catalog.py").read_text()
    body = src[src.index("def add_product_supplier"):]
    body = body[:body.index("\ndef ")]
    # Strip comments and the docstring before asserting. A first version of this test
    # failed on the COMMENT that explains why lastrowid is wrong, which is the third time
    # tonight a check has matched prose instead of code. Assert on what executes.
    code = "\n".join(l.split("#")[0] for l in body.splitlines())
    code = code.replace(body[body.index('"""'):body.index('"""', body.index('"""') + 3) + 3], "")
    assert "RETURNING id" in code
    assert "lastrowid" not in code, "cur.lastrowid raises on Postgres, and prod is Postgres"


def test_both_routes_are_registered_and_gated():
    import pathlib
    src = (pathlib.Path(__file__).resolve().parents[1] / "app.py").read_text()
    for method in ('methods=["GET"]', 'methods=["POST"]'):
        i = src.index(f'@app.route("/api/products/<slug>/suppliers", {method})')
        assert "@require_console_key" in src[i:i + 200], f"{method} route is not gated"
