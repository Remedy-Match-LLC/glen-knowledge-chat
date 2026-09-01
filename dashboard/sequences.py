"""Sequence definitions: what a drip IS, not who is on it.

Slice 2 of docs/superpowers/specs/2026-08-30-sequence-engine-design.md.

Copy is authored in the vault under `00 System/sequences/<slug>/` and pushed here
by the vault-side `sequence_push.py`, so a typo fix is an edit plus one command
rather than a deploy. Nothing in this module sends, enrolls, or schedules.

`delay_days` is measured **from enrollment**, cumulatively, not from the previous
step. That is what makes a re-push safe: editing step 3's delay cannot silently
shift steps 4 and 5 for someone already mid-flight.

The enrollment and send tables are created here too so the schema is complete in
one place, but slice 2 writes neither.
"""
from __future__ import annotations

from dashboard import db  # noqa: F401 — kept for OperationalError parity with siblings


def init_tables(cx):
    """Idempotent. Called at each use site, matching the convention in
    email_suppression.init_table and the other dashboard modules."""
    cx.execute("""CREATE TABLE IF NOT EXISTS sequences (
        slug         TEXT PRIMARY KEY,
        name         TEXT NOT NULL,
        trigger_kind TEXT NOT NULL DEFAULT 'manual',
        active       INTEGER NOT NULL DEFAULT 0,
        updated_at   TEXT DEFAULT (datetime('now')))""")
    cx.execute("""CREATE TABLE IF NOT EXISTS sequence_steps (
        slug        TEXT NOT NULL,
        step_no     INTEGER NOT NULL,
        subject     TEXT NOT NULL,
        body_md     TEXT NOT NULL,
        delay_days  INTEGER NOT NULL,
        updated_at  TEXT DEFAULT (datetime('now')),
        UNIQUE(slug, step_no))""")
    # Written by slice 3 (the runner), created here so the schema lives in one place.
    cx.execute("""CREATE TABLE IF NOT EXISTS sequence_enrollments (
        slug        TEXT NOT NULL,
        email       TEXT NOT NULL,
        enrolled_at TEXT NOT NULL,
        status      TEXT NOT NULL DEFAULT 'active',
        UNIQUE(slug, email))""")
    cx.execute("""CREATE TABLE IF NOT EXISTS sequence_sends (
        slug       TEXT NOT NULL,
        step_no    INTEGER NOT NULL,
        email      TEXT NOT NULL,
        status     TEXT NOT NULL DEFAULT 'claimed',
        message_id TEXT DEFAULT '',
        error      TEXT DEFAULT '',
        claimed_at TEXT DEFAULT (datetime('now')),
        sent_at    TEXT DEFAULT '',
        UNIQUE(slug, step_no, email))""")
    cx.commit()


def _validate(steps):
    if not steps:
        raise ValueError("a sequence needs at least one step")
    ordered = sorted(steps, key=lambda s: int(s["step_no"]))
    expected = list(range(1, len(ordered) + 1))
    if [int(s["step_no"]) for s in ordered] != expected:
        raise ValueError(
            "step numbers must be contiguous from 1; got "
            f"{[int(s['step_no']) for s in ordered]}. A gap means a step file was "
            "deleted or misnamed, and its email would silently never exist.")
    last = None
    for s in ordered:
        d = int(s["delay_days"])
        if d < 0:
            raise ValueError(f"step {s['step_no']}: delay_days must not be negative")
        if last is not None and d < last:
            raise ValueError(
                f"step {s['step_no']}: delay_days {d} is earlier than the previous "
                f"step's {last}. Offsets are measured from enrollment, so this step "
                "would arrive before the one before it.")
        last = d
    return ordered


def upsert(cx, *, slug, name, trigger_kind="manual", active=False, steps):
    """Replace a sequence definition wholesale. Idempotent.

    Steps are replaced rather than merged, so deleting a step file in the vault
    actually removes the step. A merge would leave an orphan step that still fires.
    """
    ordered = _validate(steps)
    # `active` is set on INSERT only and deliberately NOT updated on conflict.
    # A copy push must never flip a sequence live, nor deactivate a live one,
    # as a side effect of whatever happened to be in a vault file. Going live is
    # its own decision: see set_active().
    cx.execute("""INSERT INTO sequences (slug, name, trigger_kind, active)
        VALUES (?,?,?,?)
        ON CONFLICT(slug) DO UPDATE SET
          name=excluded.name,
          trigger_kind=excluded.trigger_kind""",
        (slug, name, trigger_kind, 1 if active else 0))
    cx.execute("DELETE FROM sequence_steps WHERE slug=?", (slug,))
    for s in ordered:
        cx.execute("""INSERT INTO sequence_steps
            (slug, step_no, subject, body_md, delay_days) VALUES (?,?,?,?,?)""",
            (slug, int(s["step_no"]), s["subject"], s["body_md"], int(s["delay_days"])))
    cx.commit()


