"""Kloud PEMF mat enquiries (Glen, 2026-10-25).

Glen: "enquiry-only. they must be purchased on our distributor store site with Centropix.
We do want to know when someone is interested, and be sure to give them instructions on
how to order, as well as the option of becoming a distributor. We want to follow up with
them in their process and provide support."

An `enquiry_only` product page takes an enquiry in place of a checkout. Each enquiry is
kept here, Glen and Rae are told, and the enquirer is emailed the ordering steps. The
follow-ups on days 3 and 10 are the `kloud-enquiry` sequence, authored in the vault under
`00 System/sequences/kloud-enquiry/`. Nothing here takes payment.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

GUIDE_URL = "https://illtowell.com/kloud"
STORE_URL = "https://centropix.us/remedy/products"
FINANCING_URL = "https://flexxbuy.com/remedy-match/"
SEQUENCE_SLUG = "kloud-enquiry"
# One notice per recipient: a comma-joined To is undeliverable (groovekart_notify).
NOTIFY = ("drglenswartwout@gmail.com", "suerae1111@gmail.com")
# The ordering email goes to one address at most once in this window. Glen and Rae are
# told of every enquiry.
REPLY_WINDOW = timedelta(days=1)

MODELS = {"mini": "Kloud Mini", "maxi": "Kloud Maxi", "unsure": "Kloud"}
SLUG_MODELS = {"kloud-pemf-mini": "mini", "kloud-pemf-maxi": "maxi"}

MAX_NAME, MAX_PHONE, MAX_MESSAGE = 120, 40, 2000
# Ordering emails sent to any address in one day. The form takes any address, so this
# caps what a script could send from Glen's mailbox (review round 1). Past it the
# enquiry is still kept and Glen and Rae are still told.
DAILY_REPLY_CAP = 40
_GREETING_NAME = re.compile(r"[^\W\d_][^\W\d_'’-]*(?:['’-][^\W\d_]+)*")


def greeting_name(name):
    """The first word of the name when it is a plain name of 30 letters or fewer, else "".
    A typed web address or sentence must never be echoed in the greeting."""
    first = (name or "").split()[0] if (name or "").split() else ""
    return first if len(first) <= 30 and _GREETING_NAME.fullmatch(first) else ""


def init_table(cx):
    cx.execute("""CREATE TABLE IF NOT EXISTS kloud_enquiries (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        email       TEXT NOT NULL,
        name        TEXT NOT NULL DEFAULT '',
        phone       TEXT NOT NULL DEFAULT '',
        model       TEXT NOT NULL DEFAULT 'unsure',
        distributor INTEGER NOT NULL DEFAULT 0,
        message     TEXT NOT NULL DEFAULT '',
        page_slug   TEXT NOT NULL DEFAULT '',
        replied     INTEGER NOT NULL DEFAULT 0,
        created_at  TEXT NOT NULL)""")
    cx.execute("CREATE INDEX IF NOT EXISTS ix_kloud_enquiries_email "
               "ON kloud_enquiries(email, created_at)")


def model_for_slug(slug):
    return SLUG_MODELS.get(slug, "unsure")


def clean(value, limit):
    """One line of plain text, trimmed to `limit`. Control characters are dropped so a
    value cannot add a header or a line to the notice."""
    s = "".join(ch if ch.isprintable() else " " for ch in str(value or ""))
    return " ".join(s.split())[:limit]


def clean_message(value):
    s = "".join(ch if (ch.isprintable() or ch == "\n") else " " for ch in str(value or ""))
    return s.strip()[:MAX_MESSAGE]


def record(cx, *, email, name="", phone="", model="unsure", distributor=False,
           message="", page_slug="", now=None):
    """Keep one enquiry. Returns (id, held): held is None when the ordering email should
    go, "recent" when this address was sent it within REPLY_WINDOW, and "capped" when
    DAILY_REPLY_CAP ordering emails went out in that window."""
    init_table(cx)
    now = now or datetime.now(timezone.utc)
    since = (now - REPLY_WINDOW).isoformat()
    recent = cx.execute("SELECT 1 FROM kloud_enquiries WHERE email=? AND replied=1 "
                        "AND created_at>=? LIMIT 1", (email, since)).fetchone()
    today = cx.execute("SELECT COUNT(*) FROM kloud_enquiries WHERE replied=1 "
                       "AND created_at>=?", (since,)).fetchone()[0]
    held = "recent" if recent else ("capped" if today >= DAILY_REPLY_CAP else None)
    send_reply = held is None
    cur = cx.execute(
        "INSERT INTO kloud_enquiries(email,name,phone,model,distributor,message,page_slug,"
        "replied,created_at) VALUES (?,?,?,?,?,?,?,?,?)",
        (email, name, phone, model if model in MODELS else "unsure",
         1 if distributor else 0, message, page_slug, 1 if send_reply else 0,
         now.isoformat()))
    cx.commit()
    return cur.lastrowid, held


def unmark_reply(cx, enquiry_id):
    """The ordering email failed, so a later enquiry from the address may send it."""
    cx.execute("UPDATE kloud_enquiries SET replied=0 WHERE id=?", (enquiry_id,))
    cx.commit()


def _dollars(cents):
    return f"${int(cents) // 100:,}"


def reply_text(*, first_name, model, distributor, mini_cents, maxi_cents):
    """(subject, body) of the ordering email. Prices come from the catalogue. Wording
    from communication's reviewed reply of 2026-10-25, without its order lines."""
    label = MODELS.get(model, "Kloud")
    greeting = f"Aloha {first_name}," if first_name else "Aloha,"
    if distributor:
        dist = ["You asked about becoming a Centropix distributor, with your own store for "
                "referring others.",
                "I will write to you about how it works."]
    else:
        dist = ["You can also become a Centropix distributor, with your own store for "
                "referring others.",
                "If that interests you, reply and I will explain how it works."]
    lines = [
        greeting,
        f"Thank you for asking about the {label} PEMF mat.",
        "The Kloud is ordered directly from our Centropix distributor store.",
        f"This page covers both models, how to order, and financing:\n{GUIDE_URL}",
        f"The Kloud Mini is {_dollars(mini_cents)} and the Kloud Maxi is "
        f"{_dollars(maxi_cents)}, plus shipping and tax.",
        "Order at our Centropix store by choosing your model and completing checkout "
        f"there:\n{STORE_URL}",
        "If you would like to spread the cost over time, you can apply for financing "
        f"through FlexxBuy:\n{FINANCING_URL}",
        *dist,
        "I will follow up as you decide.",
        "Reply here with any questions about the Kloud.",
        "With aloha,",
        "Dr. Glen",
    ]
    return "How to order your Kloud PEMF mat", "\n\n".join(lines) + "\n"


