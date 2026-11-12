"""Studio Black Friday 2026: a Studio app buyer claims Dr. Glen's three-door offer.

Design: marketing tab, ~/AI-Training/marketing/04 Copy/black-friday-2026/capture-design.md.
Glen approved the flow on 2026-11-12. On the same day he replaced the Studio order
number with the purchase date and plan, because Studio's Transactions table has no
order-number column. Rae matches each claim to a line in that table by date and plan.

The page asks for name, email, the date they bought the app (17 to 27 Nov 2026) and
the plan, plus one list opt-in tick box. One claim per email: a second submission
updates the date and plan and never resends the offer email.

The last day to buy a door is the claim's Hawaii date plus 14 days. Claims close after
Sunday 6 December 2026, Hawaii time.

The offer email is HELD. OFFER_SUBJECT and OFFER_TEXT stay None until marketing's copy
passes three review rounds and Glen types "send it" in the marketing tab. While they
are None nothing is sent, whatever any flag says.

Pure apart from the connection it is handed. The mail send is injected.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

SOURCE = "studio_bf_2026"
HAWAII = ZoneInfo("Pacific/Honolulu")
BOUGHT_FIRST = date(2026, 11, 17)
BOUGHT_LAST = date(2026, 11, 27)
LAST_CLAIM_DAY = date(2026, 12, 6)     # claims close after this Hawaii day
DAYS_TO_BUY = 14
PLANS = {"3mo": "3 months", "1yr": "1 year"}
STATUSES = ("pending", "confirmed", "rejected")
NAME_MAX = 120
EMAIL_MAX = 254

# Held until marketing's copy is approved. "{name}" and "{last_day}" are filled in.
OFFER_SUBJECT = None
OFFER_TEXT = None

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _now():
    return datetime.now(timezone.utc)


def hawaii_day(now=None):
    return (now or _now()).astimezone(HAWAII).date()


def is_open(now=None):
    return hawaii_day(now) <= LAST_CLAIM_DAY


def last_day_to_buy(claim_day):
    return claim_day + timedelta(days=DAYS_TO_BUY)


def day_label(d):
    """'Sunday 20 December', the way the offer email writes [last day to buy]."""
    return f"{d.strftime('%A')} {d.day} {d.strftime('%B')}"


def clean_claim(data, today=None):
    """{name, email, bought_on (date), plan, list_optin} from a form body, or raise
    ValueError with a message the page can show. A purchase date after today, Hawaii
    time, is refused (review round 1).

    list_optin is only what the form said. Anyone can type any address, so a later
    list email must not rely on it alone: the second PR confirms it first (round 1)."""
    data = data if isinstance(data, dict) else {}
    name = " ".join(str(data.get("name") or "").split())
    email = str(data.get("email") or "").strip().lower()
    if not name:
        raise ValueError("Please enter your name.")
    if len(name) > NAME_MAX:
        raise ValueError("Please shorten your name.")
    if len(email) > EMAIL_MAX or not _EMAIL_RE.match(email):
        raise ValueError("Please enter a valid email address.")
    try:
        bought = date.fromisoformat(str(data.get("bought_on") or "").strip())
    except ValueError:
        raise ValueError("Please choose the date you bought the app.")
    if not (BOUGHT_FIRST <= bought <= BOUGHT_LAST):
        raise ValueError("The offer is for apps bought from 17 to 27 November 2026.")
    if bought > (today or hawaii_day()):
        raise ValueError("That date is still to come. Please choose the day you bought the app.")
    plan = str(data.get("plan") or "").strip()
    if plan not in PLANS:
        raise ValueError("Please choose your plan: 3 months or 1 year.")
    return {"name": name, "email": email, "bought_on": bought, "plan": plan,
            "list_optin": data.get("list_optin") in (True, "on", "true", "1", 1)}


def ensure_tables(cx):
    cx.execute("""CREATE TABLE IF NOT EXISTS studio_bf_claims(
        email TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        bought_on TEXT NOT NULL,
        plan TEXT NOT NULL,
        list_optin INTEGER NOT NULL DEFAULT 0,
        claimed_on TEXT NOT NULL,
        last_day_to_buy TEXT NOT NULL,
        source TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending',
        status_by TEXT,
        status_at TEXT,
        offer_sent_at TEXT,
        offer_outcome TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL)""")


def save_claim(cx, claim, now=None):
    """Insert a first claim, or update the name, date and plan of a repeat one. A tick
    in the box is kept once given; an untick never removes it. The claim day, and so
    the last day to buy, is fixed by the first claim. A changed date or plan resets
    Rae's check to pending, since she checked the old one (review round 2). Returns
    (row, is_new)."""
    ensure_tables(cx)
    now = now or _now()
    stamp = now.isoformat(timespec="seconds")
    day = hawaii_day(now)
    em, bought = claim["email"], claim["bought_on"].isoformat()
    # ON CONFLICT: two processes taking the same first claim cannot both insert.
    cur = cx.execute(
        "INSERT INTO studio_bf_claims(email, name, bought_on, plan, list_optin, "
        "claimed_on, last_day_to_buy, source, status, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?, 'pending', ?,?) ON CONFLICT(email) DO NOTHING",
        (em, claim["name"], bought, claim["plan"], 1 if claim["list_optin"] else 0,
         day.isoformat(), last_day_to_buy(day).isoformat(), SOURCE, stamp, stamp))
    is_new = cur.rowcount == 1
    if not is_new:
        cx.execute(
            "UPDATE studio_bf_claims SET "
            "status=CASE WHEN bought_on=? AND plan=? THEN status ELSE 'pending' END, "
            "status_by=CASE WHEN bought_on=? AND plan=? THEN status_by ELSE NULL END, "
            "status_at=CASE WHEN bought_on=? AND plan=? THEN status_at ELSE NULL END, "
            "name=?, bought_on=?, plan=?, "
            "list_optin=CASE WHEN ?=1 THEN 1 ELSE list_optin END, updated_at=? "
            "WHERE email=?",
            (bought, claim["plan"], bought, claim["plan"], bought, claim["plan"],
             claim["name"], bought, claim["plan"], 1 if claim["list_optin"] else 0,
             stamp, em))
    cx.commit()
    return get_claim(cx, em), is_new


_COLS = ("email", "name", "bought_on", "plan", "list_optin", "claimed_on",
         "last_day_to_buy", "source", "status", "status_by", "status_at",
         "offer_sent_at", "offer_outcome", "created_at", "updated_at")


def _row(r):
    d = dict(zip(_COLS, r))
    d["list_optin"] = bool(d["list_optin"])
    d["plan_label"] = PLANS.get(d["plan"], d["plan"])
    d["last_day_label"] = day_label(date.fromisoformat(d["last_day_to_buy"]))
    return d


def get_claim(cx, email):
    r = cx.execute(f"SELECT {', '.join(_COLS)} FROM studio_bf_claims WHERE email=?",
                   ((email or "").strip().lower(),)).fetchone()
    return _row(r) if r else None


def list_claims(cx):
    """Every claim, newest first, for Rae's list."""
    ensure_tables(cx)
    rows = cx.execute(f"SELECT {', '.join(_COLS)} FROM studio_bf_claims "
                      "ORDER BY created_at DESC, email").fetchall()
    return [_row(r) for r in rows]


