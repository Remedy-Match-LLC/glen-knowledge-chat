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

_INTAKE_WORD = r"(intake|health profile)( form| questions)?"
# A start is her own request: a start verb aimed straight at the intake ("do my
# intake", "help me with the intake form"). Questions about the intake, a "later" and
# a "not" are ordinary chat (review rounds 1 and 3).
_START = re.compile(
    r"\b(start|begin|do|fill in|fill out|fill|complete|continue|resume|finish|go through|"
    r"help me with|help me do|help me fill|work on)\s+(with\s+|on\s+|through\s+)?"
    r"(my|the|this)\s+" + _INTAKE_WORD + r"\b", re.I)
_ASKING = re.compile(r"^\s*(please\s+)?(can|could|would|will|shall|may)\s+(we|you|i)\b", re.I)
_NOT_NOW = re.compile(
    r"\b(later|tomorrow|not now|don'?t|do not|won'?t|will not|never|already|cannot|can'?t)\b",
    re.I)
# A stop is a short message that is only a stop, so an answer that happens to say
# "quit" ("I had to quit smoking before my intake") is still an answer.
_STOP = re.compile(
    r"^\s*(ok(ay)?[,.]?\s+)?(please\s+)?(let'?s\s+|can we\s+|i want to\s+|i need to\s+)?"
    r"(stop|pause|quit|end|exit|take a break|finish (this |it |the |my )?(intake )?later|"
    r"not now|later)"
    r"(\s+(the|my|this))?(\s+" + _INTAKE_WORD + r")?"
    r"(\s+(for now|now|here|for today|please))*\s*[.!]?\s*$", re.I)


def wants_intake(query):
    """True when the client's own words ask to start or continue the intake."""
    q = str(query or "")
    if not _START.search(q) or wants_stop(q) or _NOT_NOW.search(q):
        return False
    if "?" in q and not _ASKING.search(q):
        return False      # "did Dr Glen finish my intake?" is a question about it
    return True


def wants_stop(query):
    q = str(query or "")
    return len(q.split()) <= 10 and bool(_STOP.search(q))


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
    return say or "Could you say that again?", merged, updates, done, False


CHAT_SAVED = "_chat_saved"


def _blank(v):
    return v in ("", None, [], {}) or (isinstance(v, str) and not v.strip())


def save(cx, email, updates, now_iso):
    """The saved answers, or None when nothing was written. See `save_learned`."""
    out = save_learned(cx, email, updates, now_iso)
    return None if out is None else out[0]


def save_learned(cx, email, updates, now_iso):
    """Merge `updates` into the CURRENT stored draft and save it. Returns (answers,
    learned): the saved answers and the field ids this save actually changed. Returns
    None when nothing could be written.

    The row is locked first (`intake.lock_row`), so a form save in another worker cannot
    land between this read and this write. Never writes a submitted intake, even one
    submitted a moment ago (the write is conditional in SQL).

    Each field saved here is listed under CHAT_SAVED until the form has shown it, so a
    stale open form cannot save over it (see `protect`)."""
    from dashboard import intake as _intake, intake_public as _ip
    email = (email or "").strip().lower()
    if not email:
        return None
    _intake.lock_row(cx, email)
    try:
        if _intake.is_submitted(cx, email):
            return None
        row = _intake.get_response(cx, email)
        current = dict((row["answers"] if row else {}) or {})
        if not updates:
            return current, []
        merged = _ip.merge_answers(current, updates)
        valid = {f["id"] for f in _intake._fields()} - {"email"}
        learned = sorted(k for k in updates
                         if k in valid and k in merged and merged.get(k) != current.get(k))
        if not learned:
            return current, []
        listed = [k for k in (current.get(CHAT_SAVED) or []) if isinstance(k, str)]
        merged[CHAT_SAVED] = sorted(set(listed) | set(learned))
        merged["email"] = email
        if not _intake.save_draft_unless_submitted(cx, email, merged, now_iso):
            return None
        return merged, learned
    finally:
        try:
            cx.commit()     # ends the row lock on every path
        except Exception:
            pass


LOADED = "_loaded"


def protect(stored, incoming):
    """What a FORM save should store, given the stored answers.

    The form sends, under LOADED, the values it showed when it last rendered. A field
    the chat saved is then decided by comparing three values:
    - the form's value differs from what it loaded: she changed it on this form, so the
      form wins, a clear included, and the field leaves the list;
    - the form loaded the chat's value: it has shown it, so the field leaves the list;
    - otherwise the form is stale for this field: the chat's value is kept.
    A form that sends no LOADED (a page loaded before this change) keeps the chat's
    value only over a blank. A field missing from the form save keeps its value. The
    marker is rebuilt from the stored row, never taken from the request."""
    stored = dict(stored or {})
    out = dict(incoming or {})
    loaded = out.pop(LOADED, None)
    if not isinstance(loaded, dict):
        loaded = None
    out.pop(CHAT_SAVED, None)
    listed = [k for k in (stored.get(CHAT_SAVED) or []) if isinstance(k, str)]
    keep = []
    for k in listed:
        if k not in stored or _blank(stored[k]):
            continue
        if loaded is not None and k in loaded:
            if k in out and out[k] != loaded[k]:
                continue                      # her own edit on this form
            out[k] = stored[k]
            if loaded[k] != stored[k]:
                keep.append(k)                # this form never showed the chat's value
            continue
        if k not in out or _blank(out[k]):
            out[k] = stored[k]
            keep.append(k)
    if keep:
        out[CHAT_SAVED] = keep
    return out
