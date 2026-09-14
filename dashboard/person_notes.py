"""Appending a dated line to people.notes, on SQLite and on Postgres.

The route did this inline with `notes || char(10) || ?`. `char(10)` is a SQLite
function; in Postgres `char(10)` is the TYPE character(10), so the statement is a
syntax error and POST /api/people/<id>/note returned 500 for every caller once
the people table moved to Postgres. Nothing tested the endpoint, so nothing said
so.

Two things are fixed here and both are deliberate.

  * The newline is a PYTHON string, not a SQL function. No dialect has an opinion
    about it, so this cannot break the same way again.
  * COALESCE on the read. The old CASE tested `notes=''`, which is false when
    notes is NULL, so a NULL row took the ELSE branch and `NULL || anything` is
    NULL in both engines. The first note on such a row would have silently wiped
    the column instead of writing.
"""
from datetime import datetime, timezone

# One statement, no CASE: COALESCE makes an absent note an empty string, and the
# separator is chosen in Python so an empty note does not gain a leading blank
# line.
_SQL = "UPDATE people SET notes = COALESCE(notes, '') || ? WHERE id=?"


def note_line(text, now=None):
    """The stored form: a UTC date stamp, then the text."""
    now = now or datetime.now(timezone.utc)
    return f"[{now.strftime('%Y-%m-%d %H:%M')}] {(text or '').strip()}"


def append_note(cx, person_id, text, now=None):
    """Append one dated line to this person's notes. Returns the line written.

    The caller owns the transaction, matching every other write in this app.
    """
    line = note_line(text, now)
    row = cx.execute("SELECT COALESCE(notes, '') AS notes FROM people WHERE id=?",
                     (person_id,)).fetchone()
    existing = (dict(row).get("notes") if row else "") or ""
    cx.execute(_SQL, (line if not existing else "\n" + line, person_id))
    return line
