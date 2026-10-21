"""Triage of client portal-chat messages.

Flags the messages that need Dr. Glen's personal attention — direct questions,
complaints, service issues/bugs, health concerns, or requests only he can fulfill
— so the console shows a queue and Glen gets emailed with full context + a
recommendation. Routine chat the AI already handled is NOT flagged.

The classifier's LLM call is injected (`complete(system, user) -> str`) so this is
testable offline. Pure sqlite store; caller passes the connection.
"""
import json
import re
import sqlite3
from datetime import datetime, timedelta, timezone

from dashboard import dbwrite

CATEGORIES = ("question", "complaint", "issue", "bug", "health-concern", "request", "other")
URGENCIES = ("low", "medium", "high")


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def init_table(cx):
    cx.execute("""CREATE TABLE IF NOT EXISTS portal_triage(
        id INTEGER PRIMARY KEY AUTOINCREMENT, email TEXT, name TEXT, category TEXT,
        urgency TEXT, summary TEXT, recommendation TEXT, message TEXT, answer TEXT,
        created_at TEXT, resolved INTEGER NOT NULL DEFAULT 0)""")
    cx.execute("CREATE INDEX IF NOT EXISTS idx_triage_open ON portal_triage(resolved, id)")
    cx.commit()
    # Delivery tracking for the immediate email, the retry key for warning signs, the
    # signs themselves (so a resend carries them) and a double-submit key.
    # Additive and nullable, so old rows read as "never tracked" and are left alone.
    for col, typ in _TRACKING_COLUMNS:
        _add_column(cx, col, typ)
    # NULLs never collide, so old rows and non-warning items are unaffected. Two
    # workers creating it at once can make one fail on Postgres; that one rolls back
    # and carries on (the other made the index; the retry check still runs).
    try:
        cx.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_triage_dedupe "
                   "ON portal_triage(dedupe_key)")
        cx.commit()
    except Exception as e:
        cx.rollback()
        print(f"[triage] dedupe index not created here: {type(e).__name__}", flush=True)


_TRACKING_COLUMNS = (("email_status", "TEXT"), ("emailed_at", "TEXT"),
                     ("email_last_try", "TEXT"), ("message_key", "TEXT"),
                     ("email_attempts", "INTEGER"), ("warning_signs", "TEXT"),
                     ("dedupe_key", "TEXT"))


def _add_column(cx, col, typ):
    """Add one column if missing. Race-safe: two workers booting together can both
    see it missing; the loser's ALTER fails, so roll back, re-check and carry on."""
    from dashboard import db as _db
    if _db.column_exists(cx, "portal_triage", col):
        return
    try:
        cx.execute(f"ALTER TABLE portal_triage ADD COLUMN {col} {typ}")
        cx.commit()
    except Exception:
        cx.rollback()
        if not _db.column_exists(cx, "portal_triage", col):
            raise


def add_item(cx, email, name, category, urgency, summary, recommendation, message, answer=""):
    init_table(cx)
    new_id = dbwrite.insert_returning_id(
        cx,
        "INSERT INTO portal_triage(email,name,category,urgency,summary,recommendation,"
        "message,answer,created_at,resolved) VALUES(?,?,?,?,?,?,?,?,?,0)",
        ((email or "").strip().lower(), (name or "").strip(), category, urgency,
         summary, recommendation, message, answer, _now()))
    cx.commit()
    return new_id


def message_key(text):
    """The retry key for a client message: case, spacing and end punctuation ignored."""
    t = " ".join(str(text or "").casefold().split())
    return t.strip(" .!?,;:")


