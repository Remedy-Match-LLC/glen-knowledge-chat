"""Practitioner profile drafts: the working copy Glen reviews before it goes public.

Spec: docs/superpowers/specs/2026-08-27-practitioner-website-design.md section 2.
Lifecycle and storage modelled on dashboard/ff_match_drafts.py, which lives in
the same sqlite LOG_DB: draft -> submitted -> approved.

Drafts are sqlite ON PURPOSE. The live profile is Postgres, but
db_supabase.supabase_cursor() is a raw psycopg2 cursor with no placeholder
translation -- `?` there would pass every sqlite test and fail in production.
Keeping drafts in sqlite keeps one dialect per module. The sqlite -> Postgres
hop happens once, in practitioner_profile.publish_draft, exactly as
profile_for_slug already hops the other way.

Nothing in this module makes anything public. The public page gates on
practitioners.profile_self_authored_at, and ONLY practitioner_profile.publish_draft
sets that. Losing track of this distinction is how a review gate leaks.
"""

import datetime
import json

STATUSES = frozenset({"draft", "submitted", "approved"})


def _now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def init_tables(cx):
    """Create the drafts table if absent. Idempotent; called on read paths,
    matching dashboard/referrals.py."""
    cx.execute("""CREATE TABLE IF NOT EXISTS practitioner_profile_drafts (
        practitioner_id TEXT PRIMARY KEY,
        fields TEXT NOT NULL DEFAULT '{}',
        status TEXT NOT NULL DEFAULT 'draft',
        review_note TEXT, submitted_at TEXT, reviewed_at TEXT,
        created_at TEXT, updated_at TEXT)""")
    cx.execute("CREATE INDEX IF NOT EXISTS ix_ppd_status"
               " ON practitioner_profile_drafts(status, updated_at DESC)")
    cx.commit()


def _row(r):
    if r is None:
        return None
    d = dict(r)
    raw = d.get("fields") or "{}"
    d["fields"] = json.loads(raw) if isinstance(raw, str) else raw
    return d


def get_draft(cx, pid):
    """The practitioner's working copy, or None."""
    row = cx.execute("SELECT * FROM practitioner_profile_drafts"
                     " WHERE practitioner_id=?", (str(pid),)).fetchone()
    return _row(row)


def upsert_draft(cx, pid, fields):
    """Write the practitioner's proposed values and put the row in 'draft'.

    Editing ALWAYS returns the row to 'draft', including from 'approved': a
    practitioner who changes their page after approval must be reviewed again,
    or the gate is one edit wide.
    """
    payload, now = json.dumps(fields or {}), _now()
    if get_draft(cx, pid):
        cx.execute("UPDATE practitioner_profile_drafts SET fields=?, status='draft',"
                   " review_note=NULL, updated_at=? WHERE practitioner_id=?",
                   (payload, now, str(pid)))
    else:
        cx.execute("INSERT INTO practitioner_profile_drafts"
                   " (practitioner_id, fields, status, created_at, updated_at)"
                   " VALUES (?,?, 'draft', ?, ?)", (str(pid), payload, now, now))
    cx.commit()
    return get_draft(cx, pid)
