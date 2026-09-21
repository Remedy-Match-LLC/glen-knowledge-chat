"""Erase one client's HEALTH data across the tables that hold it.

Glen, 2026-09-18: build the first slice, the health tables, with the opt-out and money
tables protected. This is the engine only. It is ALLOW-LIST driven: it erases exactly the
named health tables and nothing else. It never runs a "delete everything with this email"
sweep, because a client's data lives in ~60 tables and a blanket delete would take rows
that must be kept.

TWO INVARIANTS, both tested:

  1. HEALTH_TABLES and PROTECTED never overlap. A table cannot be both erasable and
     protected, and erase() refuses to run if that is ever true.
  2. erase() touches only HEALTH_TABLES. A protected table's rows are provably untouched
     after an erasure, checked by re-counting them.

PROTECTED holds two kinds of table that must survive a "forget me":
  - safety: email_suppression / notify state. Delete an opt-out and you start emailing
    someone who asked you to stop -- the erasure would cause the harm it was meant to end.
  - money and legal: orders, points, subscriptions, credits. A forget-me request does not
    override a record you are required to keep.

NOT HERE, on purpose:
  - journal_entries is keyed by a hardcoded user_id ("glen"), not by the client's email,
    so a per-client delete cannot find it. It is erased per-session by the "Delete this
    session" box instead (2026-09-18).
  - the ~30 "other" tables (carts, event registrations, affiliate rows, leads) are neither
    clearly health nor clearly protected. They are left for a later, named decision rather
    than swept in silently.

DRY RUN IS THE DEFAULT. plan() reports what WOULD go and deletes nothing. erase() deletes,
and only when called explicitly; it returns the per-table counts it actually removed, read
back from the cursor, so a delete that did not happen cannot be reported as if it did.
"""

# table -> the column holding the client's email. Verified 2026-09-18. Every one keys on
# `email`; if a new health table keys on something else, put the real column here.
HEALTH_TABLES = {
    "intake_responses": "email",
    "historical_intake_snapshots": "email",
    "member_element_state": "email",
    "scan_analyses": "email",
    "scan_freshness": "email",
    "biofield_auth_tests": "email",
    "portal_chat_messages": "email",
    "portal_health_history": "email",
    "portal_extended_history": "email",
    "body_map_photos": "email",
    "purity_photos": "email",
    "client_conditions": "email",
    "client_facts": "email",
    "health_suggestions": "email",
    "eye_vision_review_requests": "email",
    "client_documents": "email",
    "client_document_extractions": "email",
    "life_stress_selections": "email",
    "portal_triage": "email",
    "portal_process_requests": "email",
    "triage_invites": "email",
    "repertoire": "email",
}

# Never deleted, whatever else happens. Enforced, not just documented.
PROTECTED = frozenset({
    # safety: an opt-out erased is an opt-out ignored
    "email_suppression", "portal_notify_state",
    # money and legal
    "purchase_history", "points_ledger", "subscriptions", "coupons",
    "member_reward_grants", "evox_session_credits", "sequence_enrollments",
    "analysis_quota",
    # the record of the erasures themselves. Erasing the same client twice would
    # otherwise destroy the proof that the first request was honoured.
    "client_erasures",
})


def _norm(email):
    return (email or "").strip().lower()


def _table_exists(cx, table):
    # Backend-aware. The engine was SQLite-only at first (sqlite_master), so it errored
    # against prod Postgres and could not run there at all. Postgres names its catalog
    # differently. Mirrors dashboard/affiliate_activity.py.
    from dashboard import db as _db
    if _db.backend_of(cx) == "postgres":
        return cx.execute(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_schema = current_schema() AND table_name = ?",
            (table,)).fetchone() is not None
    return cx.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone() is not None


def _count(cx, table, col, email):
    return cx.execute(f"SELECT COUNT(*) FROM {table} WHERE LOWER({col})=?", (email,)).fetchone()[0]


