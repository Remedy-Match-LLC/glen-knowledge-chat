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
from datetime import datetime, timezone

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
    "- any healing crisis.\n"
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
#
# Scope. A sign trips only inside a sentence that also reads as a report of it
# happening: a first-person or possessive word (I, I'm, my, me, we, our, so "my
# baby" counts), or a time word (since, today, this morning, started, for 3 days,
# after taking). An education question with none of those ("Does this remedy help
# with fever?") is left to the model. A sign just after a negation ("I don't have a
# fever") or inside a hypothetical with no present report ("What if I get hives?")
# does not trip.

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

_DIZZY = r"(?:dizz\w*|vertigo|light[- ]?headed\w*|(?:room|world|everything)\s+(?:is\s+|was\s+|keeps\s+)?spinning)"
_BABY = (r"\b(?:bab(?:y|y's|ies)|infant\w*|newborn\w*|toddler\w*|little one\w*|"
         r"(?:\d{1,2}|one|two|three|six|nine|eleven|eighteen)[- ](?:week|month)s?[- ]old|"
         r"(?:1|one|2|two)[- ]years?[- ]old)\b")
_SIGN_PATTERNS = {
    "dizziness": [
        re.compile(r"\b(?:sudden\w*|severe\w*|extreme\w*|very|really|so|terribl\w*|horribl\w*|"
                   r"out of nowhere|new|worst)\b[^.!?\n]{0,30}" + _DIZZY, re.I),
        re.compile(_DIZZY + r"[^.!?\n]{0,60}\b(?:sudden\w*|severe\w*|out of nowhere|"
                   r"can'?t stand|cannot stand|fell|fall(?:ing)? down|faint\w*|passed out|"
                   r"never (?:had|felt)|first time|since (?:yesterday|this morning|last night|today)|"
                   r"started (?:today|yesterday|this morning|last night))\b", re.I),
        re.compile(r"\b(?:faint(?:ed|ing)|passed out|blacked out)\b", re.I),
    ],
    "stroke": [
        re.compile(r"\bslurr\w*\b|\b(?:can'?t|cannot|trouble|hard to)\s+(?:speak|talk|get (?:my|the) words out)\b|"
                   r"\b(?:face|mouth|smile)\s+(?:is\s+|was\s+)?droop\w*|\bdroop\w*\s+(?:face|mouth)\b", re.I),
        re.compile(_DIZZY + r"[^.!?\n]{0,60}\b(?:weak\w*|numb\w*)\b", re.I),
        re.compile(r"\b(?:weak\w*|numb\w*)\b[^.!?\n]{0,60}" + _DIZZY, re.I),
    ],
    "fever": [
        re.compile(r"\b(?:high|very high|spiking|raging|burning)\s+(?:fever|temp\w*)\b", re.I),
        re.compile(r"\b(?:fever|temp\w*)\b[^.!?\n]{0,40}\b(?:10[2-7]|39|4[0-2])(?:[.,]\d+)?\s*"
                   r"(?:°|degrees?|f\b|c\b)?", re.I),
        re.compile(r"\bfever\w*\b[^.!?\n]{0,40}\b(?:for|over|more than)\s+(?:\d+|two|three|four|five|"
                   r"six|seven|several|a few|many)\s+(?:days?|weeks?)\b", re.I),
        re.compile(r"\bfever\w*\b[^.!?\n]{0,40}\b(?:won'?t|will not|doesn'?t|does not|isn'?t|not)\s+"
                   r"(?:go(?:ing)?|come|coming|break|breaking)\b", re.I),
        re.compile(r"\bfever\w*\b[^.!?\n]{0,30}\b(?:since|all week|for days|keeps coming back|lasting|"
                   r"persistent)\b", re.I),
        re.compile(r"\b(?:lasting|persistent|ongoing|constant)\s+fever\b", re.I),
    ],
    "swelling": [
        re.compile(r"\b(?:swell\w*|swollen|puff\w*)\b[^.!?\n]{0,40}\b(?:lips?|tongue|throat)\b", re.I),
        re.compile(r"\b(?:lips?|tongue|throat)\b[^.!?\n]{0,30}\b(?:swell\w*|swollen|puff\w*)\b", re.I),
        re.compile(r"\bthroat\b[^.!?\n]{0,20}\b(?:closing|tightening|closing up)\b", re.I),
    ],
    "hives": [
        re.compile(r"\b(?:hives|welts|urticaria)\b", re.I),
    ],
    "breathing": [
        re.compile(r"\b(?:trouble|difficulty|hard|struggling|struggle|can'?t|cannot|unable)\s+"
                   r"(?:to\s+)?(?:breath\w*|catch my breath)\b", re.I),
        re.compile(r"\bshort(?:ness)?\s+of\s+breath\b|\bwheez\w*|\bgasping\b", re.I),
    ],
    # Any fever in a baby or a child under about 2 (coordinator for Glen, 20 Oct
    # 2026; his approved report line: "A fever in a baby needs prompt attention").
    "fever-baby": [
        re.compile(_BABY + r"[^.!?\n]{0,50}\b(?:fever\w*|temp\w*)\b", re.I),
        re.compile(r"\b(?:fever\w*|temp\w*)\b[^.!?\n]{0,40}" + _BABY, re.I),
    ],
    "jaundice": [
        re.compile(r"\bjaundic\w*\b", re.I),
        re.compile(r"\byellow\w*\b[^.!?\n]{0,30}\b(?:skin|eyes?|whites)\b", re.I),
        re.compile(r"\b(?:skin|eyes?|whites of (?:my|his|her|their) eyes)\b[^.!?\n]{0,25}"
                   r"\b(?:yellow\w*)\b", re.I),
    ],
    "right-rib-pain": [
        re.compile(r"\b(?:pain\w*|hurt\w*|ache\w*|aching|cramp\w*|stabbing|sharp)\b[^.!?\n]{0,50}"
                   r"\bright\b[^.!?\n]{0,25}\b(?:ribs?|rib cage|ribcage|upper (?:abdomen|belly|side))\b", re.I),
        re.compile(r"\bright\b[^.!?\n]{0,25}\b(?:ribs?|rib cage|ribcage|upper (?:abdomen|belly|side))\b"
                   r"[^.!?\n]{0,40}\b(?:pain\w*|hurt\w*|ache\w*|aching|cramp\w*|stabbing|sharp)\b", re.I),
        re.compile(r"\bgall\s?bladder attack\b", re.I),
    ],
    "healing-crisis": [
        re.compile(r"\bhealing\s+(?:crisis|crises|reaction)\b", re.I),
        re.compile(r"\bherx\w*\b", re.I),
    ],
}

