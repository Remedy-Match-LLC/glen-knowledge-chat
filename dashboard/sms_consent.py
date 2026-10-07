"""Text-message consent, per phone number (replacement plan phase 1, Glen 2026-10-06).

One row per event, never updated: a ticked opt-in box on a form, or a STOP / START reply
to our Twilio number. The latest event for a number is its consent. A number with no
event has not agreed, so `may_text` is False for it.

A text opt-out also sets the people tag `consent:sms-unsubscribed` on everyone holding
that number, and an opt-in removes it. That tag never gates email (email_suppression
matches whole tags only), and an email unsubscribe never touches it.

Wording is Glen's, approved 2026-10-06 (communication/drafts/2026-10-06-text-opt-in-wording.md).
"""
import base64
import datetime
import hashlib
import hmac
import json
import uuid

SMS_OPT_OUT_TAG = "consent:sms-unsubscribed"

STOP_WORDS = ("STOP", "STOPALL", "UNSUBSCRIBE", "CANCEL", "END", "QUIT", "OPTOUT", "REVOKE")
START_WORDS = ("START", "YES", "UNSTOP")
HELP_WORDS = ("HELP", "INFO")

# The three automatic replies, verbatim from the approved draft (section 3).
REPLY_OPT_IN = ("Remedy Match: you will now get texts about your orders, appointments and account. "
                "Message frequency varies. Msg and data rates may apply. Reply HELP for help, "
                "STOP to stop.")
REPLY_STOP = "Remedy Match: you will get no more texts from us. Reply START to receive them again."
REPLY_HELP = ("Remedy Match: for help, reply to this text or email support@remedymatch.com. "
              "Reply STOP to stop.")


def _now():
    return datetime.datetime.utcnow().isoformat(timespec="microseconds") + "Z"


def digits10(phone):
    """The last 10 digits of a number, or "" when it has fewer than 10."""
    d = "".join(ch for ch in str(phone or "") if ch.isdigit())
    return d[-10:] if len(d) >= 10 else ""


def init_table(cx):
    cx.execute("""CREATE TABLE IF NOT EXISTS sms_consent_events (
        event_id TEXT PRIMARY KEY, phone_digits TEXT NOT NULL, phone_raw TEXT,
        email TEXT, action TEXT NOT NULL, source TEXT NOT NULL, created_at TEXT NOT NULL)""")
    cx.execute("CREATE INDEX IF NOT EXISTS idx_sms_consent_phone "
               "ON sms_consent_events (phone_digits, created_at)")


def _set_people_tag(cx, digits, opted_out):
    """Add or remove the text opt-out tag on every person holding this number."""
    try:
        rows = cx.execute("SELECT id, phone, tags FROM people WHERE phone LIKE ?",
                          ("%" + digits[-4:],)).fetchall()
    except Exception:
        return 0
    changed = 0
    for pid, phone, raw in rows:
        if digits10(phone) != digits:
            continue
        try:
            tags = json.loads(raw or "[]")
        except Exception:
            continue
        has = SMS_OPT_OUT_TAG in tags
        if opted_out and not has:
            tags = tags + [SMS_OPT_OUT_TAG]
        elif not opted_out and has:
            tags = [t for t in tags if t != SMS_OPT_OUT_TAG]
        else:
            continue
        cx.execute("UPDATE people SET tags=?, updated_at=? WHERE id=?",
                   (json.dumps(tags), _now(), pid))
        changed += 1
    return changed


def record(cx, phone, action, source, email=None):
    """Record one consent event and commit it. action is "in" or "out".
    Returns False, writing nothing, for a number without 10 digits."""
    if action not in ("in", "out"):
        raise ValueError("action must be 'in' or 'out'")
    digits = digits10(phone)
    if not digits:
        return False
    init_table(cx)
    cx.execute("INSERT INTO sms_consent_events (event_id, phone_digits, phone_raw, email, "
               "action, source, created_at) VALUES (?,?,?,?,?,?,?)",
               (uuid.uuid4().hex, digits, str(phone or "")[:40],
                (email or "").strip().lower() or None, action, source[:80], _now()))
    _set_people_tag(cx, digits, opted_out=(action == "out"))
    cx.commit()
    return True


def status(cx, phone):
    """The latest action for a number: "in", "out", or None when it has no event."""
    digits = digits10(phone)
    if not digits:
        return None
    init_table(cx)
    row = cx.execute("SELECT action FROM sms_consent_events WHERE phone_digits=? "
                     "ORDER BY created_at DESC, event_id DESC LIMIT 1", (digits,)).fetchone()
    return row[0] if row else None


def may_text(cx, phone):
    """True only when the number's latest event is an opt-in."""
    return status(cx, phone) == "in"


def keyword(body):
    """Classify an inbound text: "stop", "start", "help", or None."""
    word = (body or "").strip().upper()
    if word in STOP_WORDS:
        return "stop"
    if word in START_WORDS:
        return "start"
    if word in HELP_WORDS:
        return "help"
    return None


def twilio_signature_ok(auth_token, url, params, signature):
    """Twilio's request signature: base64 HMAC-SHA1 of the URL plus each POST parameter's
    name and value, sorted by name. `params` is a list of (name, value) pairs.
    False whenever the token or the signature is missing."""
    if not auth_token or not signature:
        return False
    payload = url + "".join(k + v for k, v in sorted(params, key=lambda kv: kv[0]))
    want = base64.b64encode(hmac.new(auth_token.encode(), payload.encode(),
                                     hashlib.sha1).digest()).decode()
    return hmac.compare_digest(want, signature)
