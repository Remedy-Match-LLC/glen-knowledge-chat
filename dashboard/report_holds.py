"""Owner holds on a client's portal report dates. Pure sqlite; no Flask, no network.

Glen, 2026-09-15: "add a draft button". A held date shows on the client's portal as a draft,
with remedies blurred, whatever its stored status. That covers both kinds of history:
- a portal_biofield_reports row, whose status the button also sets to ai_draft;
- a biofield_reveals row, which has no status of its own and which the portal otherwise
  treats as confirmed.
Only the operator Publish for that date releases a hold. An autoconfirm or a re-sync never
does, so the owner's decision stands until the owner publishes again.
"""
from datetime import datetime, timezone


def _norm(email):
    return (email or "").strip().lower()


def init_table(cx):
    cx.execute("""
        CREATE TABLE IF NOT EXISTS portal_report_holds (
            email      TEXT NOT NULL,
            scan_date  TEXT NOT NULL,
            held_by    TEXT,
            held_at    TEXT,
            PRIMARY KEY (email, scan_date)
        )
    """)
    cx.commit()


def hold(cx, email, scan_date, held_by=""):
    e, d = _norm(email), (scan_date or "").strip()
    if not e or not d:
        return False
    cx.execute(
        "INSERT INTO portal_report_holds (email, scan_date, held_by, held_at) VALUES (?,?,?,?) "
        "ON CONFLICT (email, scan_date) DO UPDATE SET held_by=excluded.held_by, held_at=excluded.held_at",
        (e, d, held_by or "", datetime.now(timezone.utc).isoformat()))
    cx.commit()
    return True


def release(cx, email, scan_date):
    cx.execute("DELETE FROM portal_report_holds WHERE email=? AND scan_date=?",
               (_norm(email), (scan_date or "").strip()))
    cx.commit()


def is_held(cx, email, scan_date):
    if not email or not scan_date:
        return False
    row = cx.execute("SELECT 1 FROM portal_report_holds WHERE email=? AND scan_date=?",
                     (_norm(email), (scan_date or "").strip())).fetchone()
    return row is not None


def held_dates(cx, email):
    rows = cx.execute("SELECT scan_date FROM portal_report_holds WHERE email=?",
                      (_norm(email),)).fetchall()
    return {r[0] for r in rows}
