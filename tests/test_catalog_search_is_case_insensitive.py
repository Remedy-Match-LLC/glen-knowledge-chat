"""Console search boxes must fold case, and must do it in SQL that Postgres obeys.

Reported by the production pillar, 2026-09-18: "/api/ingredients/search is case-sensitive
in production. q=bilberry returns 0 rows and q=berry returns the Bilberry rows."

THE MECHANISM. `WHERE name LIKE ?` folds case on SQLite and does NOT on Postgres. Dev and
CI are SQLite, prod is Postgres, so this passes everywhere it is tested and fails only
where it is used. That is the shape to remember: a portability bug is invisible to a green
suite.

IT WAS FOUR SEARCHES, NOT ONE. Measured against production:

    /api/ingredients/search     curcumin  1 row   | Curcumin  8 rows
    /api/ingredients/suppliers  bio       8 rows  | Bio     100 rows
    /api/formulations/search    brain     0 rows  | Brain     3 rows
    /api/materials/search       bottle    0 rows  | Bottle   11 rows

WORSE THAN UNDER-RETURNING: the case changes the result SET. "berry" matches "Acai berry
powder" and "Berry" matches "Red Berry", so neither spelling is a superset of the other.
Nobody gets a reliable answer, and the one they do get looks complete.

WHY LOWER() AND NOT ILIKE. ILIKE is Postgres-only, so it would invert the bug and break
dev and CI instead. LOWER() on both sides is understood by both engines.
"""
import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]

# module, function, the column each one searches
SEARCHES = [
    ("dashboard/ingredient_catalog.py", "search_ingredients", "name"),
    ("dashboard/ingredient_catalog.py", "list_suppliers", "company"),
    ("dashboard/formulations.py", "search_formulations", "name"),
    ("dashboard/materials_catalog.py", "search_materials", "name"),
]


def _fn(path, name):
    src = (ROOT / path).read_text()
    i = src.index(f"def {name}(")
    nxt = src.find("\ndef ", i + 1)
    return src[i:nxt if nxt != -1 else len(src)]


@pytest.mark.parametrize("path,fn,col", SEARCHES)
def test_the_search_folds_case_on_both_sides(path, fn, col):
    body = _fn(path, fn)
    assert f"LOWER({col}) LIKE LOWER(?)" in body, (
        f"{fn} in {path} still compares raw case; it works on SQLite and fails on Postgres"
    )


@pytest.mark.parametrize("path,fn,col", SEARCHES)
def test_no_bare_like_survives_in_that_function(path, fn, col):
    """Folding one clause and leaving a second is how half a fix ships."""
    body = _fn(path, fn)
    bare = re.findall(r"(?<!LOWER\()\b" + col + r"\s+LIKE\s+\?", body)
    assert not bare, f"{fn} still has a bare `{col} LIKE ?`: {bare}"


@pytest.mark.parametrize("path,fn,col", SEARCHES)
def test_it_does_not_use_ilike(path, fn, col):
    """ILIKE would fix prod and break dev and CI: the same bug facing the other way, and
    much harder to notice because the failing environment would be the quiet one."""
    assert "ILIKE" not in _fn(path, fn).upper()


def test_the_four_searches_are_the_ones_reachable_from_the_console():
    """If a fifth search box appears, it belongs in SEARCHES. Pinned so that adding a
    route without the fold is a decision rather than an oversight."""
    app = (ROOT / "app.py").read_text()
    for route in ('@app.route("/api/ingredients/search"',
                  '@app.route("/api/formulations/search"',
                  '@app.route("/api/materials/search"'):
        assert route in app, route
    assert "_ingredients.list_suppliers(" in app


def test_the_query_actually_runs_and_folds(tmp_path):
    """The string assertions above prove the SQL is portable. This proves it is VALID and
    that it really folds, by running it against a real database with mixed-case rows.

    It runs on SQLite, which folded case anyway, so this cannot catch the Postgres bug on
    its own. It catches the other half: a LOWER() that was typed wrong, or a query that no
    longer parses. Together they cover both.
    """
    import sqlite3

    from dashboard.ingredient_catalog import init_ingredients_schema, search_ingredients

    db = str(tmp_path / "chat_log.db")
    with sqlite3.connect(db) as cx:
        init_ingredients_schema(cx)
        cx.executemany("INSERT INTO ingredients (name) VALUES (?)",
                       [("Curcumin",), ("curcumin phytosome",), ("CURCUMIN C3",),
                        ("Bilberry 25%",), ("Quercetin",)])
        cx.commit()

    lower = {r["name"] for r in search_ingredients("curcumin", db_path=db)}
    upper = {r["name"] for r in search_ingredients("CURCUMIN", db_path=db)}
    mixed = {r["name"] for r in search_ingredients("Curcumin", db_path=db)}

    assert lower == upper == mixed, (
        f"the three spellings returned different sets: {lower} / {upper} / {mixed}"
    )
    assert len(lower) == 3, f"expected all three curcumin rows, got {lower}"
    assert "Quercetin" not in lower, "the fold must not turn the search into a match-all"