def plan(cx, email):
    """Dry run. {table: rows_that_would_be_deleted} for tables that exist and match.

    Deletes nothing. A table absent from this database is simply skipped, not an error,
    so the same plan runs on prod and on a partial local copy.
    """
    _assert_disjoint()
    email = _norm(email)
    if not email:
        return {}
    out = {}
    for table, col in HEALTH_TABLES.items():
        if not _table_exists(cx, table):
            continue
        n = _count(cx, table, col, email)
        if n:
            out[table] = n
    return out


def erase(cx, email, *, confirm):
    """Delete this client's health data. IRREVERSIBLE. Returns {table: rows_deleted}.

    `confirm` must be the normalised email itself. It is a guard against calling erase()
    with the wrong argument order or a stale variable: you have to name who you are
    erasing, and it has to match. Nothing is deleted unless it does.
    """
    _assert_disjoint()
    email = _norm(email)
    if not email:
        raise ValueError("erase requires an email")
    if _norm(confirm) != email:
        raise ValueError("confirm must equal the email being erased; refusing")

    removed = {}
    for table, col in HEALTH_TABLES.items():
        if table in PROTECTED:                     # cannot happen given the invariant, but
            raise AssertionError(f"{table} is protected; refusing to erase it")
        if not _table_exists(cx, table):
            continue
        cur = cx.execute(f"DELETE FROM {table} WHERE LOWER({col})=?", (email,))
        if cur.rowcount:
            removed[table] = cur.rowcount
    cx.commit()

    # Assert the erasure actually applied: nothing matching remains in a health table.
    for table, col in HEALTH_TABLES.items():
        if _table_exists(cx, table) and _count(cx, table, col, email):
            raise AssertionError(f"{table} still holds rows for {email} after erase")
    return removed


def _assert_disjoint():
    overlap = set(HEALTH_TABLES) & PROTECTED
    if overlap:
        raise AssertionError(
            f"a table is both health-erasable and protected: {sorted(overlap)}")


# --- the record an erasure leaves behind -------------------------------------------
# Glen approved the console flow on 2026-09-20. An erasure with no trace cannot be shown
# to have been honoured, so each run records WHO was erased, by whom, and what went.
#
# This row keeps the address after the health data is gone. That is the point: it is the
# proof, and it is why `client_erasures` is in PROTECTED rather than merely left out of
# HEALTH_TABLES.

import json as _json
from datetime import datetime as _dt, timezone as _tz


def init_erasure_log(cx, *, commit=True):
    cx.execute(
        "CREATE TABLE IF NOT EXISTS client_erasures ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, email TEXT NOT NULL, actor TEXT, "
        "removed_json TEXT, total_rows INTEGER, suppressed INTEGER DEFAULT 0, "
        "note TEXT, created_at TEXT NOT NULL)")
    cx.execute("CREATE INDEX IF NOT EXISTS ix_client_erasures_email "
               "ON client_erasures(email)")
    if commit:
        cx.commit()


def record_erasure(cx, email, *, actor, removed, note="", suppressed=False, commit=True):
    """Write one row saying what was erased. `removed` is {table: rows} from erase()."""
    init_erasure_log(cx, commit=False)
    removed = dict(removed or {})
    cx.execute(
        "INSERT INTO client_erasures (email, actor, removed_json, total_rows, "
        "suppressed, note, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (_norm(email), str(actor or ""), _json.dumps(removed, sort_keys=True),
         sum(removed.values()), 1 if suppressed else 0, str(note or ""),
         _dt.now(_tz.utc).isoformat()))
    if commit:
        cx.commit()


def erasures_for(cx, email):
    """Every recorded erasure for this address, newest first. [] when there are none."""
    if not _table_exists(cx, "client_erasures"):
        return []
    rows = cx.execute(
        "SELECT id, email, actor, removed_json, total_rows, suppressed, note, created_at "
        "FROM client_erasures WHERE email=? ORDER BY id DESC", (_norm(email),)).fetchall()
    out = []
    for r in rows:
        try:
            removed = _json.loads(r[3] or "{}")
        except (TypeError, ValueError):
            removed = {}
        out.append({"id": r[0], "email": r[1], "actor": r[2], "removed": removed,
                    "total_rows": r[4], "suppressed": bool(r[5]), "note": r[6],
                    "created_at": r[7]})
    return out
