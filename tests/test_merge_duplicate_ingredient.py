"""Folding a console-created duplicate ingredient into its original.

The 2026-09-18 FMSP46 canary minted four duplicates of existing prod records (5-HTP,
7-Keto DHEA, Gum Arabic, Acai 20:1), each carrying one supplier quote. This is the
cleanup, and its guards matter more than its happy path: it deletes a row.
"""
import sqlite3

import pytest

from dashboard.ingredient_catalog import (
    create_ingredient, create_source, get_ingredient, init_ingredients_schema,
    list_sources_for_ingredient, merge_duplicate_ingredient,
)


@pytest.fixture
def db(tmp_path):
    path = str(tmp_path / "chat_log.db")
    with sqlite3.connect(path) as cx:
        init_ingredients_schema(cx)
        cx.execute("INSERT INTO ingredients(name, fmp_id) VALUES('Acai 20:1 original', '3251')")
        cx.execute("CREATE TABLE formulation_items(id INTEGER PRIMARY KEY, ingredient_id INTEGER)")
        cx.commit()
    return path


def _original(db):
    with sqlite3.connect(db) as cx:
        return cx.execute("SELECT id FROM ingredients WHERE fmp_id='3251'").fetchone()[0]


def test_sources_move_and_the_duplicate_is_gone(db):
    orig = _original(db)
    dup = create_ingredient({"name": "Acai 20:1 dup"}, db_path=db)
    create_source(dup, {"supplier_name": "Acme", "price_per_unit": "12"}, db_path=db)
    create_source(orig, {"supplier_name": "Existing"}, db_path=db)

    out = merge_duplicate_ingredient(dup, orig, db_path=db)

    assert out == {"merged": dup, "into": orig, "sources_moved": 1}
    assert get_ingredient(dup, db_path=db) is None
    names = sorted(s["supplier_name"] for s in list_sources_for_ingredient(orig, db_path=db))
    assert names == ["Acme", "Existing"]


def test_an_fmp_record_is_never_deleted(db):
    orig = _original(db)
    other = create_ingredient({"name": "console one"}, db_path=db)
    with pytest.raises(ValueError, match="FileMaker"):
        merge_duplicate_ingredient(orig, other, db_path=db)
    assert get_ingredient(orig, db_path=db) is not None


def test_a_duplicate_in_use_by_a_formula_is_refused(db):
    orig = _original(db)
    dup = create_ingredient({"name": "in a formula"}, db_path=db)
    create_source(dup, {"supplier_name": "Acme"}, db_path=db)
    with sqlite3.connect(db) as cx:
        cx.execute("INSERT INTO formulation_items(ingredient_id) VALUES(?)", (dup,))
        cx.commit()
    with pytest.raises(ValueError, match="formulation_items"):
        merge_duplicate_ingredient(dup, orig, db_path=db)
    # Refused means untouched: the source did not move either.
    assert len(list_sources_for_ingredient(dup, db_path=db)) == 1


def test_a_canonical_target_is_refused(db):
    orig = _original(db)
    dup = create_ingredient({"name": "canonical of something"}, db_path=db)
    with sqlite3.connect(db) as cx:
        cx.execute("UPDATE ingredients SET canonical_id=? WHERE id=?", (dup, orig))
        cx.commit()
    with pytest.raises(ValueError, match="canonical_id"):
        merge_duplicate_ingredient(dup, orig, db_path=db)


def test_self_and_missing_ids_are_refused(db):
    orig = _original(db)
    dup = create_ingredient({"name": "x"}, db_path=db)
    with pytest.raises(ValueError, match="itself"):
        merge_duplicate_ingredient(dup, dup, db_path=db)
    with pytest.raises(ValueError, match="no ingredient 999"):
        merge_duplicate_ingredient(dup, 999, db_path=db)
    with pytest.raises(ValueError, match="no ingredient 998"):
        merge_duplicate_ingredient(998, orig, db_path=db)
