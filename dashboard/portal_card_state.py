"""Durable per-person state for cards on the portal home hub."""

from datetime import datetime, timezone

from dashboard import dbwrite


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def init_table(cx):
    cx.execute(
        """
        CREATE TABLE IF NOT EXISTS portal_card_state (
            person_id       TEXT NOT NULL,
            card_key        TEXT NOT NULL,
            first_opened_at TEXT NOT NULL,
            last_opened_at  TEXT NOT NULL,
            open_count      INTEGER NOT NULL DEFAULT 1,
            PRIMARY KEY (person_id, card_key)
        )
        """
    )
    cx.commit()


def mark_opened(cx, person_id, card_key):
    """Record an open without conflating engagement with task completion."""
    pid = str(person_id)
    key = (card_key or "").strip().lower()
    now = _now_iso()
    row = cx.execute(
        "SELECT first_opened_at, open_count FROM portal_card_state "
        "WHERE person_id=? AND card_key=?", (pid, key)
    ).fetchone()
    if row:
        cx.execute(
            "UPDATE portal_card_state SET last_opened_at=?, open_count=? "
            "WHERE person_id=? AND card_key=?",
            (now, int(row[1] or 0) + 1, pid, key),
        )
        first_opened_at = row[0]
        open_count = int(row[1] or 0) + 1
    else:
        dbwrite.insert_or_ignore(
            cx,
            "portal_card_state",
            ("person_id", "card_key", "first_opened_at", "last_opened_at", "open_count"),
            (pid, key, now, now, 1),
            conflict_cols=("person_id", "card_key"),
        )
        first_opened_at = now
        open_count = 1
    cx.commit()
    return {
        "card_key": key,
        "visited": True,
        "first_opened_at": first_opened_at,
        "last_opened_at": now,
        "open_count": open_count,
    }


def list_for_person(cx, person_id):
    rows = cx.execute(
        "SELECT card_key, first_opened_at, last_opened_at, open_count "
        "FROM portal_card_state WHERE person_id=?", (str(person_id),)
    ).fetchall()
    return {
        row[0]: {
            "visited": True,
            "first_opened_at": row[1],
            "last_opened_at": row[2],
            "open_count": int(row[3] or 0),
        }
        for row in rows
    }
