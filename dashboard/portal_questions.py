"""Open questions from Dr. Glen that portal chat asks a client, one at a time.

Glen, 2026-11-12 (clinical tab, for Donna Banks): he emails a client numbered questions
before a call. She answers in her portal chat, by voice or typing. Chat must know the
questions, ask them in turn, understand "the answer to number 1 is ...", and record
which question each answer belongs to. Glen reads the answers from the console.

Questions are set per email from the console. A new set replaces the open set; answers
already given are kept, each with the wording of the question it answered.

Pure apart from the connection it is handed. The model call is injected.
"""
import json
import re
from datetime import datetime, timezone

QUESTION_MAX = 600      # characters per question
QUESTIONS_MAX = 40      # questions per set
ANSWER_MAX = 4000       # characters stored per answer

EXTRACT_SYSTEM = (
    "You match a client's chat message to numbered questions their practitioner asked. "
    "You are given the open questions, the assistant's previous message, and the client's "
    "new message. Decide which questions, if any, the client's new message answers. A "
    "message can answer a question by number (\"the answer to number 3 is ...\") or by "
    "replying to the question the assistant's previous message asked. Quote the client's "
    "own words for each answer, exactly as written, with nothing added. Do not answer for "
    "the client, guess, summarise or interpret. If the message answers none, return none. "
    'Reply with JSON only: {"answers": [{"n": <question number>, "answer": "<the '
    'client\'s exact words>"}]}'
)


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def ensure_tables(cx):
    cx.execute("""CREATE TABLE IF NOT EXISTS portal_open_questions(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        email TEXT NOT NULL,
        n INTEGER NOT NULL,
        text TEXT NOT NULL,
        active INTEGER NOT NULL DEFAULT 1,
        set_at TEXT,
        set_by TEXT)""")
    cx.execute("CREATE INDEX IF NOT EXISTS idx_poq_email ON portal_open_questions(email, active)")
    cx.execute("""CREATE TABLE IF NOT EXISTS portal_question_answers(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        email TEXT NOT NULL,
        n INTEGER NOT NULL,
        question TEXT NOT NULL,
        answer TEXT NOT NULL,
        source_msg_id INTEGER,
        created_at TEXT)""")
    cx.execute("CREATE INDEX IF NOT EXISTS idx_pqa_email ON portal_question_answers(email)")


def clean_questions(raw):
    """[{n, text}] from a console body, or raise ValueError saying what is wrong.
    Numbers are whole and unique; text is trimmed and not empty."""
    if not isinstance(raw, list) or not raw:
        raise ValueError("questions must be a non-empty list")
    if len(raw) > QUESTIONS_MAX:
        raise ValueError(f"at most {QUESTIONS_MAX} questions")
    out, seen = [], set()
    for q in raw:
        if not isinstance(q, dict):
            raise ValueError("each question is {n, text}")
        try:
            n = int(q.get("n"))
        except (TypeError, ValueError):
            raise ValueError("each question needs a whole number n")
        if isinstance(q.get("n"), bool) or n < 1 or n in seen:
            raise ValueError(f"question number {q.get('n')!r} is not a new number from 1 up")
        text = " ".join(str(q.get("text") or "").split())
        if not text:
            raise ValueError(f"question {n} has no text")
        if len(text) > QUESTION_MAX:
            raise ValueError(f"question {n} is longer than {QUESTION_MAX} characters")
        seen.add(n)
        out.append({"n": n, "text": text})
    return sorted(out, key=lambda q: q["n"])


def set_questions(cx, email, questions, set_by=""):
    """Replace this email's open questions with `questions` (already cleaned)."""
    ensure_tables(cx)
    email = (email or "").strip().lower()
    now = _now()
    cx.execute("UPDATE portal_open_questions SET active=0 WHERE email=? AND active=1", (email,))
    for q in questions:
        cx.execute("INSERT INTO portal_open_questions(email, n, text, active, set_at, set_by) "
                   "VALUES (?,?,?,1,?,?)", (email, q["n"], q["text"], now, set_by or ""))
    cx.commit()


def clear_questions(cx, email):
    ensure_tables(cx)
    cx.execute("UPDATE portal_open_questions SET active=0 WHERE email=? AND active=1",
               ((email or "").strip().lower(),))
    cx.commit()


def open_questions(cx, email):
    """This email's current questions in number order, each {n, text, answered}.
    A question counts as answered only by an answer given to the same wording, so a
    re-worded question 1 is asked again."""
    ensure_tables(cx)
    email = (email or "").strip().lower()
    rows = cx.execute("SELECT n, text FROM portal_open_questions WHERE email=? AND active=1 "
                      "ORDER BY n", (email,)).fetchall()
    done = {(int(r[0]), r[1]) for r in cx.execute(
        "SELECT n, question FROM portal_question_answers WHERE email=?", (email,)).fetchall()}
    return [{"n": int(n), "text": t, "answered": (int(n), t) in done} for n, t in rows]