def get(cx, slug):
    row = cx.execute("SELECT slug, name, trigger_kind, active FROM sequences "
                     "WHERE slug=?", (slug,)).fetchone()
    if not row:
        return None
    steps = cx.execute(
        "SELECT step_no, subject, body_md, delay_days FROM sequence_steps "
        "WHERE slug=? ORDER BY step_no", (slug,)).fetchall()
    return {
        "slug": row[0], "name": row[1], "trigger_kind": row[2],
        "active": bool(row[3]),
        "steps": [{"step_no": s[0], "subject": s[1], "body_md": s[2],
                   "delay_days": s[3]} for s in steps],
    }


def list_all(cx):
    rows = cx.execute("""SELECT s.slug, s.name, s.trigger_kind, s.active,
              (SELECT COUNT(*) FROM sequence_steps t WHERE t.slug = s.slug)
        FROM sequences s ORDER BY s.slug""").fetchall()
    return [{"slug": r[0], "name": r[1], "trigger_kind": r[2],
             "active": bool(r[3]), "step_count": r[4]} for r in rows]


def set_active(cx, slug, active):
    """Turn a sequence on or off. Separate from upsert on purpose: a copy edit
    must not be able to start sending, and a re-push must not stop a live one."""
    cx.execute("UPDATE sequences SET active=? WHERE slug=?",
               (1 if active else 0, slug))
    cx.commit()


# ── Enrollment and the send ledger (slice 3) ─────────────────────────────────
#
# Two properties do most of the safety work here:
#
#  1. ONE step per contact per tick, and a step that came due long ago is SKIPPED
#     rather than sent. Slice 5 migrates ~40 people mid-flight by backdating
#     enrolled_at, so on the first tick several of their steps are already due by
#     date. Releasing those would deliver four emails at once to a real client.
#
#  2. Claim before send. A crash between claiming and sending leaves a stuck row,
#     which a reaper releases; the alternative failure mode is a duplicate, and a
#     duplicate cannot be recalled.

import datetime as _dt


def _utcnow():
    # naive UTC: every stored timestamp is naive UTC, so keep the
    # comparison in one representation rather than mixing aware/naive.
    return _dt.datetime.now(_dt.timezone.utc).replace(tzinfo=None)


def _now_iso(now=None):
    return (now or _utcnow()).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse(ts):
    return _dt.datetime.strptime(str(ts)[:19].replace(" ", "T"), "%Y-%m-%dT%H:%M:%S")


def enroll(cx, slug, email, enrolled_at=None):
    """Put someone on a sequence. Idempotent: re-enrolling neither duplicates nor
    restarts them, so a trigger that fires twice cannot replay the whole drip."""
    e = (email or "").strip().lower()
    if not e:
        return
    cx.execute("""INSERT INTO sequence_enrollments (slug, email, enrolled_at, status)
        VALUES (?,?,?,'active') ON CONFLICT(slug, email) DO NOTHING""",
        (slug, e, enrolled_at or _now_iso()))
    cx.commit()


def set_enrollment_status(cx, slug, email, status):
    cx.execute("UPDATE sequence_enrollments SET status=? WHERE slug=? AND email=?",
               (status, slug, (email or "").strip().lower()))
    cx.commit()


def _candidates(cx):
    """Every (enrollment, step) pair not yet claimed or sent, for ACTIVE sequences
    and ACTIVE enrollments, ordered so the earliest step comes first."""
    return cx.execute("""
        SELECT e.slug, e.email, e.enrolled_at, t.step_no, t.subject, t.body_md,
               t.delay_days
          FROM sequence_enrollments e
          JOIN sequences s   ON s.slug = e.slug
          JOIN sequence_steps t ON t.slug = e.slug
         WHERE s.active = 1
           AND e.status = 'active'
           AND NOT EXISTS (SELECT 1 FROM sequence_sends d
                            WHERE d.slug = e.slug AND d.email = e.email
                              AND d.step_no = t.step_no)
         ORDER BY e.email, t.step_no""").fetchall()