def set_status(cx, email, status, by, now=None):
    """Rae's check against Studio's Transactions table. Returns the row, or None when
    no claim has this email."""
    if status not in STATUSES:
        raise ValueError(f"status must be one of {', '.join(STATUSES)}")
    ensure_tables(cx)
    em = (email or "").strip().lower()
    cur = cx.execute("UPDATE studio_bf_claims SET status=?, status_by=?, status_at=? "
                     "WHERE email=?",
                     (status, (by or "")[:80], (now or _now()).isoformat(timespec="seconds"),
                      em))
    cx.commit()
    return get_claim(cx, em) if cur.rowcount else None


def offer_message(row):
    """(subject, text) for this claim, or None while the copy is held."""
    if not OFFER_SUBJECT or not OFFER_TEXT:
        return None
    fill = {"name": row["name"], "last_day": row["last_day_label"]}
    return OFFER_SUBJECT.format(**fill), OFFER_TEXT.format(**fill)


# A mark older than this with no outcome is a send that never ran (a restart, a thread
# that never started), so it may be tried again (review round 2).
STALE_MARK = timedelta(hours=1)


def claim_offer_send(cx, email, now=None):
    """Mark the offer as being sent to this email, before the send, so two quick
    submissions send once. True when this caller should send. A stale mark with no
    outcome is taken again."""
    now = now or _now()
    stale = (now - STALE_MARK).isoformat(timespec="seconds")
    cur = cx.execute("UPDATE studio_bf_claims SET offer_sent_at=? WHERE email=? AND "
                     "offer_outcome IS NULL AND (offer_sent_at IS NULL OR offer_sent_at < ?)",
                     (now.isoformat(timespec="seconds"), (email or "").strip().lower(), stale))
    cx.commit()
    return cur.rowcount == 1


def record_offer_outcome(cx, email, outcome):
    """What the send did. None means it surely sent nothing: the mark is freed so a
    later claim or the console's send-pending action tries again. "unknown" is kept,
    so a send SES may have taken is never repeated."""
    em = (email or "").strip().lower()
    if outcome is None:
        cx.execute("UPDATE studio_bf_claims SET offer_sent_at=NULL WHERE email=? AND "
                   "offer_outcome IS NULL", (em,))
    else:
        cx.execute("UPDATE studio_bf_claims SET offer_outcome=? WHERE email=?",
                   (str(outcome)[:20], em))
    cx.commit()


def pending_offer_emails(cx, now=None):
    """Claims whose offer has not gone: never tried, or a stale try with no outcome.
    Claims made while the copy was held are sent from here once it is released."""
    ensure_tables(cx)
    stale = ((now or _now()) - STALE_MARK).isoformat(timespec="seconds")
    rows = cx.execute("SELECT email FROM studio_bf_claims WHERE offer_outcome IS NULL AND "
                      "(offer_sent_at IS NULL OR offer_sent_at < ?) ORDER BY created_at",
                      (stale,)).fetchall()
    return [r[0] for r in rows]
