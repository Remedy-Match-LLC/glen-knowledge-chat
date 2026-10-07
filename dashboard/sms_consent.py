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


def norm_phone(phone):
    """A number as all its digits with the country code. Ten digits are taken as US/Canada
    and get a leading 1. Fewer than ten digits gives "", which is never recorded."""
    d = "".join(ch for ch in str(phone or "") if ch.isdigit())
    if len(d) < 10:
        return ""
    return "1" + d if len(d) == 10 else d


def init_table(cx):
    cx.execute("""CREATE TABLE IF NOT EXISTS sms_consent_events (
        event_id TEXT PRIMARY KEY, phone_digits TEXT NOT NULL, phone_raw TEXT,
        email TEXT, action TEXT NOT NULL, source TEXT NOT NULL, created_at TEXT NOT NULL,
        message_sid TEXT)""")
    cx.execute("CREATE INDEX IF NOT EXISTS idx_sms_consent_phone "
               "ON sms_consent_events (phone_digits, created_at)")
    cx.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_sms_consent_sid "
               "ON sms_consent_events (message_sid) WHERE message_sid IS NOT NULL")


def _set_people_tag(cx, number, opted_out):
    """Add or remove the text opt-out tag on every person holding this number. Runs inside
    a savepoint, so a failure here (Postgres aborts the transaction on any error) rolls
    back only the tag work and never the consent event already inserted."""
    cx.execute("SAVEPOINT sms_tag")
    try:
        rows = cx.execute("SELECT id, phone, tags FROM people WHERE phone LIKE ?",
                          ("%" + number[-4:] + "%",)).fetchall()
        changed = 0
        for pid, phone, raw in rows:
            if norm_phone(phone) != number:
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
        cx.execute("RELEASE SAVEPOINT sms_tag")
        return changed
    except Exception as e:
        cx.execute("ROLLBACK TO SAVEPOINT sms_tag")
        print(f"[sms-consent] people tag update failed: {e!r}", flush=True)
        return 0


def _latest(cx, number):
    return cx.execute("SELECT action, source FROM sms_consent_events WHERE phone_digits=? "
                      "ORDER BY created_at DESC, event_id DESC LIMIT 1", (number,)).fetchone()


def record(cx, phone, action, source, email=None, message_sid=None):
    """Record one consent event and commit it. action is "in" or "out".
    Returns False, writing nothing, when:
      - the number has fewer than 10 digits;
      - this Twilio message was already recorded (a retry or a replay);
      - a form tick would reverse a STOP. Only the number's own START reverses a STOP,
        because anyone can type any number into a public form."""
    if action not in ("in", "out"):
        raise ValueError("action must be 'in' or 'out'")
    number = norm_phone(phone)
    if not number:
        return False
    init_table(cx)
    if message_sid and cx.execute("SELECT 1 FROM sms_consent_events WHERE message_sid=?",
                                  (message_sid,)).fetchone():
        return False
    if action == "in" and not source.startswith("sms:"):
        last = _latest(cx, number)
        if last and last[0] == "out" and last[1].startswith("sms:"):
            return False
    cx.execute("INSERT INTO sms_consent_events (event_id, phone_digits, phone_raw, email, "
               "action, source, created_at, message_sid) VALUES (?,?,?,?,?,?,?,?)",
               (uuid.uuid4().hex, number, str(phone or "")[:40],
                (email or "").strip().lower() or None, action, source[:80], _now(),
                message_sid))
    _set_people_tag(cx, number, opted_out=(action == "out"))
    cx.commit()
    return True


def status(cx, phone):
    """The latest action for a number: "in", "out", or None when it has no event."""
    number = norm_phone(phone)
    if not number:
        return None
    init_table(cx)
    row = _latest(cx, number)
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
