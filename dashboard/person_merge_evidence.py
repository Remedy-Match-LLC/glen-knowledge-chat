"""Which address a person actually uses, and the survivor the merge tool suggests.

Spec: docs/superpowers/specs/2026-09-26-merge-two-people-design.md. Glen: "The best
indicators might be opens, replies, and email sources", then "suggest". The tool suggests;
staff confirm. Reads only. The mailbox search lists and reads metadata; it never changes mail."""
from datetime import datetime, timedelta, timezone

SIGNALS = ("reply", "signin", "signin_link", "click", "scan", "order", "intake", "booking")
# Strongest first. Within a tier, the latest date counts.
TIERS = (("reply",), ("signin",), ("signin_link",), ("order", "scan", "intake", "booking"),
         ("click",))
LABELS = {"reply": "reply", "signin": "portal sign-in", "signin_link": "sign-in link request",
          "order": "order", "scan": "scan", "intake": "intake form", "booking": "booking",
          "click": "email click"}
MAIL_KEEP_DAYS = 90

# (signal, table, SQL returning one date). `?` is the address, or the person id for signin.
_SOURCES = (
    ("signin", "portal_auth_events",
     "SELECT MAX(created_at) FROM portal_auth_events WHERE person_id=? AND event='login_succeeded'",
     "person"),
    ("signin", "auth_tokens",
     "SELECT MAX(consumed_at) FROM auth_tokens WHERE lower(email)=? AND purpose='client_magic_link'",
     "email"),
    ("signin_link", "auth_tokens",
     "SELECT MAX(created_at) FROM auth_tokens WHERE lower(email)=? AND purpose='client_magic_link'",
     "email"),
    ("click", "cadence_clicks",
     "SELECT MAX(clicked_at) FROM cadence_clicks WHERE lower(email)=?", "email"),
    ("scan", "client_scans", "SELECT MAX(scan_date) FROM client_scans WHERE lower(email)=?", "email"),
    ("order", "orders", "SELECT MAX(created_at) FROM orders WHERE lower(email)=?", "email"),
    ("intake", "intake_responses",
     "SELECT MAX(COALESCE(submitted_at, created_at)) FROM intake_responses WHERE lower(email)=?",
     "email"),
    ("booking", "evox_bookings", "SELECT MAX(start_ts) FROM evox_bookings WHERE lower(email)=?",
     "email"),
)


def _tables(cx):
    from dashboard import db
    if db.backend_of(cx) == "postgres":
        return {r[0] for r in cx.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema=current_schema()")}
    return {r[0] for r in cx.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _later(a, b):
    if not a:
        return b
    if not b:
        return a
    return max(a, b)


def gather(cx, email, person_id, reply_lookup):
    """The latest date of each signal for one address. Missing tables give None."""
    e = (email or "").strip().lower()
    have = _tables(cx)
    out = {k: None for k in SIGNALS}
    for signal, table, sql, by in _SOURCES:
        if table not in have:
            continue
        row = cx.execute(sql, (person_id if by == "person" else e,)).fetchone()
        val = row[0] if row else None
        out[signal] = _later(out[signal], str(val) if val else None)
    try:
        out["reply"] = reply_lookup(e) if reply_lookup else None
    except Exception:
        out["reply"] = None
    return out


def gmail_last_reply(email, service=None):
    """The date of the latest message received from this address, or None. Read-only."""
    try:
        if service is None:
            from dashboard import inbox
            service = inbox._get_gmail_service()
        msgs = service.users().messages()
        found = msgs.list(userId="me", q=f"from:{email}", maxResults=1).execute()
        ids = [m["id"] for m in (found.get("messages") or [])]
        if not ids:
            return None
        meta = msgs.get(userId="me", id=ids[0], format="metadata",
                        metadataHeaders=["Date"]).execute()
        ms = int(meta.get("internalDate") or 0)
        return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat() if ms else None
    except Exception:
        return None


def _day(v):
    try:
        return datetime.fromisoformat(str(v)[:10]).replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _short(email):
    return email.split("@")[-1].split(".")[0] if "@" in email else email


def suggest(evidence_by_email, survivor_hint, now):
    """Suggest the survivor and whether to keep mailing the other address."""
    emails = list(evidence_by_email)
    a, b = emails[0], emails[1]
    survivor, reason = survivor_hint, "No sign of use on either address; keeping the current choice."
    for tier in TIERS:
        da = max((evidence_by_email[a].get(k) or "" for k in tier), default="")
        db_ = max((evidence_by_email[b].get(k) or "" for k in tier), default="")
        if not da and not db_:
            continue
        if da == db_:
            break
        survivor = a if da > db_ else b
        other = b if survivor == a else a
        won = da if survivor == a else db_
        lost = db_ if survivor == a else da
        what = " or ".join(LABELS[k] for k in tier)
        reason = (f"Suggested: {_short(survivor)}. Last {what} from it was {won[:10]}; "
                  + (f"from {_short(other)}, {lost[:10]}." if lost else f"none from {_short(other)}."))
        break
    other = b if survivor == a else a
    latest = ""
    for k in SIGNALS:
        latest = max(latest, evidence_by_email[other].get(k) or "")
    day = _day(latest) if latest else None
    if day and now - day <= timedelta(days=MAIL_KEEP_DAYS):
        mail_old, mail_reason = "keep", (f"Keep mailing {_short(other)}: it was used on "
                                         f"{latest[:10]}, within {MAIL_KEEP_DAYS} days.")
    else:
        mail_old = "stop"
        mail_reason = (f"Stop mailing {_short(other)}: "
                       + (f"last used {latest[:10]}, over {MAIL_KEEP_DAYS} days ago." if latest
                          else "no sign it is used."))
    return {"survivor": survivor, "survivor_reason": reason,
            "mail_old": mail_old, "mail_reason": mail_reason}
