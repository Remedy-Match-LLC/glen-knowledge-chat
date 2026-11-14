"""The intake by conversation, inside the portal chat (Glen, 2026-11-14).

Glen, on his Bridge: "ask to have the ability to receive the intake information and to
ask the questions to go through the intake form with the client. ... make sure that gets
set up to be functional on the portal chat."

The public funnel already has this guide (`intake_chat`, at /api/intake/public/chat).
This module lets the signed-in portal chat run the same guide with the portal token as
the identity, so a client can do the whole intake by voice or typing.

Rules:
- It runs only while the intake is NOT submitted. A finished intake is edited on the
  form, never re-asked by chat.
- It starts when the client asks for it (`wants_intake`) or the page says the client is
  in intake mode. It stops when the client asks to stop (`wants_stop`) or the guide says
  it is done.
- Answers are merged into the stored draft, never replace it. Only known field ids are
  kept (`intake_public.merge_answers`), and the email always comes from the token.
- Consent is never taken by chat. `intake_chat` leaves it for the review-and-sign step,
  where the client ticks the box herself and presses submit.
"""
import json
import re

START_LINE_DONE = ("Thank you. That covers your intake questions. Please open Build My "
                   "Health Profile, read the consent, tick it yourself, and press submit.")
STOP_LINE = ("We can pause here. Everything you told me is saved in your intake draft. "
             "Say \"let's continue my intake\" whenever you are ready.")

_INTAKE_WORD = r"(intake|health profile|intake form|intake questions)"
_START = re.compile(
    r"\b(start|begin|do|fill|complete|continue|resume|finish|go through|help me with|"
    r"work on|let'?s do)\b[^.?!]{0,40}\b" + _INTAKE_WORD + r"\b", re.I)
_STOP = re.compile(
    r"\b(stop|pause|quit|end|exit|finish later|not now|later)\b[^.?!]{0,30}\b"
    + _INTAKE_WORD + r"\b|^\s*(stop|pause)\s*[.!]?\s*$", re.I)


def wants_intake(query):
    """True when the client's own words ask to start or continue the intake."""
    q = str(query or "")
    return bool(_START.search(q)) and not wants_stop(q)


def wants_stop(query):
    return bool(_STOP.search(str(query or "")))


def should_run(submitted, page_says_intake, query):
    """Whether this turn goes to the intake guide. `page_says_intake` is the browser's
    flag; it only keeps a conversation going, and a submitted intake always wins."""
    if submitted:
        return False
    if wants_stop(query):
        return bool(page_says_intake)   # a stop inside the mode is answered by the guide
    return bool(page_says_intake) or wants_intake(query)


def build_messages(history, query):
    msgs = []
    for m in (history or [])[-16:]:
        if not isinstance(m, dict):
            continue
        c = str(m.get("content") or "").strip()
        if not c:
            continue
        msgs.append({"role": "user" if m.get("role") == "user" else "assistant",
                     "content": c[:2000]})
    msgs.append({"role": "user", "content": str(query or "")[:2000]})
    # The model requires the first message to be the user's.
    while msgs and msgs[0]["role"] != "user":
        msgs.pop(0)
    return msgs


def build_system(base_voice, answers):
    from dashboard import intake_chat as _ic
    known = {k: v for k, v in (answers or {}).items()
             if not str(k).startswith("_") and v not in ("", None, [], {})}
    return (_ic.build_system(base_voice, (answers or {}).get("first_name") or "")
            + "\n\nFORM TO COLLECT:\n" + _ic.form_outline()
            + "\n\nALREADY ON FILE (do not re-ask): " + json.dumps(known, default=str))


def run_turn(answers, history, query, base_voice, call_model):
    """One guide turn. Returns (say, merged_answers, updates, done, stopped).

    `call_model(system, messages)` returns the model's raw text. Pure apart from it, so
    a test can pass a fake."""
    from dashboard import intake_chat as _ic, intake_public as _ip
    if wants_stop(query):
        return STOP_LINE, dict(answers or {}), {}, False, True
    text = call_model(build_system(base_voice, answers), build_messages(history, query))
    say, updates, done = _ic.parse_reply(text)
    # Consent is the client's own tick, never a chat answer.
    from dashboard import intake as _intake
    consent_ids = {f["id"] for f in _intake._fields() if f.get("type") == "consent"}
    updates = {k: v for k, v in (updates or {}).items() if k not in consent_ids}
    merged = _ip.merge_answers(answers or {}, updates)
    if done:
        say = (say + "\n\n" if say else "") + START_LINE_DONE
    return say or "Could you say that again?", merged, updates, done, False


def save(cx, email, updates, now_iso):
    """Merge `updates` into the CURRENT stored draft and save it. Re-reads the row so a
    form save made while the model was thinking is kept. Refuses a submitted intake.
    Returns the saved answers, or None when nothing was written."""
    from dashboard import intake as _intake, intake_public as _ip
    email = (email or "").strip().lower()
    if not email or _intake.is_submitted(cx, email):
        return None
    row = _intake.get_response(cx, email)
    current = dict((row["answers"] if row else {}) or {})
    if not updates:
        return current
    merged = _ip.merge_answers(current, updates)
    merged["email"] = email
    _intake.save_draft(cx, email, merged, now_iso)
    return merged