def _partition(cx, now, max_catchup_days):
    now = now or _utcnow()
    due, stale, seen = [], [], set()
    for slug, email, enrolled_at, step_no, subject, body_md, delay in _candidates(cx):
        try:
            due_at = _parse(enrolled_at) + _dt.timedelta(days=int(delay))
        except ValueError:
            continue
        if due_at > now:
            continue                      # not yet
        row = {"slug": slug, "email": email, "step_no": step_no,
               "subject": subject, "body_md": body_md,
               "due_at": due_at.strftime("%Y-%m-%dT%H:%M:%SZ")}
        if max_catchup_days is not None and \
                (now - due_at) > _dt.timedelta(days=max_catchup_days):
            stale.append(row)             # too old to send; see mark_skipped
            continue
        if email in seen:
            continue                      # one step per contact per tick
        seen.add(email)
        due.append(row)
    return due, stale


def due(cx, now=None, max_catchup_days=2):
    return _partition(cx, now, max_catchup_days)[0]


def stale_steps(cx, now=None, max_catchup_days=2):
    return _partition(cx, now, max_catchup_days)[1]


def claim(cx, slug, step_no, email, claimed_at=None):
    """Claim a send. True if this process won it. Cross-process safe: the UNIQUE
    index on (slug, step_no, email) is the arbiter, not a read-then-write."""
    cur = cx.execute("""INSERT INTO sequence_sends
        (slug, step_no, email, status, claimed_at) VALUES (?,?,?, 'claimed', ?)
        ON CONFLICT(slug, step_no, email) DO NOTHING""",
        (slug, int(step_no), (email or "").strip().lower(),
         claimed_at or _now_iso()))
    cx.commit()
    return bool(cur.rowcount)


def mark_sent(cx, slug, step_no, email, message_id, sent_at=None):
    e = (email or "").strip().lower()
    cx.execute("""INSERT INTO sequence_sends
        (slug, step_no, email, status, message_id, sent_at) VALUES (?,?,?,'sent',?,?)
        ON CONFLICT(slug, step_no, email) DO UPDATE SET
          status='sent', message_id=excluded.message_id, sent_at=excluded.sent_at""",
        (slug, int(step_no), e, message_id or "", sent_at or _now_iso()))
    cx.commit()


def mark_failed(cx, slug, step_no, email, error):
    e = (email or "").strip().lower()
    cx.execute("""INSERT INTO sequence_sends
        (slug, step_no, email, status, error) VALUES (?,?,?,'failed',?)
        ON CONFLICT(slug, step_no, email) DO UPDATE SET
          status='failed', error=excluded.error""",
        (slug, int(step_no), e, str(error)[:500]))
    cx.commit()


def mark_skipped(cx, slug, step_no, email, reason):
    e = (email or "").strip().lower()
    cx.execute("""INSERT INTO sequence_sends
        (slug, step_no, email, status, error) VALUES (?,?,?,'skipped',?)
        ON CONFLICT(slug, step_no, email) DO UPDATE SET
          status='skipped', error=excluded.error""",
        (slug, int(step_no), e, str(reason)[:500]))
    cx.commit()


def release_stale_claims(cx, now=None, older_than_minutes=60):
    """Free rows claimed but never resolved, e.g. a crash mid-send. Only touches
    'claimed'; a sent row is never reopened, because that would duplicate."""
    now = now or _utcnow()
    cutoff = (now - _dt.timedelta(minutes=older_than_minutes)) \
        .strftime("%Y-%m-%dT%H:%M:%SZ")
    rows = cx.execute("SELECT slug, step_no, email, claimed_at FROM sequence_sends "
                      "WHERE status='claimed'").fetchall()
    freed = 0
    for slug, step_no, email, claimed_at in rows:
        if str(claimed_at or "") <= cutoff:
            cx.execute("DELETE FROM sequence_sends WHERE slug=? AND step_no=? "
                       "AND email=? AND status='claimed'", (slug, step_no, email))
            freed += 1
    cx.commit()
    return freed
