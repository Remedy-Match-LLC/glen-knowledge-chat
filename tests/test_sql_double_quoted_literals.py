"""No SQL string may quote a VALUE with double quotes.

SQLite falls back to treating "visible" as a string literal; Postgres reads it as
an IDENTIFIER. Prod runs Postgres, so

    WHERE id=? AND status="visible"

raised `psycopg.errors.UndefinedColumn: column "visible" does not exist` on every
portal Reserve click, while every SQLite-backed test passed. Group Coaching sat at
one registration for the day as a result.

A behavioural test cannot catch this class -- under SQLite both spellings work --
so this scans the source instead, via the parser rather than a regex over raw
text (which runs across string boundaries and trips on row["col"] dict access).
"""
import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FILES = [ROOT / "app.py"] + sorted((ROOT / "dashboard").glob("*.py"))

# A real SQL fragment STARTS with a SQL keyword. SQL is often split across
# concatenated pieces, so a continuation like "FROM x WHERE y" counts too.
# Prose docstrings that merely mention SELECT, and HTML/JS blobs containing
# <select ...>, are excluded -- both produced false positives.
SQL_HINT = re.compile(
    r"^\s*(SELECT|INSERT|UPDATE|DELETE|FROM|WHERE|AND|OR|SET|VALUES|JOIN|LEFT|INNER|"
    r"ORDER BY|GROUP BY|HAVING|LIMIT)\b", re.I)
NOT_SQL = re.compile(r"[<>]{1}[a-zA-Z/]|function\s|=>")
BAD_VALUE = re.compile(
    '(?:=|<>|!=|\\bLIKE\\b|\\bIN\\b\\s*\\()\\s*"[A-Za-z_][A-Za-z0-9_ %-]*"')


def _sql_strings(path):
    """Every string CONSTANT in the file that looks like SQL, with its line."""
    # Fail LOUD on a syntax error. Skipping the file would make this guard pass
    # for the worst possible reason -- it did exactly that during development.
    tree = ast.parse(path.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            v = node.value
            if SQL_HINT.search(v) and not NOT_SQL.search(v):
                yield getattr(node, "lineno", 0), v


def test_no_sql_string_uses_double_quotes_around_a_value():
    offenders = []
    for path in FILES:
        for line, chunk in _sql_strings(path):
            m = BAD_VALUE.search(chunk)
            if m:
                offenders.append("%s:%d  ...%s..." % (path.name, line, m.group(0)))
    assert offenders == [], (
        "double-quoted value in SQL; Postgres reads it as a column name:\n  "
        + "\n  ".join(offenders))


def test_the_guard_catches_the_shape_that_broke_reserve():
    """Mutation check on the checker, so it cannot quietly pass forever."""
    bad = 'SELECT zoom_meeting_id FROM calendar_events WHERE id=? AND status="visible"'
    good = "SELECT zoom_meeting_id FROM calendar_events WHERE id=? AND status='visible'"
    assert BAD_VALUE.search(bad), "the guard would not have caught the live bug"
    assert not BAD_VALUE.search(good), "the guard misfires on the correct spelling"
    # And it must not fire on ordinary Python dict access appearing near SQL.
    assert not BAD_VALUE.search('SELECT a FROM t WHERE b=?  # row["consumed_at"]')


def test_the_reserve_query_is_fixed():
    text = (ROOT / "app.py").read_text()
    assert 'status="visible"' not in text
    assert "status='visible'" in text