def dedupe_key(email, key, now_dt, window_minutes=10):
    """The double-submit key: client, message and a `window_minutes` time bucket."""
    bucket = int(now_dt.timestamp() // (int(window_minutes) * 60))
    return f"{email}|{bucket}|{key}"


def add_warning_item(cx, email, name, keys, message, answer="", window_minutes=10, now=None,
                     flag=None):
    """Store a warning-sign item, once. A retry of the same message by the same client
    within `window_minutes` returns the earlier item instead of a second one.
    Two workers racing on a double submit are stopped by the unique dedupe_key: the
    loser's insert fails and it returns the winner's item. Returns (item_id, created)."""
    from dashboard import db as _db
    init_table(cx)
    e = (email or "").strip().lower()
    key = message_key(message)
    now_dt = now or datetime.now(timezone.utc)
    since = (now_dt - timedelta(minutes=window_minutes)).strftime("%Y-%m-%dT%H:%M:%SZ")
    row = cx.execute(
        "SELECT id FROM portal_triage WHERE email=? AND message_key=? AND created_at>=? "
        "ORDER BY id DESC LIMIT 1", (e, key, since)).fetchone()
    if row:
        return row[0], False
    res = flag or warning_flag(keys)
    dkey = dedupe_key(e, key, now_dt, window_minutes)
    try:
        new_id = dbwrite.insert_returning_id(
            cx,
            "INSERT INTO portal_triage(email,name,category,urgency,summary,recommendation,"
            "message,answer,created_at,resolved,message_key,email_status,warning_signs,"
            "dedupe_key) VALUES(?,?,?,?,?,?,?,?,?,0,?,'pending',?,?)",
            (e, (name or "").strip(), res["category"], res["urgency"], res["summary"],
             res["recommendation"], message, answer, now_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
             key, ",".join(res.get("warning_signs") or []) or None, dkey))
    except _db.IntegrityError:
        cx.rollback()
        row = cx.execute("SELECT id FROM portal_triage WHERE dedupe_key=?", (dkey,)).fetchone()
        if not row:
            raise
        return row[0], False
    cx.commit()
    return new_id, True


def set_answer(cx, item_id, answer):
    init_table(cx)
    cx.execute("UPDATE portal_triage SET answer=? WHERE id=?", (answer, int(item_id)))
    cx.commit()


def set_summary(cx, item_id, summary):
    init_table(cx)
    cx.execute("UPDATE portal_triage SET summary=? WHERE id=?", (summary, int(item_id)))
    cx.commit()


# Values of `sent_via` from app._send_full_report_email that are not a delivery.
NOT_DELIVERED = ("console-log", "suppressed")


def record_email(cx, item_id, sent_via, now=None):
    """Record one send attempt. A real send stamps emailed_at; anything else
    (console-log, suppressed, error) leaves the item for the resend job."""
    init_table(cx)
    stamp = now or _now()
    sent = bool(sent_via) and sent_via not in NOT_DELIVERED and not str(sent_via).startswith("error")
    status = f"sent:{sent_via}" if sent else f"unsent:{sent_via or 'error'}"
    cx.execute(
        "UPDATE portal_triage SET email_status=?, email_last_try=?, "
        "email_attempts=COALESCE(email_attempts,0)+1, emailed_at=? WHERE id=?",
        (status, stamp, stamp if sent else None, int(item_id)))
    cx.commit()
    return sent


def unsent_high(cx, now=None, retry_after_minutes=15, max_attempts=12, limit=20):
    """Open high-urgency items whose immediate email has not gone, due a retry.
    Rows from before delivery tracking (email_status NULL) are not touched."""
    init_table(cx)
    now_dt = now or datetime.now(timezone.utc)
    cutoff = (now_dt - timedelta(minutes=retry_after_minutes)).strftime("%Y-%m-%dT%H:%M:%SZ")
    cx.row_factory = sqlite3.Row
    rows = cx.execute(
        "SELECT * FROM portal_triage WHERE resolved=0 AND urgency='high' "
        "AND (email_status LIKE 'unsent%' OR email_status='pending') "
        "AND COALESCE(email_attempts,0)<? "
        "AND COALESCE(email_last_try, created_at)<? ORDER BY id LIMIT ?",
        (int(max_attempts), cutoff, int(limit))).fetchall()
    return [dict(r) for r in rows]


def claim_resend(cx, item_id, seen_last_try, now=None):
    """Claim one item for a resend, so two workers never send it twice."""
    init_table(cx)
    stamp = now or _now()
    if seen_last_try is None:
        cur = cx.execute("UPDATE portal_triage SET email_last_try=? "
                         "WHERE id=? AND email_last_try IS NULL", (stamp, int(item_id)))
    else:
        cur = cx.execute("UPDATE portal_triage SET email_last_try=? "
                         "WHERE id=? AND email_last_try=?", (stamp, int(item_id), seen_last_try))
    cx.commit()
    return cur.rowcount == 1


def list_open(cx, limit=50):
    init_table(cx)
    cx.row_factory = sqlite3.Row
    rows = cx.execute("SELECT * FROM portal_triage WHERE resolved=0 ORDER BY id DESC LIMIT ?",
                      (int(limit or 50),)).fetchall()
    return [dict(r) for r in rows]


def open_count(cx):
    init_table(cx)
    return cx.execute("SELECT COUNT(*) FROM portal_triage WHERE resolved=0").fetchone()[0]


def resolve(cx, item_id):
    init_table(cx)
    cx.execute("UPDATE portal_triage SET resolved=1 WHERE id=?", (int(item_id),))
    cx.commit()


def format_digest(items):
    """Build the daily digest text from open items -> (count, body). Grouped by
    urgency (high first). Returns (0, "") when there's nothing open."""
    items = items or []
    if not items:
        return (0, "")
    rank = {"high": 0, "medium": 1, "low": 2}
    ordered = sorted(items, key=lambda it: (rank.get(it.get("urgency"), 3), it.get("id", 0)))
    lines = [f"{len(ordered)} open item(s) needing your attention:", ""]
    for it in ordered:
        who = it.get("name") or it.get("email") or "(unknown)"
        lines.append(f"[{(it.get('urgency') or '?').upper()}] {it.get('category', '')} — "
                     f"{who} <{it.get('email', '')}>")
        lines.append(f"   {it.get('summary', '')}")
        lines.append(f"   ↳ {it.get('recommendation', '')}")
        lines.append("")
    return (len(ordered), "\n".join(lines))


_TRIAGE_SYSTEM = (
    "You triage a client's portal-chat message to a naturopathic practitioner (Dr. Glen "
    "Swartwout). An AI concierge has ALREADY answered the client (its reply is shown to you). "
    "MOST messages are fully handled by the AI and do NOT need Dr. Glen: routine questions such "
    "as reorder timing, 'what to take when', dosing/protocol clarifications, product/capsule/"
    "ingredient questions, portal how-tos, and general education are the AI's job (it answers "
    "Socratically) and must NOT be escalated.\n"
    "Escalate (needs_attention=true) ONLY when the message genuinely needs Dr. Glen personally:\n"
    "- a NEW, worsening, or concerning symptom or a possible red flag (clinical judgment the AI "
    "should not make);\n"
    "- a request only he can fulfill: a consultation, an Individualized Terrain Restore, a "
    "personal review of their scan, or a remedy-substitution decision;\n"
    "- a complaint, dissatisfaction, refund request, or emotionally-charged message;\n"
    "- a service problem or bug (portal broken, link dead, wrong/missing order);\n"
    "- a question the AI clearly could NOT answer, or where the client is stuck or frustrated "
    "after the AI's reply.\n"
    "WARNING SIGNS. These ALWAYS need Dr. Glen: needs_attention=true, category "
    "\"health-concern\", urgency \"high\", even when the AI answered well and even when you are "
    "unsure. The client reports one of these in themself or in someone in their care:\n"
    "- sudden or severe new dizziness;\n"
    "- dizziness with weakness or slurred speech;\n"
    "- a high or lasting fever;\n"
    "- swelling of the lips, tongue or throat;\n"
    "- hives or trouble breathing;\n"
    "- yellow skin or eyes;\n"
    "- severe pain under the right ribs;\n"
    "- any healing crisis, die-off or Herxheimer reaction.\n"
    "A general education question about one of these, with nothing happening to anyone, is "
    "not a warning sign.\n"
    "When unsure whether the AI handled it, do NOT escalate. That rule never holds back a "
    "warning sign above. Return STRICT JSON ONLY, no prose: "
    '{"needs_attention": true|false, "category": "question"|"complaint"|"issue"|"bug"|'
    '"health-concern"|"request"|"other", "urgency": "low"|"medium"|"high", '
    '"summary": "one-line what they need", "recommendation": "what Dr. Glen should do"}. '
    "If it does not need attention, return {\"needs_attention\": false} and nothing else matters."
)


def _parse(text):
    text = (text or "").strip()
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return {}
    try:
        return json.loads(m.group(0))
    except Exception:
        return {}


def classify(complete, query, answer="", history_text=""):
    """complete(system,user) -> JSON str. Returns a normalized dict when the message
    needs attention, else None. Never raises (returns None on any parse issue)."""
    if not (query or "").strip():
        return None
    user = (f"CONVERSATION SO FAR:\n{history_text}\n\n" if history_text else "") + \
           f"CLIENT MESSAGE:\n{query}\n\nAI ANSWER GIVEN:\n{answer or '(none)'}"
    data = _parse(complete(_TRIAGE_SYSTEM, user))
    if not data.get("needs_attention"):
        return None
    cat = data.get("category") if data.get("category") in CATEGORIES else "other"
    urg = data.get("urgency") if data.get("urgency") in URGENCIES else "medium"
    return {"needs_attention": True, "category": cat, "urgency": urg,
            "summary": (data.get("summary") or "").strip() or "(no summary)",
            "recommendation": (data.get("recommendation") or "").strip() or "(no recommendation)"}


# --- Warning-sign backstop -------------------------------------------------
# Glen, 20 Oct 2026: these signs must always reach him at high urgency with an
# immediate email, "not as a substitute for local health care, but a support for
# self-healing". The model is told so in _TRIAGE_SYSTEM, but a model can say no,
# return garbage, or error. This keyword check runs on the CLIENT'S message only
# and forces the flag whatever the model did.

WARNING_SIGNS = (
    ("dizziness", "sudden or severe new dizziness"),
    ("stroke", "dizziness with weakness or slurred speech"),
    ("fever", "a high or lasting fever"),
    ("swelling", "swelling of the lips, tongue or throat"),
    ("hives-breathing", "hives or trouble breathing"),
    ("jaundice", "yellow skin or eyes"),
    ("right-rib-pain", "severe pain under the right ribs"),
    ("healing-crisis", "a healing crisis"),
)
_SIGN_LABELS = dict(WARNING_SIGNS)

# How a sign is judged. The message is cut into sentences, and each sentence into
# clauses at commas, semicolons, colons and but / and / so / because / though / while.
# Every occurrence of every pattern is checked (finditer), so a negated first
# mention cannot hide a later real one. An occurrence counts unless:
#   - a negation sits earlier in its clause ("I don't currently have any hives"),
#     or inside the matched words ("my lips are not swollen");
#   - its clause is a hypothetical or an education question with no present report
#     ("What if I get hives?", "What causes swollen lips?", "Can you explain hives
#     to me?"). Lead-ins such as "not sure if", "I don't know if", "if it helps"
#     are blanked first, so a hedge never hides the report after it;
#   - the clauses it touches are about a product or an order ("drops for hives",
#     "the hives formula", "order more", "how do I take"), an improvement ("better",
#     "gone", "cleared", "no longer"), or past history ("years ago", "as a child",
#     "last year");
#   - a sign-specific exclusion applies: a blocked nose for breathing, chapped lips
#     for swelling, "mild" or "slight" for right-rib pain.
# Most signs also need a report word in the sentence: a first-person or possessive
# word (I, I'm, my, we, our; "me" as an object does not count), a time word (since,
# today, this morning, started, for 3 days, after taking), or "!" / "help" / "urgent".
# Swelling, trouble breathing and a fever in a baby trip without one: "Swollen lips
# and tongue!" must flag, because a false alarm costs Glen one email and a miss costs
# far more. The typo spellings are a reviewed list, not fuzzy matching.

_DIZZY = (r"(?:dizz\w*|di+z+y\w*|vertigo|light[- ]?headed\w*|"
          r"(?:room|world|everything)\s+(?:is\s+|was\s+|keeps\s+)?spinning)")
_FEVER = r"(?:fever|feaver|feever|fevor|fevre|fevr)\w*"
_HOT = r"(?:10[2-7]|39|4[0-2])(?:[.,]\d+)?"
_SWELL = r"(?:swell\w*|swollen|swolen|swollon|swolln|sweling|puff\w*)"
_MOUTH = r"(?:lips?|tongue|tounge|throat)"
_RIB = (r"(?:right\b[^!?\n]{0,25}\b(?:ribs?|rib cage|ribcage|upper (?:abdomen|belly|side))|"
        r"upper right (?:abdomen|side|belly|quadrant|ribs?))")
_PAIN = r"(?:pain\w*|hurt\w*|ache\w*|aching|cramp\w*|stabbing|sharp|killing me)"
_BABY = (r"\b(?:bab(?:y|y's|ies)|infant\w*|newborn\w*|toddler\w*|little one\w*|"
         r"(?:\d{1,2}|one|two|three|six|nine|eleven|eighteen)[- ](?:week|month)s?[- ]old|"
         r"(?:1|one|2|two)[- ]years?[- ]old)\b")
_SIGN_PATTERNS = {
    "dizziness": [
        re.compile(r"\b(?:sudden\w*|severe\w*|extreme\w*|very|really|realy|real|super|so|"
                   r"terribl\w*|horribl\w*|out of nowhere|new|worst)\b[^!?\n]{0,30}" + _DIZZY, re.I),
        re.compile(_DIZZY + r"[^!?\n]{0,60}\b(?:sudden\w*|severe\w*|out of nowhere|"
                   r"can'?t stand|cannot stand|fell|fall(?:ing)? down|faint\w*|passed out|"
                   r"never (?:had|felt)|first time|since (?:yesterday|this morning|last night|today)|"
                   r"started (?:today|yesterday|this morning|last night))\b", re.I),
        re.compile(r"\b(?:faint(?:ed|ing)|passed out|blacked out|feel(?:ing)? faint)\b", re.I),
        re.compile(r"\b(?:room|world|everything)\s+(?:is\s+|was\s+|keeps\s+|starts?\s+)?spinn?\w*", re.I),
    ],
    "stroke": [
        re.compile(r"\bslurr\w*\b|\b(?:can'?t|cannot|trouble|hard to)\s+(?:speak|talk|get (?:my|the) words out)\b|"
                   r"\b(?:face|mouth|smile)\s+(?:is\s+|was\s+)?droop\w*|\bdroop\w*\s+(?:face|mouth)\b", re.I),
        re.compile(_DIZZY + r"[^!?\n]{0,60}\b(?:weak\w*|numb\w*)\b", re.I),
        re.compile(r"\b(?:weak\w*|numb\w*)\b[^!?\n]{0,60}" + _DIZZY, re.I),
    ],
    "fever": [
        re.compile(r"\b(?:high|very high|spiking|raging|burning)\s+(?:" + _FEVER + r"|temp\w*)", re.I),
        re.compile(r"\b(?:" + _FEVER + r"|temp\w*)[^!?\n]{0,40}\b" + _HOT +
                   r"\s*(?:°|degrees?|f\b|c\b)?", re.I),
        re.compile(r"\b" + _HOT + r"\s*(?:°\s*[fc]?|degrees?(?:\s+[fc])?|[fc])?\s+" + _FEVER, re.I),
        re.compile(r"\b" + _FEVER + r"[^!?\n]{0,40}\b(?:(?:for|over|more than)\s+)?(?:\d+|two|three|"
                   r"four|five|six|seven|several|a few|many)\s+(?:days?|weeks?)\b", re.I),
        re.compile(r"\b" + _FEVER + r"[^!?\n]{0,40}\b(?:won'?t|will not|doesn'?t|does not|isn'?t|not)\s+"
                   r"(?:go(?:ing)?|come|coming|break|breaking)\b", re.I),
        re.compile(r"\b(?:" + _FEVER + r"|temp\w*)[^!?\n]{0,30}\b(?:since|all week|for days|"
                   r"keeps coming back|lasting|persistent)\b", re.I),
        re.compile(r"\b(?:lasting|persistent|ongoing|constant)\s+" + _FEVER, re.I),
    ],
    "swelling": [
        re.compile(r"\b" + _SWELL + r"[^!?\n]{0,40}\b" + _MOUTH + r"\b", re.I),
        re.compile(r"\b" + _MOUTH + r"\b[^!?\n]{0,30}\b" + _SWELL, re.I),
        re.compile(r"\b(?:throat|tongue|tounge)\b[^!?\n]{0,20}\b(?:closing|tight\w*|thick)\b", re.I),
    ],
    "hives": [
        re.compile(r"\b(?:hives|hivs|hivez|welts|urticaria)\b", re.I),
        re.compile(r"\bitchy (?:bumps|welts)\b|\bbroke out in (?:a )?(?:rash|bumps|hives|welts)\b", re.I),
    ],
    "breathing": [
        re.compile(r"\b(?:trouble|difficulty|a hard time|hard time|hard|struggling|struggle|"
                   r"can'?t|cant|cannot|unable|barely|hardly)\s+(?:to\s+)?"
                   r"(?:breath\w*|breth\w*|catch (?:my|his|her|their|our) breath)", re.I),
        re.compile(r"\bbreathing (?:is|feels)\s+(?:\w+\s+)?(?:hard|difficult|labou?red|tight|shallow)\b", re.I),
        re.compile(r"\bshort(?:ness)?\s+of\s+breath\b|\bwheez\w*|\bgasping\b", re.I),
        re.compile(r"\b(?:(?:haven'?t|hasn'?t|have not|has not|not|never)\s+(?:been\s+)?able to|"
                   r"unable to|can'?t|cannot|couldn'?t|could not)\s+"
                   r"(?:breathe\w*|get (?:a |any )?(?:full|deep|good|proper|enough) (?:breath|air)|"
                   r"get (?:enough )?air|catch (?:my|his|her|their|our) breath)|"
                   r"\bnot getting (?:enough|any) air\b|\bgasping for air\b", re.I),
    ],
    # Any fever in a baby or a child under about 2 (coordinator for Glen, 20 Oct
    # 2026; his approved report line: "A fever in a baby needs prompt attention").
    "fever-baby": [
        re.compile(_BABY + r"[^!?\n]{0,50}\b(?:" + _FEVER + r"|temp\w*)", re.I),
        re.compile(r"\b(?:" + _FEVER + r"|temp\w*)[^!?\n]{0,40}" + _BABY, re.I),
    ],
    # "yellow" must describe skin or eyes: "yellow skin", "my eyes look yellow".
    "jaundice": [
        re.compile(r"\bjaund[ia]\w*", re.I),
        re.compile(r"\byellow\w*\s+(?:skin|eyes?|tinge)\b", re.I),
        re.compile(r"\b(?:skin|eyes?|whites of (?:my|his|her|their|the) eyes)\s+(?:\w+\s+){0,2}"
                   r"yellow\w*", re.I),
    ],
    "right-rib-pain": [
        re.compile(r"\b" + _PAIN + r"\b[^!?\n]{0,50}\b" + _RIB + r"\b", re.I),
        re.compile(r"\b" + _RIB + r"\b[^!?\n]{0,40}\b" + _PAIN, re.I),
        re.compile(r"\bgall\s?bladder attack\b", re.I),
    ],
    "healing-crisis": [
        re.compile(r"\bhealing\s+(?:crisis|crises|reaction)\b", re.I),
        re.compile(r"\bherx\w*\b", re.I),
        # Glen, 20 Oct 2026: die-off is a healing crisis; its other name is a
        # Herxheimer reaction (caught by the line above).
        re.compile(r"\bdie[- ]?off\b|\bdying off\b", re.I),
    ],
}

# Internal pattern groups that report as one public sign key.
_REPORTS_AS = {"hives": "hives-breathing", "breathing": "hives-breathing",
               "fever-baby": "fever"}
# These trip with no report word (see above). Negation, hypothetical and the
# exclusions still apply.
# Coordinator for Glen, 20 Oct 2026: in this chat a client typing "high fever" or
# "eyes look yellow" is reporting it, so NO warning sign needs a report word. The
# negation, hypothetical, education, product, improvement and past-history rules
# do the filtering instead.
_NO_MARKER_NEEDED = frozenset(_SIGN_PATTERNS)

# A report of it happening: someone the client speaks for, a time anchor, or alarm.
_REPORT_MARKER = re.compile(
    r"\b(?:i|i'm|im|i've|ive|i'd|i'll|my|mine|myself|we|we're|our)\b|"
    r"\b(?:since|today|tonight|yesterday|this (?:morning|afternoon|evening|week)|"
    r"last night|right now|started|starting|began|keeps?|for (?:\d+|two|three|a few|several) "
    r"(?:hours?|days?|weeks?)|after (?:taking|starting|i|the))\b|!|"
    r"\bhelp\s*(?:!|$)|\b(?:please|need) help\b|\burgent\w*\b", re.I)
_PRESENT_REPORT = re.compile(
    r"\b(?:i have|i've|ive|i'm|im|i am|i got|i feel|i felt|i think i|i've been|we have|"
    r"my \w+(?: \w+){0,3} (?:is|are|has|have|was|were|feels?|looks?|got|turned|went|keeps?)|"
    r"has (?:a|had))\b", re.I)
_PRESENT_TIME = re.compile(
    r"\b(?:right now|currently|at the moment|today|tonight|since|this morning|last night|"
    r"yesterday)\b", re.I)
_HYPO_CUE = re.compile(
    r"\b(?:if|what if|in case|unless|could (?:i|it|this|that)|will (?:i|it|this)|"
    r"would (?:i|it|this)|is it possible|"
    r"(?:can|could|do|does|will|would|might) (?:this|it|that|these|those|they|the)(?: \w+)? cause|"
    r"should i expect|"
    r"can (?:a|an|someone|people|kids|babies)\b)", re.I)
_EDU_CUE = re.compile(
    r"\b(?:what causes?|what can cause|why do(?:es)?|what (?:is|are)|what does|explain|"
    r"tell me (?:about|more|how)|how (?:do|does) \w+ work|can come from|"
    r"(?:is|are) caused by|watch (?:out )?for|look out for|worry about|how long (?:does|do|will|can|should|is|are)\b(?! (?:my|mine|i|this|it)\b))\b", re.I)
# Reading or asking about a topic: education even with "I'm" in the clause.
_TOPIC_CUE = re.compile(
    r"\b(?:read(?:ing)? (?:about|up on)|article|"
    r"learn(?:ing)? about|curious about|info(?:rmation)? (?:on|about)|"
    r"in (?:kids|children|babies|infants|adults|people|general))\b", re.I)
# The conditional part of a clause, and a modal question ("could I have ..."),
# are not a present report.
_IF_PART = re.compile(r"\b(?:what if|if|in case|unless)\b.*|\b(?:could|will|would|can|should) i\b", re.I)
# Hedges that introduce a report rather than make it hypothetical. Blanked first.
_LEAD_IN = re.compile(
    r"\b(?:i'?m not sure|not sure|unsure|i don'?t know|don'?t know|dunno|i wonder|wondering|"
    r"i can'?t tell|can'?t tell)\s+(?:if|whether|why|what|how)\b|"
    r"\bif (?:it|this|that) (?:helps|matters)\b|\bif you need to know\b|"
    r"\bnever\s+(?:felt|been)\b", re.I)
_CLAUSE_SPLIT = re.compile(
    r"[,;:]|\b(?:but|and|so|because|though|although|while)\b", re.I)
_NEGATION = re.compile(
    r"\b(?:no|not|never|don'?t|doesn'?t|didn'?t|haven'?t|hasn'?t|hadn'?t|isn'?t|aren'?t|"
    r"wasn'?t|weren'?t|without|nor|no longer|free of)\b", re.I)
# Negation inside the matched words ("my lips are not swollen"). Not used for the
# fever patterns, which read "won't go down" / "will not break" as a sign.
_NEGATION_INSIDE = re.compile(r"\b(?:not|isn'?t|aren'?t|wasn'?t|weren'?t|no longer|never|"
                              r"doesn'?t|don'?t|didn'?t|hasn'?t|haven'?t)\b", re.I)
# Round 3 (H1): a product word never suppresses a reported sign. Only shopping,
# ordering and how-to-take questions, and a product named by its sign ("the wheeze
# formula"), are excluded, and not even those when the
# sentence ties the sign to a product ("after the first dose", "from the formula").
_PRODUCT_ASK = re.compile(
    r"\b(?:drops? for|remedy for|remedies for|formula for|"
    r"do you (?:have|sell|carry|make)|(?:anything|something) (?:for|to help|that helps)|"
    r"order (?:more|another|again|some)|place an order|re-?order\w*|ordering|"
    r"how (?:do|should|much|often|many) (?:i|we|you)|buy|purchase|in stock|price|"
    r"use (?:this|it|these) for|almost out of|run(?:ning)? out of|"
    r"(?:fever|hives?|allergy|wheeze|cough|headache|migraine|rash|skin) "
    r"(?:drops|remedy|tincture|spray|formula|capsules?|blend)|"
    r"can (?:she|he|i|we|they|my \w+) (?:take|use|have) (?:the|this|it|these)|"
    r"(?:does|do|can|will|would|could|might) (?:this|it|these|those|that|they|the|my)(?: \w+)? help|"
    r"which (?:one|ones|product|remedy|formula|drops))\b", re.I)
_PRODUCT = r"(?:capsules?|caps|tablets?|pills?|dose|doses|dosage|drops|formula|remedy|remedies|" \
           r"program|supplements?|bottle|product|tincture|spray|powder)"
_REACTION = re.compile(
    r"\b(?:after|since|from|on)\s+(?:\w+\s+){0,4}" + _PRODUCT + r"\b|"
    r"\b(?:after|since)\s+(?:i\s+)?(?:taking|starting|started|took|began|doubling|"
    r"increasing|switching)\b|"
    r"\b(?:gave|giving|made|making) (?:me|him|her|us|them)\b|\breaction to\b", re.I)
# A habit, not an acute event: "my husband gets hives from shellfish", "whenever".
_HABITUAL = re.compile(
    r"\bwhenever\b|\bevery time\b|\b(?:gets?|getting)\b[^!?\n]{0,40}\b(?:from|when)\b|"
    r"\bwhen (?:i|he|she|they|we|you) (?:get|eat|run|exercise|work out|climb|walk)\b", re.I)
_EXCLUDE = re.compile(
    # improvement ("normal" alone is not: "is it normal for my lips to swell")
    r"\b(?:better|(?:is|are|it's|its|they're|all) gone|gone now|cleared|clearing up|went away|"
    r"resolved|improv\w*|no longer|healed|"
    r"fading|subsided|calmed down|back to normal)\b|"
    # past history
    r"\b(?:years? ago|months? ago|as a (?:child|kid|baby|teen\w*)|when i was (?:a |young|little)|"
    r"last year|in (?:19|20)\d\d|growing up|in the past|used to|back then)\b", re.I)
_EXCLUDE_FOR = {
    "breathing": re.compile(r"\b(?:nose|nasal|nostrils?|stuffy|congest\w*|sinus\w*)\b", re.I),
    "swelling": re.compile(r"\b(?:chapped|cold sores?)\b", re.I),
    "right-rib-pain": re.compile(r"\b(?:mild|slight\w*|a little|a bit|minor|twinge)\b", re.I),
}


# Negations that are part of the sign itself: "I haven't been able to breathe".
_SIGN_NEGATION = re.compile(
    r"\b(?:haven'?t|hasn'?t|have not|has not|not|never)\s+(?:been\s+)?able to\b|"
    r"\bnot getting (?:enough|any) air\b", re.I)


def _sentences(text):
    return [s for s in re.split(r"(?<=[.!?])\s+|\n+", text or "") if s.strip()]


def _clause_bounds(s):
    """[(start, end), ...] for the clauses of sentence s."""
    cuts = [0]
    for m in _CLAUSE_SPLIT.finditer(s):
        cuts += [m.start(), m.end()]
    cuts.append(len(s))
    return [(cuts[i], cuts[i + 1]) for i in range(0, len(cuts), 2)]


def _clause_at(bounds, pos):
    for a, b in bounds:
        if a <= pos < b:
            return a, b
    return bounds[-1]


def _hypothetical(clause):
    if _TOPIC_CUE.search(clause):
        return True
    if not (_HYPO_CUE.search(clause) or _EDU_CUE.search(clause)):
        return False
    if _PRESENT_TIME.search(clause):
        return False
    return not _PRESENT_REPORT.search(_IF_PART.sub(" ", clause))


def _counts(s, bounds, m, key):
    """Whether one occurrence `m` of a sign in sentence s is a report of it."""
    a, b = _clause_at(bounds, m.start())
    _, end = _clause_at(bounds, max(m.end() - 1, m.start()))
    if _NEGATION.search(s[a:m.start()]):
        return False
    if key not in ("fever",) and _NEGATION_INSIDE.search(_SIGN_NEGATION.sub(" ", m.group(0))):
        return False
    if _hypothetical(s[a:b]):
        return False
    region = s[a:end]
    if _EXCLUDE.search(region):
        return False
    if (_PRODUCT_ASK.search(region) or _HABITUAL.search(region)) and not _REACTION.search(s):
        return False
    extra = _EXCLUDE_FOR.get(key)
    if extra and extra.search(region):
        return False
    return True


def _scan(message, patterns_by_key, no_marker=(), reports_as=None):
    """The keys whose patterns report something in `message`, by the rules above."""
    reports_as = reports_as or {}
    found = set()
    for sentence in _sentences(message):
        s = _LEAD_IN.sub(lambda m: " " * len(m.group(0)), sentence)
        bounds = _clause_bounds(s)
        has_marker = bool(_REPORT_MARKER.search(s))
        for key, patterns in patterns_by_key.items():
            out = reports_as.get(key, key)
            if out in found:
                continue
            if not has_marker and key not in no_marker:
                continue
            if any(_counts(s, bounds, m, key) for pat in patterns for m in pat.finditer(s)):
                found.add(out)
    return found


def warning_signs(message):
    """The warning-sign keys the client's message reports, in WARNING_SIGNS order.
    Empty when none. Pure and deterministic: no model call."""
    found = _scan(message, _SIGN_PATTERNS, _NO_MARKER_NEEDED, _REPORTS_AS)
    return [k for k, _ in WARNING_SIGNS if k in found]


# --- Healing-crisis watch (Glen, 20 Oct 2026) ------------------------------
# "alert me to signs of a possible healing crisis, typically in the first 7 to 10
# days after delivery of a new program". The window opens at the carrier delivery
# (shipments.delivered_at) of a shipment holding an order with source "biofield"
# for this client, an order raised from a report. Reorders and other sources do
# not open it. Inside the window these signs flag to Glen at high urgency; outside
# it nothing changes. Same report-word, negation, hypothetical and exclusion rules
# as the warning signs; every sign here needs a report word.
# Spec: clinical/biofield-sections/plan-report/symptom-changes-2026-10-20/
# healing-crisis-watch.json in the vault.

HEALING_WINDOW_DAYS = 10
HEALING_ORDER_SOURCE = "biofield"
HEALING_SIGNS = (
    ("old-symptom", "an old symptom returning or flaring"),
    ("tired-aches", "tiredness or aches"),
    ("headache", "a headache"),
    ("rash", "a rash or breakouts"),
    ("loose-stools", "loose stools"),
    ("flu-like", "flu-like feelings or a mild fever"),
    ("mucus", "more mucus"),
)
_HEALING_LABELS = dict(HEALING_SIGNS)
_SYMPTOM = (r"\b(?:pains?|aches?|aching|eczema|psoriasis|rash\w*|acne|itch\w*|symptoms?|"
            r"problems?|issues?|conditions?|cough\w*|congest\w*|sinus\w*|allerg\w*|migraines?|"
            r"head ?aches?|insomnia|anxiety|bloat\w*|reflux|arthritis|stiff\w*|numb\w*|tingl\w*|"
            r"swell\w*|cramp\w*|injur\w*|old \w+)")
_HEALING_PATTERNS = {
    "old-symptom": [
        # Round 3 (M2): a return needs a symptom word ("my energy is back" is not one).
        re.compile(r"\bflar(?:e|ed|es|ing)(?: up)?\b|\bresurfac\w*", re.I),
        re.compile(_SYMPTOM + r"[^!?\n]{0,40}\b(?:came back|coming back|come back|comes back|"
                   r"returned|returning|back again|is back|are back|recurr\w*)\b", re.I),
    ],
    "tired-aches": [
        re.compile(r"\b(?:tired(?:ness)?(?!\s+of\b)|exhaust\w*|fatigu\w*|wiped out|drained|no energy|"
                   r"achy|aches?|aching|sore all over|body aches?|joint pain|muscle pain|"
                   r"sore (?:muscles|joints))\b", re.I),
    ],
    "headache": [
        re.compile(r"\b(?:head ?aches?|migraines?|head (?:hurts|is pounding|is killing me))\b", re.I),
    ],
    "rash": [
        re.compile(r"\b(?:rash\w*|break ?outs?|broke out|breaking out|pimples?|acne|"
                   r"(?:red|itchy|little) spots|spots on (?:my|his|her|their) (?:skin|face|body|arms?|"
                   r"legs?|chest|back|neck|hands?|feet)|itchy skin)\b", re.I),
    ],
    "loose-stools": [
        re.compile(r"\b(?:loose (?:stools?|bowels?|poo\w*)|diarr?h?o?ea|diarrhea|"
                   r"runny (?:stools?|poo\w*)|the runs)\b", re.I),
    ],
    "flu-like": [
        re.compile(r"\b(?:flu[- ]?like|fluish|flu-?ish|chills|feverish|"
                   r"(?:mild|low|slight|low[- ]grade|little) (?:fever|temp\w*)|"
                   r"(?:like|getting|coming down with) (?:the |a )?flu|a fever|have a fever)\b",
                   re.I),
    ],
    "mucus": [
        re.compile(r"\b(?:mucus|mucous|phlegm|snot\w*|catarrh|runny nose|"
                   r"coughing (?:stuff|things) up)\b", re.I),
    ],
}


# The window is the client's own, so another person's tiredness or rash is not a
# sign of it (round 3 probe: "My husband is tired after work").
_HEALING_OTHER = re.compile(
    r"\b(?:my|our) (?:wife|husband|partner|daughter|son|kids?|children|child|baby|mom|mother|"
    r"dad|father|sister|brother|friend|boyfriend|girlfriend|dog|cat)\b|"
    r"\b(?:he|she|they) (?:is|are|has|have|was|were|feels?|seems?)\b", re.I)
for _k in _HEALING_PATTERNS:
    _EXCLUDE_FOR[_k] = _HEALING_OTHER

# Same change for the healing-crisis signs: "headache since tuesday" and "rash on
# my arms" flag inside the window with no report word.
_HEALING_NO_MARKER = frozenset(_HEALING_PATTERNS)


def healing_signs(message):
    """The healing-crisis sign keys the client's message reports, in HEALING_SIGNS
    order. Pure; the caller decides whether the client is inside the window."""
    found = _scan(message, _HEALING_PATTERNS, _HEALING_NO_MARKER)
    return [k for k, _ in HEALING_SIGNS if k in found]


def _parse_when(text):
    t = str(text or "").strip()
    if not t:
        return None
    try:
        d = datetime.fromisoformat(t.replace("Z", "+00:00").replace(" ", "T", 1))
    except ValueError:
        try:
            d = datetime.strptime(t[:10], "%Y-%m-%d")
        except ValueError:
            return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def healing_window(cx, email, now=None, days=None):
    """The open healing-crisis window for this client, or None:
    {"day": whole days since delivery, "delivered_at", "shipment_id", "order_id"}.
    Cheap: the client's own biofield orders are read first, then only their
    shipments delivered in the last `days` (+1 for clock slack) are read, and their member orders are resolved the way the coaching code does it
    (coaching.shipment_member_orders), so a household shipment opens the window only
    for the member whose own order is a biofield order. Plain SQL, Postgres-safe."""
    from dashboard import coaching
    e = (email or "").strip().lower()
    if not e:
        return None
    days = HEALING_WINDOW_DAYS if days is None else days
    now_dt = now or datetime.now(timezone.utc)
    since = (now_dt - timedelta(days=days + 1)).strftime("%Y-%m-%d")
    # This client's own biofield orders first, so only their shipments are read.
    mine = cx.execute(
        "SELECT shipment_id, external_ref FROM orders WHERE LOWER(TRIM(email))=? "
        "AND source=? AND status!='cancelled'", (e, HEALING_ORDER_SOURCE)).fetchall()
    ship_ids = sorted({r[0] for r in mine if r[0] is not None})
    refs = sorted({r[1] for r in mine if r[1]})
    if not (ship_ids or refs):
        return None
    links = []
    params = [since]
    if ship_ids:
        links.append("id IN (" + ",".join("?" * len(ship_ids)) + ")")
        params += ship_ids
    if refs:
        links.append("order_uuid IN (" + ",".join("?" * len(refs)) + ")")
        params += refs
    rows = cx.execute(
        "SELECT id, order_uuid, delivered_at FROM shipments "
        "WHERE delivered_at IS NOT NULL AND delivered_at >= ? AND (" + " OR ".join(links) + ") "
        "ORDER BY delivered_at DESC", tuple(params)).fetchall()
    best = None
    for row in rows:
        sid, uuid, delivered = row[0], row[1], row[2]
        when = _parse_when(delivered)
        if when is None or when > now_dt:
            continue
        elapsed = now_dt - when
        if elapsed >= timedelta(days=days):
            continue
        for m in coaching.shipment_member_orders(cx, sid, uuid):
            if m.get("email") == e and m.get("source") == HEALING_ORDER_SOURCE:
                cand = {"day": elapsed.days, "delivered_at": delivered,
                        "shipment_id": sid, "order_id": m.get("id")}
                if best is None or cand["day"] < best["day"]:
                    best = cand
    return best


HEALING_RECOMMENDATION = ("Check in with the client: this may be a healing crisis in the first "
                          "days of a new program. Advise on pacing the remedies, and local care "
                          "if anything becomes acute.")


def healing_flag(keys, window, base=None):
    """The triage result for healing-crisis signs inside the window."""
    day = int((window or {}).get("day") or 0)
    when = "delivery day" if day == 0 else f"day {day} after delivery"
    labels = "; ".join(_HEALING_LABELS[k] for k in keys if k in _HEALING_LABELS)
    summary = f"Possible healing crisis, {when} of a Biofield program: {labels}."
    model_summary = ((base or {}).get("summary") or "").strip()
    if model_summary and model_summary != "(no summary)":
        summary += " " + model_summary
    return {"needs_attention": True, "category": "health-concern", "urgency": "high",
            "summary": summary, "recommendation": HEALING_RECOMMENDATION,
            "healing_signs": list(keys), "window": window}


def warning_sign_labels(keys):
    return [_SIGN_LABELS[k] for k in keys if k in _SIGN_LABELS]


WARNING_RECOMMENDATION = ("Reach the client today. A warning sign was reported; they may need "
                          "local urgent care alongside your support for their self-healing.")


def warning_flag(keys, base=None):
    """The triage result for a matched warning sign: always high urgency, category
    health-concern, and the warning recommendation. The model's summary, when it
    gave one, is kept after the warning; its recommendation never is, because it
    can contradict the flag ("wait until your next appointment")."""
    base = base or {}
    labels = "; ".join(warning_sign_labels(keys))
    summary = f"Warning sign: {labels}."
    model_summary = (base.get("summary") or "").strip()
    if model_summary and model_summary != "(no summary)":
        summary += " " + model_summary
    rec = WARNING_RECOMMENDATION
    return {"needs_attention": True, "category": "health-concern", "urgency": "high",
            "summary": summary, "recommendation": rec, "warning_signs": list(keys)}


def triage(complete, query, answer="", history_text=""):
    """classify() plus the warning-sign backstop. The backstop runs on `query` (the
    client's message) only, and wins over a model 'no', garbage, or an error.
    Never raises."""
    try:
        res = classify(complete, query, answer, history_text)
    except Exception as e:
        print(f"[triage] classify failed: {e!r}", flush=True)
        res = None
    keys = warning_signs(query)
    if keys:
        return warning_flag(keys, res)
    return res