# A report of it happening: someone the client speaks for, or a time anchor.
_REPORT_MARKER = re.compile(
    r"\b(?:i|i'm|im|i've|ive|i'd|i'll|me|my|mine|myself|we|we're|our|us)\b|"
    r"\b(?:since|today|tonight|yesterday|this (?:morning|afternoon|evening|week)|"
    r"last night|right now|started|starting|began|keeps?|for (?:\d+|two|three|a few|several) "
    r"(?:hours?|days?|weeks?)|after (?:taking|starting|i|the))\b", re.I)
# A present report that overrides a hypothetical framing.
_PRESENT_REPORT = re.compile(
    r"\b(?:i have|i've|ive|i'm|im|i am|i got|i feel|i felt|i think i|i've been|we have|"
    r"my \w+(?: \w+)? (?:is|are|has|have|was|were|feels?|looks?|got|turned|went|keeps?)|"
    r"has (?:a|had)|since|today|tonight|yesterday|this morning|last night|right now)\b", re.I)
_HYPOTHETICAL = re.compile(
    r"\b(?:if|what if|in case|could (?:i|it|this|that)|will (?:i|it|this)|would (?:i|it|this)|"
    r"is it possible|can (?:this|it|that|the \w+) cause|should i expect)\b", re.I)
# The condition clause of "If I get hives, ..." is not a present report.
_IF_CLAUSE = re.compile(r"\b(?:what if|if|in case)\b[^,]*,?|"
                        r"\b(?:could|will|would|can|should) i\b", re.I)
_NEGATION_BEFORE = re.compile(
    r"\b(?:no|not|never|don'?t|doesn'?t|didn'?t|haven'?t|hasn'?t|hadn'?t|isn'?t|aren'?t|"
    r"wasn'?t|weren'?t|without|nor|no longer|free of)\b", re.I)


def _sentences(text):
    return [s for s in re.split(r"(?<=[.!?])\s+|\n+", text or "") if s.strip()]


# Negation inside the matched words ("my lips are not swollen"). Not used for
# fever, whose own patterns read "won't go down" / "will not break" as a sign.
_NEGATION_INSIDE = re.compile(r"\b(?:not|isn'?t|aren'?t|wasn'?t|weren'?t|no longer|never|"
                              r"doesn'?t|don'?t|didn'?t|hasn'?t|haven'?t)\b", re.I)


def _negated(sentence, m, key):
    before = sentence[:m.start()].split()[-3:]
    if _NEGATION_BEFORE.search(" ".join(before)):
        return True
    return key != "fever" and bool(_NEGATION_INSIDE.search(m.group(0)))


# Internal pattern groups that report as one public sign key.
_REPORTS_AS = {"hives": "hives-breathing", "breathing": "hives-breathing",
               "fever-baby": "fever"}
# These trip with no first-person or time word: "Swollen lips and tongue!" must
# flag. A false alarm costs Glen one email; a miss costs far more. The negation
# and hypothetical rules still apply.
_NO_MARKER_NEEDED = {"swelling", "breathing"}


def warning_signs(message):
    """The warning-sign keys the client's message reports, in WARNING_SIGNS order.
    Empty when none. Pure and deterministic: no model call."""
    found = set()
    for sentence in _sentences(message):
        if (_HYPOTHETICAL.search(sentence)
                and not _PRESENT_REPORT.search(_IF_CLAUSE.sub(" ", sentence))):
            continue
        has_marker = bool(_REPORT_MARKER.search(sentence))
        for key, patterns in _SIGN_PATTERNS.items():
            out = _REPORTS_AS.get(key, key)
            if out in found:
                continue
            if not has_marker and key not in _NO_MARKER_NEEDED:
                continue
            for pat in patterns:
                m = pat.search(sentence)
                if m and not _negated(sentence, m, key):
                    found.add(out)
                    break
    return [k for k, _ in WARNING_SIGNS if k in found]


def warning_sign_labels(keys):
    return [_SIGN_LABELS[k] for k in keys if k in _SIGN_LABELS]


WARNING_RECOMMENDATION = ("Reach the client today. A warning sign was reported; they may need "
                          "local urgent care alongside your support for their self-healing.")


def warning_flag(keys, base=None):
    """The triage result for a matched warning sign: always high urgency, category
    health-concern. Keeps the model's own summary/recommendation when it gave one."""
    base = base or {}
    labels = "; ".join(warning_sign_labels(keys))
    summary = f"Warning sign: {labels}."
    model_summary = (base.get("summary") or "").strip()
    if model_summary and model_summary != "(no summary)":
        summary += " " + model_summary
    rec = (base.get("recommendation") or "").strip()
    if not rec or rec == "(no recommendation)":
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
