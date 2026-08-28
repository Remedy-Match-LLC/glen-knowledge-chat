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


def submit(cx, pid):
    """Practitioner sends their draft for review. True if a draft moved."""
    now = _now()
    cur = cx.execute("UPDATE practitioner_profile_drafts SET status='submitted',"
                     " submitted_at=?, updated_at=?"
                     " WHERE practitioner_id=? AND status='draft'",
                     (now, now, str(pid)))
    cx.commit()
    return cur.rowcount == 1


def approve(cx, pid, note=""):
    """Glen approves a SUBMITTED draft. True if one moved.

    Deliberately refuses a row still in 'draft': approving something the
    practitioner has not submitted would publish an edit they were mid-way
    through writing.
    """
    now = _now()
    cur = cx.execute("UPDATE practitioner_profile_drafts SET status='approved',"
                     " review_note=?, reviewed_at=?, updated_at=?"
                     " WHERE practitioner_id=? AND status='submitted'",
                     (note or None, now, now, str(pid)))
    cx.commit()
    return cur.rowcount == 1


def reject(cx, pid, note):
    """Send a submitted draft back with a reason. The note is required:
    a rejection the practitioner cannot act on just produces a resubmit."""
    if not (note or "").strip():
        raise ValueError("a rejection needs a note")
    now = _now()
    cur = cx.execute("UPDATE practitioner_profile_drafts SET status='draft',"
                     " review_note=?, reviewed_at=?, updated_at=?"
                     " WHERE practitioner_id=? AND status='submitted'",
                     (note.strip(), now, now, str(pid)))
    cx.commit()
    return cur.rowcount == 1


def list_by_status(cx, status=None, limit=200):
    """Rows for the review queue, newest first."""
    if status:
        rows = cx.execute("SELECT * FROM practitioner_profile_drafts WHERE status=?"
                          " ORDER BY updated_at DESC LIMIT ?",
                          (status, int(limit))).fetchall()
    else:
        rows = cx.execute("SELECT * FROM practitioner_profile_drafts"
                          " ORDER BY updated_at DESC LIMIT ?",
                          (int(limit),)).fetchall()
    return [_row(r) for r in rows]


DEFAULT_POLICY = "review"

# Per-field review policy. BETA POLICY: everything is reviewed.
# Relaxing a field to "auto" is a deliberate decision to let it reach the
# public page unreviewed -- make it here, one line, never in the schema.
#
# Keyed on the fields practitioner_profile.save_draft ACTUALLY STORES, not on
# the public presentation shape -- save_draft writes "city" and "state"
# separately; there is no "location" key in a draft's fields, so a policy
# entry named "location" can never match anything and silently never applies.
# The spec's "location" is city+state together: keep BOTH keyed here, or a
# future reader will reintroduce this mismatch. "logo_url" is kept even
# though save_draft does not store it yet -- a later plan wires that up, and
# an unmatched key is harmless (needs_review only consults known-stored keys).
REVIEW_POLICY = {
    "bio": "review",
    "photo_url": "review",
    "logo_url": "review",
    "services": "review",
    "city": "review",
    "state": "review",
    "accepting_clients": "review",
}


def needs_review(field):
    """True unless the field is explicitly policied 'auto'. Unknown fields
    default to review, so a field added later is safe before anyone thinks
    about it."""
    return REVIEW_POLICY.get(field, DEFAULT_POLICY) != "auto"


def split_by_policy(fields):
    """Partition proposed values into (auto_publishable, needs_review)."""
    auto, review = {}, {}
    for k, v in (fields or {}).items():
        (review if needs_review(k) else auto)[k] = v
    return auto, review
