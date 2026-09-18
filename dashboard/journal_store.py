"""Local sqlite store for voice-journal entries.

Re-homed from a Supabase REST table (`journal_entries`) after that project went
dark. Lives in the app's existing LOG_DB (chat_log.db) alongside orders/biofield.
JSON-shaped columns are stored as TEXT and decoded on read so callers see the
same dict/list shapes the old PostgREST rows had. No Flask dependency; the caller
passes a sqlite3 connection.
"""
import json
import sqlite3

from dashboard import dbwrite

# Columns that hold JSON structures (stored as TEXT, decoded on read).
_JSON_COLS = (
    "emotion_scores", "tcm_scores", "top_emotions", "polyvagal_state",
    "congruence", "lexical_metrics", "top_themes", "transcript_embedding",
    "mapper_check", "metadata",
)
_SCALAR_COLS = (
    "user_id", "recorded_at", "duration_seconds", "transcript",
    "dominant_element", "dominant_treasure",
)
_ALL_COLS = _SCALAR_COLS + _JSON_COLS


def init_table(cx):
    cx.execute(
        """
        CREATE TABLE IF NOT EXISTS journal_entries (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          user_id TEXT,
          recorded_at TEXT,
          duration_seconds DOUBLE PRECISION,
          transcript TEXT,
          dominant_element TEXT,
          dominant_treasure TEXT,
          emotion_scores TEXT, tcm_scores TEXT, top_emotions TEXT,
          polyvagal_state TEXT, congruence TEXT, lexical_metrics TEXT,
          top_themes TEXT, transcript_embedding TEXT, mapper_check TEXT,
          metadata TEXT,
          created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
        )
        """
    )
    cx.execute(
        "CREATE INDEX IF NOT EXISTS idx_journal_recorded_at "
        "ON journal_entries(recorded_at)"
    )
    cx.commit()


def insert(cx, record: dict):
    """Insert one entry. Returns [{"id": <new id>}] to match the shape the
    analyze route expects (it reads saved[0]['id'])."""
    init_table(cx)
    vals = []
    for c in _ALL_COLS:
        v = record.get(c)
        vals.append(json.dumps(v) if c in _JSON_COLS else v)
    placeholders = ",".join("?" * len(_ALL_COLS))
    new_id = dbwrite.insert_returning_id(cx,
        f"INSERT INTO journal_entries ({','.join(_ALL_COLS)}) VALUES ({placeholders})",
        vals,
    )
    cx.commit()
    return [{"id": new_id}]


def _decode(row: sqlite3.Row) -> dict:
    d = dict(row)
    for c in _JSON_COLS:
        if c in d and isinstance(d[c], str):
            try:
                d[c] = json.loads(d[c])
            except (ValueError, TypeError):
                d[c] = None
    return d


def select(cx, *, since_iso: str, order: str = "desc", limit: int | None = None):
    """Entries with recorded_at >= since_iso, ordered by recorded_at. JSON columns
    are decoded back to structures. `order` is 'desc' or 'asc'."""
    init_table(cx)
    cx.row_factory = sqlite3.Row
    direction = "ASC" if str(order).lower() == "asc" else "DESC"
    sql = ("SELECT * FROM journal_entries WHERE recorded_at >= ? "
           f"ORDER BY recorded_at {direction}")
    params = [since_iso]
    if limit is not None:
        sql += " LIMIT ?"
        params.append(int(limit))
    return [_decode(r) for r in cx.execute(sql, params).fetchall()]


def delete_entry(cx, entry_id):
    """Delete ONE journal entry outright. Returns True if a row went.

    Glen, 2026-09-18: a "Delete this chat session" box, unchecked by default, one per
    session. This is the whole-session half of that; paragraph-level is a later step and
    needs the row RE-ANALYSED rather than edited, because the scores were computed over
    the whole transcript.

    THE WHOLE ROW GOES, not just the transcript. A journal row carries
    transcript_embedding, top_themes, emotion_scores, polyvagal_state, lexical_metrics
    and congruence, all derived from what the person said and several near-reversible.
    Blanking the transcript and keeping the embedding would leave a machine-readable
    trace of the same words while claiming the session was deleted.
    """
    cur = cx.execute("DELETE FROM journal_entries WHERE id=?", (int(entry_id),))
    cx.commit()
    return bool(cur.rowcount)
