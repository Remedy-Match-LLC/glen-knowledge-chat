"""Two-party appointment proposals and their private message threads."""
from datetime import datetime, timezone

from dashboard import db

SESSION_TYPES = {
    "biofield-consult": {"label": "Biofield Analysis Consultation", "practitioner": "glen", "medium": "zoom", "duration_min": 30},
    "evox": {"label": "EVOX Session", "practitioner": "rae", "medium": "phone", "duration_min": 60},
}


def _now():
    return datetime.now(timezone.utc).isoformat()


def init_tables(cx):
    cx.execute("""CREATE TABLE IF NOT EXISTS appointment_proposals (
      id INTEGER PRIMARY KEY AUTOINCREMENT, client_email TEXT NOT NULL,
      session_type TEXT NOT NULL, practitioner TEXT NOT NULL, medium TEXT NOT NULL,
      proposed_start TEXT NOT NULL, duration_min INTEGER NOT NULL,
      proposed_timezone TEXT NOT NULL DEFAULT 'Pacific/Honolulu',
      billing_mode TEXT NOT NULL DEFAULT 'paid', price_cents INTEGER NOT NULL DEFAULT 0,
      status TEXT NOT NULL DEFAULT 'proposed', proposed_by TEXT NOT NULL,
      client_confirmed INTEGER NOT NULL DEFAULT 0, staff_confirmed INTEGER NOT NULL DEFAULT 0,
      booking_id INTEGER, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""")
    cx.execute("""CREATE TABLE IF NOT EXISTS appointment_proposal_messages (
      id INTEGER PRIMARY KEY AUTOINCREMENT, proposal_id INTEGER NOT NULL,
      sender TEXT NOT NULL, text TEXT NOT NULL DEFAULT '', audio_filename TEXT NOT NULL DEFAULT '',
      created_at TEXT NOT NULL, FOREIGN KEY(proposal_id) REFERENCES appointment_proposals(id))""")
    if not db.column_exists(cx, "appointment_proposals", "proposed_timezone"):
        cx.execute("ALTER TABLE appointment_proposals ADD COLUMN proposed_timezone "
                   "TEXT NOT NULL DEFAULT 'Pacific/Honolulu'")
    cx.execute("CREATE INDEX IF NOT EXISTS idx_appt_prop_email "
               "ON appointment_proposals(client_email,updated_at)")
    cx.commit()


def create(cx, *, email, session_type, start, proposed_by, billing_mode="paid",
           price_cents=0, medium="", practitioner="",
           proposed_timezone="Pacific/Honolulu"):
    init_tables(cx)
    spec = SESSION_TYPES.get(session_type)
    if not spec:
        raise ValueError("bad_session_type")
    billing_mode = billing_mode if billing_mode in ("paid", "prepaid") else "paid"
    practitioner = practitioner or spec["practitioner"]
    medium = medium or spec["medium"]
    client_yes = 1 if proposed_by == "client" else 0
    staff_yes = 0 if proposed_by == "client" else 1
    now = _now()
    cur = cx.execute(
        "INSERT INTO appointment_proposals "
        "(client_email,session_type,practitioner,medium,proposed_start,duration_min,"
        "proposed_timezone,billing_mode,price_cents,status,proposed_by,client_confirmed,"
        "staff_confirmed,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,'proposed',?,?,?,?,?) "
        "RETURNING id",
        ((email or "").strip().lower(), session_type, practitioner, medium, start,
         spec["duration_min"], proposed_timezone, billing_mode, int(price_cents or 0),
         proposed_by, client_yes, staff_yes, now, now))
    proposal_id = int(cur.fetchone()[0])
    cx.commit()
    return proposal_id


def get(cx, proposal_id):
    init_tables(cx)
    cx.row_factory = __import__("sqlite3").Row
    row = cx.execute("SELECT * FROM appointment_proposals WHERE id=?", (proposal_id,)).fetchone()
    return dict(row) if row else None


def list_for_client(cx, email):
    init_tables(cx)
    cx.row_factory = __import__("sqlite3").Row
    rows = cx.execute(
        "SELECT * FROM appointment_proposals WHERE lower(client_email)=? "
        "ORDER BY proposed_start,id", ((email or "").strip().lower(),)).fetchall()
    return [decorate(cx, dict(row)) for row in rows]


def list_for_staff(cx, practitioner=None):
    init_tables(cx)
    cx.row_factory = __import__("sqlite3").Row
    query, args = "SELECT * FROM appointment_proposals", ()
    if practitioner in ("glen", "rae"):
        query += " WHERE practitioner=?"
        args = (practitioner,)
    rows = cx.execute(query + " ORDER BY updated_at DESC", args).fetchall()
    return [decorate(cx, dict(row)) for row in rows]


def decorate(cx, proposal):
    spec = SESSION_TYPES.get(proposal["session_type"], {})
    proposal["session_label"] = spec.get("label", proposal["session_type"])
    proposal["start_iso"] = (proposal["proposed_start"] + "-10:00"
                             if len(proposal["proposed_start"]) == 19
                             else proposal["proposed_start"])
    proposal["messages"] = messages(cx, proposal["id"])
    return proposal


def confirm(cx, proposal_id, side):
    if side not in ("client", "staff"):
        raise ValueError("bad_confirmation_side")
    proposal = get(cx, proposal_id)
    if not proposal:
        return None
    column = "client_confirmed" if side == "client" else "staff_confirmed"
    cx.execute(f"UPDATE appointment_proposals SET {column}=1,updated_at=? WHERE id=?",
               (_now(), proposal_id))
    proposal = get(cx, proposal_id)
    status = ("confirmed" if proposal["client_confirmed"] and proposal["staff_confirmed"]
              else "proposed")
    cx.execute("UPDATE appointment_proposals SET status=?,updated_at=? WHERE id=?",
               (status, _now(), proposal_id))
    cx.commit()
    return get(cx, proposal_id)


def add_message(cx, proposal_id, sender, text="", audio_filename=""):
    if not (text or audio_filename):
        raise ValueError("message_required")
    cx.execute("INSERT INTO appointment_proposal_messages "
               "(proposal_id,sender,text,audio_filename,created_at) VALUES (?,?,?,?,?)",
               (proposal_id, sender, (text or "").strip(), audio_filename or "", _now()))
    cx.execute("UPDATE appointment_proposals SET updated_at=? WHERE id=?", (_now(), proposal_id))
    cx.commit()


def messages(cx, proposal_id):
    cx.row_factory = __import__("sqlite3").Row
    rows = cx.execute("SELECT * FROM appointment_proposal_messages "
                      "WHERE proposal_id=? ORDER BY id", (proposal_id,)).fetchall()
    return [dict(row) for row in rows]
