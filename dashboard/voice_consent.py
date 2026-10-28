"""The client's opt-in to the Five Element Voice Analysis.

Glen, 2026-10-27, sales tab (brief: sales/plans/2026-10-27-five-element-voice-opt-in-brief.md):
clients must opt in before the portal's Five Element Voice Analysis runs. One tick covers
the analysis and keeping its results. Unticking deletes what was kept. "No for now" to
extending it to the portal chat: a pattern the chat wrote (source portal_chat) is not
guarded here and is never deleted here.

A row in voice_analysis_consent means the client agreed. No row means they did not, or
they withdrew: withdrawing deletes the row too, so nothing is kept about the choice.

What the tick guards today is the voice pattern in member_element_state (source
portal_voice). Tone-of-voice measurements are not built yet; when they are, withdraw()
must delete them as well, and its test must say so.
"""
from datetime import datetime, timezone

VOICE_SOURCE = "portal_voice"


def _norm(email):
    return (email or "").strip().lower()


def init_table(cx):
    cx.execute("""
        CREATE TABLE IF NOT EXISTS voice_analysis_consent (
            email TEXT PRIMARY KEY,
            agreed_at TEXT NOT NULL
        )
    """)
    cx.commit()


def agreed_at(cx, email):
    """When the client's current opt-in was given, or None when there is none. A new
    tick after an untick gets a new time, so this names one opt-in, not just a yes."""
    email = _norm(email)
    if not email:
        return None
    init_table(cx)
    row = cx.execute("SELECT agreed_at FROM voice_analysis_consent WHERE email=?",
                     (email,)).fetchone()
    return row[0] if row else None


def has_consent(cx, email):
    return agreed_at(cx, email) is not None


def save_if_still_agreed(cx, email, since, element_scores):
    """Keep a voice pattern only if the opt-in in force when the recording started is
    still the one in force now. Returns the saved state, or None when it was not saved.

    A recording takes seconds to analyse, and the client can untick, or untick and tick
    again, meanwhile. Comparing agreed_at refuses both. On Postgres the opt-in row is
    locked FOR UPDATE until the pattern commits, and withdraw() deletes that row first,
    so an untick from another worker waits for this save and then deletes it too."""
    from dashboard import db
    from dashboard import member_element_state as _mes
    email = _norm(email)
    if not email or not since:
        return None
    lock = " FOR UPDATE" if db.backend_of(cx) == "postgres" else ""
    row = cx.execute("SELECT agreed_at FROM voice_analysis_consent WHERE email=?" + lock,
                     (email,)).fetchone()
    if not row or row[0] != since:
        cx.rollback()
        return None
    return _mes.upsert(cx, email, element_scores, source=VOICE_SOURCE)   # commits


def agree(cx, email):
    email = _norm(email)
    if not email:
        raise ValueError("no email")
    init_table(cx)
    cx.execute("INSERT INTO voice_analysis_consent (email, agreed_at) VALUES (?, ?) "
               "ON CONFLICT(email) DO NOTHING",
               (email, datetime.now(timezone.utc).isoformat()))
    cx.commit()


def delete_voice_pattern(cx, email):
    """Delete the pattern the voice analysis kept for this client. A backdrop choice
    on the same row is the client's own setting, not a result, so it stays. Returns
    the number of rows whose pattern was removed. Does not commit."""
    from dashboard import member_element_state as _mes
    _mes.init_table(cx)
    email = _norm(email)
    n = cx.execute("DELETE FROM member_element_state WHERE email=? AND source=? "
                   "AND scene_override IS NULL", (email, VOICE_SOURCE)).rowcount or 0
    n += cx.execute("UPDATE member_element_state SET element_scores=NULL, "
                    "dominant_element=NULL, deficient_element=NULL, source=NULL "
                    "WHERE email=? AND source=?", (email, VOICE_SOURCE)).rowcount or 0
    return n


def withdraw(cx, email):
    """Untick: delete the opt-in and everything it kept. Returns how many voice
    patterns were deleted."""
    email = _norm(email)
    if not email:
        raise ValueError("no email")
    init_table(cx)
    try:
        # The opt-in row goes first: on Postgres this waits for any save holding it
        # (save_if_still_agreed), so the pattern that save commits is deleted below.
        cx.execute("DELETE FROM voice_analysis_consent WHERE email=?", (email,))
        n = delete_voice_pattern(cx, email)
        cx.commit()
    except Exception:
        cx.rollback()
        raise
    return n


def hide_unconsented(row, consented):
    """The portal's view of a member_element_state row. A voice pattern without an
    opt-in is hidden: no scores, no elements, so no backdrop is chosen from it."""
    if not row or consented or row.get("source") != VOICE_SOURCE:
        return row
    out = dict(row)
    out.update({"element_scores": {}, "dominant_element": None,
                "deficient_element": None, "source": None})
    return out