REPLY_LINES = {
    "sent": "They were emailed the ordering steps.",
    "failed": "The ordering email to them was not sent. Please write to them.",
    "capped": "They were not emailed: today's limit on ordering emails was reached. Please write to them.",
    "recent": "They were not emailed again: they were sent the ordering steps in the last day.",
}


def notice_text(*, enquiry_id, email, name, phone, model, distributor, message,
                page_slug, reply_status, follow_ups_on):
    """(subject, body) of the notice to Glen and Rae. reply_status is "sent", "failed"
    or "recent" (already sent the ordering email within REPLY_WINDOW)."""
    who = name or email
    subject = f"Kloud enquiry: {who} ({MODELS.get(model, 'Kloud')})"
    lines = [
        f"{who} asked about the Kloud PEMF mat on the store.",
        "",
        f"Name: {name or '(not given)'}",
        f"Email: {email}",
        f"Phone: {phone or '(not given)'}",
        f"Model: {MODELS.get(model, 'Kloud') if model != 'unsure' else 'Not sure yet'}",
        f"Interested in becoming a distributor: {'Yes' if distributor else 'No'}",
        f"Page: https://myhealingoasis.com/begin/product/{page_slug}" if page_slug else "",
        "",
        "Their question:" if message else "They left no question.",
        message,
        "",
        REPLY_LINES[reply_status],
        ("Follow-up emails go to them on days 3 and 10." if follow_ups_on else
         "The day 3 and day 10 follow-up emails are not switched on yet."),
    ]
    if distributor:
        lines.append("They asked about becoming a distributor. The ordering email says "
                     "Dr. Glen will write to them about how it works.")
    lines.append(f"Enquiry {enquiry_id}.")
    return subject, "\n".join(l for l in lines if l is not None) + "\n"
