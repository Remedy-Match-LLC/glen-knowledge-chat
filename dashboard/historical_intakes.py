"""Immutable historical intake snapshots for My Clinical Record.

Snapshots are separate from intake_responses, which remains the client's one
current editable profile. Imported consent is discarded at the store boundary.
Only approved/client-visible snapshots may cross a portal-token route.
"""
import json
from datetime import datetime, timezone


def _now():
    return datetime.now(timezone.utc).isoformat()


def _email(value):
    return (value or "").strip().lower()


def init_table(cx):
    cx.execute("""CREATE TABLE IF NOT EXISTS historical_intake_snapshots (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        person_id INTEGER,
        email TEXT NOT NULL,
        form_date TEXT,
        form_name TEXT,
        answers_json TEXT NOT NULL,
        source_document_id INTEGER,
        source_system TEXT NOT NULL,
        source_record_id TEXT NOT NULL,
        import_batch_id TEXT,
        review_status TEXT NOT NULL DEFAULT 'staff_review',
        client_visible INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL,
        reviewed_at TEXT,
        reviewed_by TEXT)""")
    cx.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_historical_intake_source "
               "ON historical_intake_snapshots(source_system, source_record_id)")
    cx.execute("CREATE INDEX IF NOT EXISTS ix_historical_intake_email "
               "ON historical_intake_snapshots(email, form_date)")
    cx.commit()


def put_import(cx, *, person_id, email, form_date, form_name, answers,
               source_system, source_record_id, import_batch_id="",
               source_document_id=None):
    """Idempotently store one staff-only snapshot; never stores terms/consent."""
    init_table(cx)
    safe_answers = dict(answers or {})
    safe_answers.pop("terms", None)
    safe_answers.pop("_imported", None)
    safe_answers.pop("_external", None)
    safe_answers.pop("_migration_provenance", None)
    cx.execute(
        "INSERT OR IGNORE INTO historical_intake_snapshots "
        "(person_id,email,form_date,form_name,answers_json,source_document_id,"
        " source_system,source_record_id,import_batch_id,review_status,client_visible,created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,'staff_review',0,?)",
        (person_id, _email(email), form_date or "", form_name or "Historical intake",
         json.dumps(safe_answers, ensure_ascii=False), source_document_id,
         source_system, source_record_id, import_batch_id or "", _now()))
    cx.commit()
    row = cx.execute(
        "SELECT id FROM historical_intake_snapshots WHERE source_system=? AND source_record_id=?",
        (source_system, source_record_id)).fetchone()
    return row[0] if row else None


def _row(row):
    if not row:
        return None
    keys = ("id", "person_id", "email", "form_date", "form_name", "answers_json",
            "source_document_id", "source_system", "source_record_id", "import_batch_id",
            "review_status", "client_visible", "created_at", "reviewed_at", "reviewed_by")
    result = dict(zip(keys, row))
    result["answers"] = json.loads(result.pop("answers_json") or "{}")
    result["client_visible"] = bool(result["client_visible"])
    return result


def get_for_email(cx, snapshot_id, email, *, visible_only=False):
    init_table(cx)
    sql = "SELECT * FROM historical_intake_snapshots WHERE id=? AND email=?"
    params = [int(snapshot_id), _email(email)]
    if visible_only:
        sql += " AND client_visible=1 AND review_status='approved'"
    return _row(cx.execute(sql, params).fetchone())


def list_for_email(cx, email):
    init_table(cx)
    rows = cx.execute(
        "SELECT * FROM historical_intake_snapshots WHERE email=? "
        "ORDER BY form_date DESC, id DESC", (_email(email),)).fetchall()
    return [_row(row) for row in rows]


def list_visible_for_email(cx, email):
    init_table(cx)
    rows = cx.execute(
        "SELECT * FROM historical_intake_snapshots WHERE email=? "
        "AND client_visible=1 AND review_status='approved' "
        "ORDER BY form_date DESC, id DESC", (_email(email),)).fetchall()
    return [_row(row) for row in rows]


def review(cx, snapshot_id, *, approved, visible, reviewed_by):
    """Practitioner decision. Rejected records can never remain client-visible."""
    init_table(cx)
    status = "approved" if approved else "rejected"
    client_visible = 1 if approved and visible else 0
    cur = cx.execute(
        "UPDATE historical_intake_snapshots SET review_status=?, client_visible=?, "
        "reviewed_at=?, reviewed_by=? WHERE id=?",
        (status, client_visible, _now(), (reviewed_by or "console").strip(), int(snapshot_id)))
    cx.commit()
    return cur.rowcount > 0
