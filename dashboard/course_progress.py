"""Per-learner MentorshipU course progress: per-lesson 'watched' + per-module
homework. Pure: stdlib + the caller's sqlite3 connection only; never imports app.
A module is completed when every lesson in it is watched AND the homework is
submitted. Once earned, completion is recorded and kept: adding a lesson to a
module later does not take the credit away (Glen, 2026-10-09). Only a module's
`completion_reset` date in course.yaml clears credit earned before it.
Reads never raise (they run on request paths)."""
from __future__ import annotations

import time


def _norm(s: str | None) -> str:
    return (s or "").strip().lower()


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def init_progress_tables(cx) -> None:
    cx.execute(
        "CREATE TABLE IF NOT EXISTS course_lesson_watched("
        "email TEXT NOT NULL, course TEXT NOT NULL, module TEXT NOT NULL, "
        "lesson TEXT NOT NULL, watched_at TEXT, "
        "UNIQUE(email, course, module, lesson))")
    cx.execute(
        "CREATE TABLE IF NOT EXISTS course_module_homework("
        "email TEXT NOT NULL, course TEXT NOT NULL, module TEXT NOT NULL, "
        "payload TEXT, submitted_at TEXT, ai_rating TEXT, ai_feedback TEXT, updated_at TEXT, "
        "UNIQUE(email, course, module))")
    cx.execute(
        "CREATE TABLE IF NOT EXISTS course_module_completed("
        "email TEXT NOT NULL, course TEXT NOT NULL, module TEXT NOT NULL, "
        "completed_at TEXT NOT NULL, UNIQUE(email, course, module))")
    cx.commit()


def ensure_progress_tables(cx) -> None:
    """init_progress_tables for request paths: never raises, and rolls back a
    failed DDL so the Postgres transaction is usable for the reads after it."""
    try:
        init_progress_tables(cx)
    except Exception:
        try:
            cx.rollback()
        except Exception:
            pass


def mark_watched(cx, email, course, module, lesson) -> None:
    init_progress_tables(cx)
    cx.execute(
        "INSERT OR IGNORE INTO course_lesson_watched(email, course, module, lesson, watched_at) "
        "VALUES(?,?,?,?,?)", (_norm(email), course, module, lesson, _now()))
    cx.commit()


def record_homework(cx, email, course, module, payload, ai_rating=None, ai_feedback=None) -> None:
    init_progress_tables(cx)
    now = _now()
    cx.execute(
        "INSERT INTO course_module_homework(email, course, module, payload, submitted_at, "
        "ai_rating, ai_feedback, updated_at) VALUES(?,?,?,?,?,?,?,?) "
        "ON CONFLICT(email, course, module) DO UPDATE SET payload=excluded.payload, "
        "submitted_at=excluded.submitted_at, ai_rating=excluded.ai_rating, "
        "ai_feedback=excluded.ai_feedback, updated_at=excluded.updated_at",
        (_norm(email), course, module, payload, now, ai_rating, ai_feedback, now))
    cx.commit()


def watched_lessons(cx, email, course, module) -> set:
    try:
        rows = cx.execute(
            "SELECT lesson FROM course_lesson_watched WHERE email=? AND course=? AND module=?",
            (_norm(email), course, module)).fetchall()
        return {r[0] for r in rows}
    except Exception:
        return set()


def homework(cx, email, course, module):
    try:
        row = cx.execute(
            "SELECT payload, submitted_at, ai_rating, ai_feedback FROM course_module_homework "
            "WHERE email=? AND course=? AND module=?",
            (_norm(email), course, module)).fetchone()
        if not row:
            return None
        return {"payload": row[0], "submitted_at": row[1], "ai_rating": row[2], "ai_feedback": row[3]}
    except Exception:
        return None


def completed_at(cx, email, course, module):
    """When this learner's completion of the module was recorded, or None."""
    try:
        row = cx.execute(
            "SELECT completed_at FROM course_module_completed WHERE email=? AND course=? AND module=?",
            (_norm(email), course, module)).fetchone()
        return row[0] if row else None
    except Exception:
        try:
            cx.rollback()  # Postgres: a failed SELECT must not poison the reads after it
        except Exception:
            pass
        return None


def _record_completed(cx, email, course, module) -> None:
    cx.execute(
        "INSERT INTO course_module_completed(email, course, module, completed_at) VALUES(?,?,?,?) "
        "ON CONFLICT(email, course, module) DO UPDATE SET completed_at=excluded.completed_at",
        (_norm(email), course, module, _now()))
    cx.commit()


def _live_completed(cx, email, course, module, lesson_slugs) -> bool:
    if not lesson_slugs:
        return False
    watched = watched_lessons(cx, email, course, module)
    if not set(lesson_slugs).issubset(watched):
        return False
    hw = homework(cx, email, course, module)
    return bool(hw and hw.get("submitted_at"))


def _counts(at, reset_before) -> bool:
    """A recorded completion counts if there is no reset, or it was recorded on a
    UTC date AFTER the reset date. Credit from the reset day itself does not
    count: the natural step is to add a lesson and set today's date."""
    return bool(at) and (not reset_before or at[:10] > str(reset_before))


def module_completed(cx, email, course, module, lesson_slugs, reset_before="") -> bool:
    """True if completion is on record (and not older than reset_before), else
    checks the current lessons live and records a fresh completion.
    A reset therefore means: credit is judged against the current lessons again.
    A learner who has watched every current lesson and handed in homework is
    complete again at once; one missing a lesson is not."""
    try:
        if _counts(completed_at(cx, email, course, module), reset_before):
            return True
        if not _live_completed(cx, email, course, module, lesson_slugs):
            return False
        try:
            _record_completed(cx, email, course, module)
        except Exception:
            try:
                cx.rollback()  # Postgres: leave the transaction usable
            except Exception:
                pass
        return True
    except Exception:
        return False


def backfill_completions(cx, lessons_by_module, resets=None) -> int:
    """Record everyone who has completed a module under its CURRENT lessons.
    lessons_by_module: {(course, module): [lesson slugs]}; resets: {(course,
    module): "YYYY-MM-DD"}. A record that a reset voided is re-stamped if the
    learner is complete under the current lessons. Idempotent. Returns the number of new records, or -1 if it failed. Never raises."""
    try:
        init_progress_tables(cx)
        rows = cx.execute(
            "SELECT email, course, module FROM course_module_homework "
            "WHERE submitted_at IS NOT NULL AND submitted_at != ''").fetchall()
        n = 0
        for email, course, module in rows:
            lessons = lessons_by_module.get((course, module))
            reset = (resets or {}).get((course, module), "")
            if not lessons or _counts(completed_at(cx, email, course, module), reset):
                continue
            if _live_completed(cx, email, course, module, lessons):
                cur = cx.execute(
                    "INSERT INTO course_module_completed(email, course, module, completed_at) "
                    "VALUES(?,?,?,?) ON CONFLICT(email, course, module) "
                    "DO UPDATE SET completed_at=excluded.completed_at",
                    (email, course, module, _now()))
                n += cur.rowcount or 0
        cx.commit()
        return n
    except Exception:
        try:
            cx.rollback()
        except Exception:
            pass
        return -1
