"""Two-party appointment proposals and their private message threads."""
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

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


# --- voice clips --------------------------------------------------------------------
# Glen, 2026-10-27 (sales tab): a voice clip on an appointment message is deleted 90
# days after it was sent, and client erasure deletes that client's clips. The file goes
# and audio_filename is cleared. The message text stays.

CLIP_KEEP_DAYS = 90
# Only a name _save_appointment_audio could have written is ever deleted.
# The proposal number is part of the name, and must be the message's own proposal, so a
# row can never point the delete at another thread's clip.
_CLIP_NAME = re.compile(r"^appt-(\d+)-[0-9a-f]{24}\.(mp3|m4a|webm|wav)$")


def _parse_ts(ts):
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _delete_clip(cx, assets_dir, message_id, proposal_id, name):
    """Remove one clip's file, then clear its row and commit. Returns True when the clip
    is gone. A name this code did not write for this proposal, or a file that will not
    delete, leaves the row as it is and is logged, so the clip stays visible rather than
    silently orphaned."""
    m = _CLIP_NAME.match(name or "")
    if not m or int(m.group(1)) != int(proposal_id):
        print(f"[appt-clips] message {message_id}: unexpected file name, left in place",
              flush=True)
        return False
    try:
        (Path(assets_dir) / name).unlink()
    except FileNotFoundError:
        pass                      # already gone: the row still needs clearing
    except OSError as e:
        print(f"[appt-clips] message {message_id}: delete failed: {e!r}", flush=True)
        return False
    cur = cx.execute("UPDATE appointment_proposal_messages SET audio_filename='' "
                     "WHERE id=? AND audio_filename=?", (message_id, name))
    cx.commit()                   # one clip at a time: a later failure undoes none of it
    # A second worker's run finds the row already clear: gone, but not counted twice.
    return (cur.rowcount or 0) == 1


def expire_clips(cx, assets_dir, *, now=None, keep_days=CLIP_KEEP_DAYS):
    """Delete every clip sent more than keep_days ago. Returns how many went. Safe to
    run twice at once: a second run finds the file gone and the row already clear."""
    init_tables(cx)
    cutoff = (now or datetime.now(timezone.utc)) - timedelta(days=keep_days)
    rows = cx.execute("SELECT id, proposal_id, audio_filename, created_at "
                      "FROM appointment_proposal_messages WHERE audio_filename<>''").fetchall()
    n = 0
    for mid, pid, name, created in rows:
        sent = _parse_ts(created)
        if sent is None:
            # An unreadable date falls back to when the file was written, so the clip
            # still goes on time instead of staying forever.
            try:
                sent = datetime.fromtimestamp((Path(assets_dir) / name).stat().st_mtime,
                                              timezone.utc)
            except (OSError, TypeError, ValueError):
                sent = None
        if sent is None:
            print(f"[appt-clips] message {mid}: no readable date, left in place", flush=True)
            continue
        if sent <= cutoff and _delete_clip(cx, assets_dir, mid, pid, name):
            n += 1
    cx.commit()
    return n


def _client_clip_rows(cx, addresses):
    addrs = sorted({(a or "").strip().lower() for a in addresses if (a or "").strip()})
    if not addrs:
        return []
    init_tables(cx)
    marks = ",".join("?" for _ in addrs)
    return cx.execute(
        "SELECT m.id, m.proposal_id, m.audio_filename FROM appointment_proposal_messages m "
        "JOIN appointment_proposals p ON p.id=m.proposal_id "
        f"WHERE LOWER(p.client_email) IN ({marks}) AND m.audio_filename<>''", addrs).fetchall()


def count_client_clips(cx, addresses):
    return len(_client_clip_rows(cx, addresses))


def erase_client_clips(cx, assets_dir, addresses):
    """Client erasure: delete every clip on this client's appointment threads, sent by
    either side. Returns how many went. Callers check count_client_clips afterwards: a
    clip that would not delete must stop the erasure from reporting success."""
    return sum(1 for mid, pid, name in _client_clip_rows(cx, addresses)
               if _delete_clip(cx, assets_dir, mid, pid, name))


# Glen, 2026-10-27 (sales tab): client erasure deletes the text of the client's
# appointment messages too, on every thread of theirs, merged addresses included. The
# message rows go; the appointments themselves (times, status, booking) stay.

def _client_thread_clause(addresses):
    addrs = sorted({(a or "").strip().lower() for a in addresses if (a or "").strip()})
    marks = ",".join("?" for _ in addrs)
    return addrs, (f"proposal_id IN (SELECT id FROM appointment_proposals "
                   f"WHERE LOWER(client_email) IN ({marks}))")


def count_client_messages(cx, addresses):
    addrs, where = _client_thread_clause(addresses)
    if not addrs:
        return 0
    init_tables(cx)
    return cx.execute(f"SELECT COUNT(*) FROM appointment_proposal_messages WHERE {where}",
                      addrs).fetchone()[0]


def erase_client_messages(cx, addresses):
    """Delete every message on this client's appointment threads, sent by either side.
    Returns how many went. Does not commit: the caller's erasure commits it, so a failed
    erasure rolls it back. Delete the clips first (erase_client_clips), which need these
    rows to find their files."""
    addrs, where = _client_thread_clause(addresses)
    if not addrs:
        return 0
    init_tables(cx)
    return cx.execute(f"DELETE FROM appointment_proposal_messages WHERE {where}",
                      addrs).rowcount or 0
