"""Historical intake snapshots for My Clinical Record.

An import never replaces a stored snapshot. The one later edit is split_concern, a
practitioner's split of one concern that keeps the original row in the snapshot.

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


SPLIT_MAX_PARTS = 20
SPLIT_PART_MAX = 160


def split_concern(cx, snapshot_id, email, concern, parts, *, split_by):
    """Replace one stored health concern with the separate issues it held.

    An imported concern can hold a whole application's summary in one entry, which the
    Clinical Summary then shows as one row (Krist Symons' "P5: ..." block, Glen,
    2026-10-22). The entry is replaced in place by one entry per part, and the original
    wording is kept under `health_concerns_split`, so nothing typed by the client is
    lost and the split can be read back or undone. Each part keeps the original row's
    other fields, such as its rating, and the whole original row is kept in the history.

    `concern` must equal the stored wording of exactly one row, and the snapshot must
    belong to `email`. Returns the updated snapshot, or None when nothing matched or
    the snapshot changed underneath this call. Raises ValueError on bad parts or a
    wording stored twice (review round 2, 2026-10-22).
    """
    init_table(cx)
    clean = []
    for p in parts or []:
        p = str(p or "").strip()
        if not p or len(p) > SPLIT_PART_MAX:
            raise ValueError(f"each part must be 1 to {SPLIT_PART_MAX} characters")
        if p == concern:
            raise ValueError("a part cannot repeat the whole concern")
        if p not in clean:
            clean.append(p)
    if not clean or len(clean) > SPLIT_MAX_PARTS:
        raise ValueError(f"give 1 to {SPLIT_MAX_PARTS} parts")
    snap = get_for_email(cx, snapshot_id, email)
    if not snap:
        return None
    before = cx.execute("SELECT answers_json FROM historical_intake_snapshots "
                        "WHERE id=? AND email=?", (int(snapshot_id), _email(email))).fetchone()
    if not before:
        return None
    answers = json.loads(before[0] or "{}")
    rows = answers.get("health_concerns")
    if not isinstance(rows, list):
        return None
    hits = [i for i, r in enumerate(rows)
            if isinstance(r, dict) and r.get("concern") == concern]
    if not hits:
        return None
    if len(hits) > 1:
        raise ValueError("that wording is stored more than once; split by hand")
    at = hits[0]
    original = dict(rows[at])
    # A part already stored as its own concern is not added twice (review round 3).
    others = {r.get("concern") for i, r in enumerate(rows) if i != at and isinstance(r, dict)}
    clean = [p for p in clean if p not in others]
    answers["health_concerns"] = (rows[:at] + [{**original, "concern": p} for p in clean]
                                  + rows[at + 1:])
    history = answers.get("health_concerns_split")
    history = history if isinstance(history, list) else []
    history.append({"original": concern, "original_row": original, "index": at, "into": clean,
                    "at": _now(), "by": (split_by or "console").strip()})
    answers["health_concerns_split"] = history
    # Only if the snapshot is still what was read: another worker's write in between
    # would otherwise be silently undone (review rounds 1 and 2).
    cur = cx.execute(
        "UPDATE historical_intake_snapshots SET answers_json=? "
        "WHERE id=? AND email=? AND answers_json=?",
        (json.dumps(answers, ensure_ascii=False), int(snapshot_id), _email(email),
         before[0]))
    cx.commit()
    return get_for_email(cx, snapshot_id, email) if cur.rowcount else None