def answers(cx, email):
    """Every answer recorded for this email, oldest first."""
    ensure_tables(cx)
    rows = cx.execute(
        "SELECT n, question, answer, source_msg_id, created_at FROM portal_question_answers "
        "WHERE email=? ORDER BY id", ((email or "").strip().lower(),)).fetchall()
    return [{"n": int(r[0]), "question": r[1], "answer": r[2], "source_msg_id": r[3],
             "at": r[4]} for r in rows]


def record_answers(cx, email, found, questions, source_msg_id=None):
    """Store each {n, answer} whose n is a current question. Returns how many."""
    ensure_tables(cx)
    by_n = {q["n"]: q["text"] for q in questions}
    now, stored = _now(), 0
    for f in found:
        if f["n"] not in by_n:
            continue
        cx.execute("INSERT INTO portal_question_answers(email, n, question, answer, "
                   "source_msg_id, created_at) VALUES (?,?,?,?,?,?)",
                   ((email or "").strip().lower(), f["n"], by_n[f["n"]],
                    f["answer"][:ANSWER_MAX], source_msg_id, now))
        stored += 1
    cx.commit()
    return stored


def _norm(s):
    return " ".join(str(s or "").lower().split())


def _strict_json(text):
    text = str(text or "").strip()
    m = re.search(r"\{.*\}", text, re.S)
    return json.loads(m.group(0) if m else text)


def extract_answers(complete, questions, client_msg, previous_assistant=""):
    """[{n, answer}] the client's message gives to open questions. `complete(system,
    user) -> str` is the model call. Only numbers among `questions` are kept. An
    answer must be the client's own words: a quote not found in the message is
    replaced by the whole message, so nothing the model wrote is ever stored as
    hers. Any error returns []."""
    text = (client_msg or "").strip()
    if not text or not questions:
        return []
    listing = "\n".join(f"{q['n']}. {q['text']}" for q in questions)
    user = (f"OPEN QUESTIONS:\n{listing}\n\nASSISTANT'S PREVIOUS MESSAGE:\n"
            f"{(previous_assistant or '').strip()[:2000] or '(none)'}\n\n"
            f"CLIENT'S NEW MESSAGE:\n{text}")
    try:
        data = _strict_json(complete(EXTRACT_SYSTEM, user))
    except Exception as e:
        print(f"[portal-questions] extract failed: {type(e).__name__}", flush=True)
        return []
    items = data.get("answers") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return []
    valid = {q["n"] for q in questions}
    out, seen = [], set()
    for it in items:
        if not isinstance(it, dict):
            continue
        try:
            n = int(it.get("n"))
        except (TypeError, ValueError):
            continue
        if n not in valid or n in seen:
            continue
        quote = str(it.get("answer") or "").strip()
        if not quote or _norm(quote) not in _norm(text):
            quote = text
        seen.add(n)
        out.append({"n": n, "answer": quote})
    return out


PROMPT_HEAD = "OPEN QUESTIONS FROM DR. GLEN:"
PROMPT_RULES = (
    "Dr. Glen sent this client the numbered questions below before their call. Help them "
    "answer the ones not yet answered. If they have not started, offer to go through the "
    "questions together. Ask one question at a time, in number order, using Dr. Glen's "
    "wording and saying its number. They may answer in any order, by number, or by voice. "
    "Accept each answer as given: thank them and move to the next question. Do not "
    "interpret, diagnose or advise on an answer. If they ask what a question means, say "
    "Dr. Glen can explain it on the call. When every question is answered, tell them "
    "Dr. Glen will read their answers before the call."
)


def prompt_section(questions):
    """The system-prompt block for these questions, or "" when none are open."""
    questions = list(questions or [])
    if not questions:
        return ""
    todo = [q for q in questions if not q.get("answered")]
    if not todo:
        return (PROMPT_HEAD + "\nEvery numbered question Dr. Glen sent has been answered. "
                "If the client asks, tell them Dr. Glen will read their answers before the call.")
    lines = [PROMPT_HEAD, PROMPT_RULES, "Not yet answered:"]
    lines += [f"{q['n']}. {q['text']}" for q in todo]
    done = [str(q["n"]) for q in questions if q.get("answered")]
    if done:
        lines.append("Already answered: " + ", ".join(done) + ".")
    return "\n".join(lines)
