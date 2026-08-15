import sqlite3

from dashboard import practitioner_portal as pp


def test_paid_cart_clears_only_when_draft_still_matches(tmp_path):
    db = tmp_path / "cart.db"
    pp.cart_set("p1", "iron-out", 2, db_path=db)
    pp.cart_set("p1", "free-and-easy", 1, db_path=db)

    assert pp.cart_clear_if_matches(
        "p1", [{"slug": "iron-out", "qty": 2},
               {"slug": "free-and-easy", "qty": 1}], db_path=db) is True
    assert pp.cart_items("p1", db_path=db) == []


def test_paid_cart_preserves_a_newer_draft(tmp_path):
    db = tmp_path / "cart.db"
    pp.cart_set("p1", "iron-out", 3, db_path=db)

    assert pp.cart_clear_if_matches(
        "p1", [{"slug": "iron-out", "qty": 2}], db_path=db) is False
    assert pp.cart_items("p1", db_path=db) == [{"slug": "iron-out", "qty": 3}]
