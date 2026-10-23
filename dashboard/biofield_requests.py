"""Biofield test requests: a "Requested" line in the local app's "Your authored tests".

Glen, 2026-10-23: "When someone asks for a Biofield test, e.g. by email or in chat, add
them to the list and notify me." Brief: 00 System/pillar-inboxes/platform/
2026-10-23-biofield-request-capture.md.

His rulings:
  - A request becomes a "Requested" line in that list, never a separate list.
  - A clear request ("Can I book a Biofield Analysis?") adds the line at once.
  - A possible request ("I'd like to get checked again sometime") waits for his yes.
  - "Biofield Analysis" is only the $300/$200 consultation. A free scan or a Remedy
    Match request is a possible request at most, never a clear one.

Where things live:
  - Local (the Biofield app's own SQLite DB): biofield_test_requests, one row per
    request, and biofield_request_seen, the message ids already classified (ids and a
    verdict only, no address), so a message is never sent to the model twice.
  - Prod: biofield_request_flags, the portal-chat messages the classifier flagged. The
    local job pulls them through an owner-only GET and files them here.

Nothing here sends mail or replies to the requester. The classifier is injected
(`complete(system, user) -> str`) so tests never call the API.
"""
import datetime as _dt
import json
import re
import sqlite3

TABLE = "biofield_test_requests"
SEEN_TABLE = "biofield_request_seen"
FLAGS_TABLE = "biofield_request_flags"
MODEL = "claude-haiku-4-5-20251001"
SOURCES = ("email", "portal", "ghl")
CERTAINTIES = ("clear", "possible", "none")
# requested: a line with a Start test button. pending_yes: waits for Glen's Yes or No.
# declined: Glen said No. started: the test exists (test_id). duplicate: the person
# already had an open line or an unfinished test, so no second line was made; the row
# keeps the message from being filed or alerted again.
OPEN_STATUSES = ("requested", "pending_yes")
HST = _dt.timezone(_dt.timedelta(hours=-10))


def _now():
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ── The classifier ───────────────────────────────────────────────────────────

SYSTEM = (
    "You read one message a person sent to Dr. Glen Swartwout's practice, by email, "
    "portal chat or text. Decide whether they are asking for a Biofield Analysis.\n"
    "A Biofield Analysis is Dr. Glen's paid consultation ($300, or $200 for a returning "
    "client). It is NOT the free voice scan, and NOT the Remedy Match scan reading or its "
    "$100 program.\n"
    "Answer \"clear\" only when the person plainly asks to book, schedule, have or get a "
    "Biofield Analysis (or a Biofield test or session), for themself or for someone in "
    "their care. Example: \"Can I book a Biofield Analysis?\"\n"
    "Answer \"possible\" when they may want one but do not plainly ask: a wish to be "
    "checked or tested again, a question about how or when to get one, interest in a "
    "consultation without naming it, or a request for a free scan or for Remedy Match. "
    "A free scan or Remedy Match request is never \"clear\". Example: \"I'd like to get "
    "checked again sometime.\"\n"
    "Answer \"none\" for everything else. That includes a message about a Biofield "
    "Analysis they already had, its report, its invoice or its remedies, and mail that "
    "only mentions the word.\n"
    "If they ask for someone else (a child, partner, parent or animal in their care), give "
    "that person's name, and their email only if the message states it.\n"
    "Return STRICT JSON only, no prose: {\"certainty\": \"clear\"|\"possible\"|\"none\", "
    "\"quote\": \"the person's own words that make the request, one line, at most 200 "
    "characters\", \"for_name\": \"\", \"for_email\": \"\"}"
)

_BIOFIELD = re.compile(r"\bbio[\s-]?field", re.I)
# Only these lift a message that also names a free or scan offer to "clear".
_BIOFIELD_EXPLICIT = re.compile(r"\bbio[\s-]?field\s+analys[ie]s\b|\bconsult(?:ation)?s?\b",
                                re.I)
# The free and scan offers: a Biofield scan, a scan reading, anything free, Remedy Match,
# the $100 program, the voice scan and E4L.
_FREE_OR_SCAN = re.compile(r"\bbio[\s-]?field\s+scan|\bscan\s+reading|\bfree\b|"
                           r"remedy\s*match|\$\s?100\b|voice\s*scan|\be4l\b", re.I)
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _parse(text):
    m = re.search(r"\{.*\}", text or "", re.S)
    if not m:
        return {}
    try:
        d = json.loads(m.group(0))
    except Exception:  # noqa: BLE001
        return {}
    return d if isinstance(d, dict) else {}


def one_line(s, cap=200):
    s = " ".join(str(s or "").split())
    return s if len(s) <= cap else s[:cap - 1].rstrip() + "…"


def guard_certainty(certainty, text):
    """The model's verdict, held to Glen's naming rule in code, not only in the prompt.

    "clear" needs the message to name Biofield. When it also names a free or scan offer
    (a Biofield scan, a scan reading, "free", Remedy Match, $100, the voice scan, E4L),
    it must say "Biofield Analysis" or "consultation"/"consult", or it is a possible
    request at most."""
    if certainty != "clear":
        return certainty
    if not _BIOFIELD.search(text or ""):
        return "possible"
    if _FREE_OR_SCAN.search(text or "") and not _BIOFIELD_EXPLICIT.search(text or ""):
        return "possible"
    return "clear"


def classify(complete, text, sender_name="", sender_email=""):
    """{certainty, quote, for_name, for_email}. Never raises.

    certainty is "error" when the model call failed or its reply was unreadable. An
    error is never filed or remembered, so the next run asks again."""
    out = {"certainty": "none", "quote": "", "for_name": "", "for_email": ""}
    err = dict(out, certainty="error")
    body = str(text or "").strip()
    if not body:
        return out
    user = (f"FROM: {sender_name or ''} <{sender_email or ''}>\n"
            f"MESSAGE:\n{body[:4000]}")
    try:
        data = _parse(complete(SYSTEM, user))
    except Exception as e:  # noqa: BLE001
        print(f"[biofield-requests] classify failed: {e!r}", flush=True)
        return err
    c = data.get("certainty")
    if c not in CERTAINTIES:
        print(f"[biofield-requests] unreadable verdict: {str(data)[:120]!r}", flush=True)
        return err
    c = guard_certainty(c, body)
    if c == "none":
        return out
    quote = one_line(data.get("quote")) or one_line(body)
    for_email = str(data.get("for_email") or "").strip().lower()
    if not _EMAIL.match(for_email):
        for_email = ""
    return {"certainty": c, "quote": quote,
            "for_name": one_line(data.get("for_name"), 120), "for_email": for_email}


def haiku_complete(client=None, max_tokens=200):
    """complete(system, user) through the anthropic SDK, as portal_triage does."""
    if client is None:
        import anthropic
        client = anthropic.Anthropic()

    def _complete(system, user):
        m = client.messages.create(model=MODEL, max_tokens=max_tokens, system=system,
                                   messages=[{"role": "user", "content": user}])
        return m.content[0].text
    return _complete


def request_for(verdict, sender_name, sender_email):
    """(name, email, payer_email) for the line. A caregiver asking for a household
    member makes the line the member's, with the caregiver as payer. A member with no
    email of their own is filed under the caregiver's address."""
    sender_email = (sender_email or "").strip().lower()
    member = (verdict.get("for_name") or "").strip()
    member_email = (verdict.get("for_email") or "").strip().lower()
    if member_email == sender_email:
        member_email = ""
    if member or member_email:
        return member, (member_email or sender_email), sender_email
    return (sender_name or "").strip(), sender_email, ""


# ── Local store ──────────────────────────────────────────────────────────────

def ensure_schema(cx):
    cx.execute("""CREATE TABLE IF NOT EXISTS biofield_test_requests(
        id INTEGER PRIMARY KEY AUTOINCREMENT, email TEXT NOT NULL, name TEXT,
        source TEXT NOT NULL, source_ref TEXT NOT NULL, requested_at TEXT, quote TEXT,
        certainty TEXT NOT NULL, payer_email TEXT, status TEXT NOT NULL, test_id TEXT,
        created_at TEXT NOT NULL, note TEXT, decided_at TEXT, alerted_at TEXT)""")
    cx.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_biofield_test_requests_source "
               "ON biofield_test_requests(source, source_ref)")
    cx.execute("""CREATE TABLE IF NOT EXISTS biofield_request_seen(
        source TEXT NOT NULL, source_ref TEXT NOT NULL, certainty TEXT, seen_at TEXT,
        PRIMARY KEY(source, source_ref))""")
    cx.commit()


def _has_table(cx, name):
    return cx.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                      (name,)).fetchone() is not None


def is_seen(cx, source, source_ref):
    """True when this message was already classified, whatever the verdict."""
    if not _has_table(cx, TABLE):
        return False
    if cx.execute(f"SELECT 1 FROM {TABLE} WHERE source=? AND source_ref=?",
                  (source, str(source_ref))).fetchone():
        return True
    return cx.execute(f"SELECT 1 FROM {SEEN_TABLE} WHERE source=? AND source_ref=?",
                      (source, str(source_ref))).fetchone() is not None


def mark_seen(cx, source, source_ref, certainty):
    ensure_schema(cx)
    cx.execute(f"INSERT OR IGNORE INTO {SEEN_TABLE}(source, source_ref, certainty, seen_at) "
               "VALUES(?,?,?,?)", (source, str(source_ref), certainty, _now()))
    cx.commit()


def _norm_name(s):
    s = re.sub(r"[^a-z ]", " ", str(s or "").casefold())
    s = " ".join(w for w in s.split() if w != "unnamed")
    return s


def _shared(person):
    """True when this line or test sits on an address more than one person uses: a member
    filed under the caregiver's address, or a household address."""
    e = (person.get("email") or "").strip().lower()
    return bool(person.get("household")) or bool(
        e and (person.get("payer_email") or "").strip().lower() == e)


def same_person(a, b):
    """Matched by address, in both directions. When either side sits on a shared
    address, the normalised names must be equal too ("Sam" is not "Samantha Doe"),
    so a caregiver's own request and a member's line never hide each other. A side
    with no name cannot be told apart, and counts as the same person."""
    if (a.get("email") or "").strip().lower() != (b.get("email") or "").strip().lower():
        return False
    if _shared(a) or _shared(b):
        na, nb = _norm_name(a.get("name")), _norm_name(b.get("name"))
        if na and nb and na != nb:
            return False
    return True


def unfinished_tests(cx, fetch_orders):
    """[{test_id, email, name, note}] for authored tests that are not done, as the list
    decides it (biofield_progress.build, is_done). One orders call. When the orders
    cannot be read, a test with an invoice step left counts as unfinished, and the note
    says why."""
    from dashboard import biofield_progress as bp
    from dashboard.biofield_authoring import list_authored
    authored = list_authored(cx)
    cx.row_factory = None
    if not authored:
        return []
    local, links = bp.local_steps(cx), bp.stored_links(cx)
    orders, err = fetch_orders(sorted(set(links.values())))
    # A household address: tests on it carry two or more different names.
    names = {}
    for t in authored:
        n = _norm_name(t.get("name"))
        if n:
            names.setdefault((t.get("email") or "").strip().lower(), set()).add(n)
    out = []
    for t, p in zip(authored, bp.build(authored, local, links, orders, err)):
        if p.get("all_done") or not (t.get("email") or "").strip():
            continue
        out.append({"test_id": t["test_id"], "email": t["email"],
                    "household": len(names.get(t["email"].strip().lower(), ())) > 1,
                    "name": "" if t.get("name") == "(unnamed)" else (t.get("name") or ""),
                    "note": (f" (orders could not be read: {err}, so it may be finished)"
                             if err else "")})
    return out


def record(cx, req, unfinished=()):
    """File one classified message. Returns {"kind", "id", "note"}.

    kind: "error" (the classifier failed: nothing is filed or remembered, so the next
    run retries), "none" (not a request, remembered as seen), "seen" (this source_ref is
    already filed), "added" (a Requested line), "pending" (waits for Glen's yes),
    "upgraded" (a clear request lifted the person's waiting line to Requested) or
    "duplicate" (the person already has an open line or an unfinished test, so no
    second line). The checks and the write run in one write transaction."""
    source, ref = req["source"], str(req["source_ref"])
    if source not in SOURCES:
        raise ValueError(f"unknown source {source!r}")
    if req.get("certainty") == "error":
        return {"kind": "error", "id": None, "note": ""}
    ensure_schema(cx)
    if req.get("certainty") not in ("clear", "possible"):
        mark_seen(cx, source, ref, "none")
        return {"kind": "none", "id": None, "note": ""}
    me = {"email": (req.get("email") or "").strip().lower(),
          "name": (req.get("name") or "").strip(),
          "payer_email": (req.get("payer_email") or "").strip().lower()}
    clear = req["certainty"] == "clear"
    cx.commit()
    cx.execute("BEGIN IMMEDIATE")
    try:
        if cx.execute(f"SELECT 1 FROM {TABLE} WHERE source=? AND source_ref=?",
                      (source, ref)).fetchone() or cx.execute(
                f"SELECT 1 FROM {SEEN_TABLE} WHERE source=? AND source_ref=?",
                (source, ref)).fetchone():
            cx.rollback()
            return {"kind": "seen", "id": None, "note": ""}
        note, waiting = "", None
        for rid, oe, on, op, st in cx.execute(
                f"SELECT id, email, name, payer_email, status FROM {TABLE} "
                "WHERE status IN (?,?) ORDER BY id", OPEN_STATUSES).fetchall():
            if not same_person(me, {"email": oe, "name": on, "payer_email": op}):
                continue
            if st == "requested":
                note = f"already has an open Requested line (line {rid})"
                break
            waiting = waiting or rid
        if not note and waiting and clear:
            # A clear request lifts the waiting line: Glen's yes is not needed now.
            cx.execute(f"UPDATE {TABLE} SET status='requested', certainty='clear', quote=?, "
                       "requested_at=?, alerted_at=NULL, decided_at=? "
                       "WHERE id=? AND status='pending_yes'",
                       (one_line(req.get("quote")), req.get("requested_at") or _now(), _now(),
                        waiting))
            cx.execute(f"INSERT OR IGNORE INTO {SEEN_TABLE}(source, source_ref, certainty, "
                       "seen_at) VALUES(?,?,?,?)", (source, ref, "clear", _now()))
            cx.commit()
            return {"kind": "upgraded", "id": waiting, "note": ""}
        if not note and waiting:
            note = f"already has a possible request waiting for your yes (line {waiting})"
        if not note:
            for t in unfinished or ():
                if same_person(me, t):
                    note = (f"already has test {t['test_id']}, not finished"
                            + (t.get("note") or ""))
                    break
        if note:
            status, kind = "duplicate", "duplicate"
        elif clear:
            status, kind = "requested", "added"
        else:
            status, kind = "pending_yes", "pending"
        from dashboard import dbwrite
        new_id = dbwrite.insert_returning_id(
            cx,
            f"INSERT INTO {TABLE}(email, name, source, source_ref, requested_at, quote, "
            "certainty, payer_email, status, created_at, note) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (me["email"], me["name"], source, ref, req.get("requested_at") or _now(),
             one_line(req.get("quote")), req["certainty"], me["payer_email"] or None, status,
             _now(), note or None))
        cx.commit()
        return {"kind": kind, "id": new_id, "note": note}
    except Exception:
        cx.rollback()
        raise


def _rows(cx, sql, args=()):
    cx.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in cx.execute(sql, args).fetchall()]
    finally:
        cx.row_factory = None


def list_open(cx):
    """The open lines for the list, newest first. Reads only."""
    if not _has_table(cx, TABLE):
        return []
    return _rows(cx, f"SELECT * FROM {TABLE} WHERE status IN (?,?) "
                     "ORDER BY requested_at DESC, id DESC", OPEN_STATUSES)


def get(cx, rid):
    if not _has_table(cx, TABLE):
        return None
    r = _rows(cx, f"SELECT * FROM {TABLE} WHERE id=?", (int(rid),))
    return r[0] if r else None


def approve(cx, rid):
    """Glen's Yes: a possible request becomes a Requested line. (ok, message)."""
    ensure_schema(cx)
    n = cx.execute(f"UPDATE {TABLE} SET status='requested', decided_at=? "
                   "WHERE id=? AND status='pending_yes'", (_now(), int(rid))).rowcount
    cx.commit()
    return (True, "") if n == 1 else (False, "This request is no longer waiting for a yes.")


def decline(cx, rid):
    """Glen's No: the possible request leaves the list. (ok, message)."""
    ensure_schema(cx)
    n = cx.execute(f"UPDATE {TABLE} SET status='declined', decided_at=? "
                   "WHERE id=? AND status='pending_yes'", (_now(), int(rid))).rowcount
    cx.commit()
    return (True, "") if n == 1 else (False, "This request is no longer waiting for a yes.")


def today_hst():
    return _dt.datetime.now(HST).date().isoformat()


STUCK_AFTER = _dt.timedelta(minutes=5)


def start(cx, rid, date=None, now=None):
    """Start test: create the authored test, link the request to it. (test_id, error).

    The request is claimed first with a conditional update, so two clicks make one
    test: the second gets the first one's test_id once it is stored, or an error while
    it is being made. The test is created with its name, email and date in one call,
    so no blank test is left. A failed create puts the request back. A claim left at
    started with no test for more than STUCK_AFTER (a crash mid-start) can be taken
    again."""
    from dashboard.biofield_authoring import create_test, init_auth_tables
    ensure_schema(cx)
    now = now or _dt.datetime.now(_dt.timezone.utc)
    cutoff = (now - STUCK_AFTER).strftime("%Y-%m-%dT%H:%M:%SZ")
    row = get(cx, rid)
    if not row:
        return None, "No such request."
    if row["status"] == "started" and row.get("test_id"):
        return row["test_id"], None
    stuck = row["status"] == "started" and str(row.get("decided_at") or "") < cutoff
    if row["status"] == "started" and not stuck:
        return None, "This test is being created. Reload the list."
    if row["status"] != "requested" and not stuck:
        return None, "Only a Requested line can start a test."
    init_auth_tables(cx)
    n = cx.execute(f"UPDATE {TABLE} SET status='started', decided_at=? "
                   "WHERE id=? AND test_id IS NULL AND (status='requested' OR "
                   "(status='started' AND COALESCE(decided_at,'')<?))",
                   (now.strftime("%Y-%m-%dT%H:%M:%SZ"), int(rid), cutoff)).rowcount
    cx.commit()
    if n != 1:
        return None, "This request was started already. Reload the list."
    try:
        tid = create_test(cx, row.get("name") or "", row.get("email") or "",
                          date or today_hst())
        cx.execute(f"UPDATE {TABLE} SET test_id=? WHERE id=?", (tid, int(rid)))
        cx.commit()
    except Exception:
        cx.rollback()
        cx.execute(f"UPDATE {TABLE} SET status='requested', decided_at=NULL "
                   "WHERE id=? AND test_id IS NULL", (int(rid),))
        cx.commit()
        raise
    return tid, None


# ── Alerts (composed here, sent by the vault job through send-glen-only) ─────

def unalerted(cx):
    if not _has_table(cx, TABLE):
        return []
    return _rows(cx, f"SELECT * FROM {TABLE} WHERE alerted_at IS NULL ORDER BY id")


def mark_alerted(cx, ids):
    ids = [int(i) for i in ids]
    if not ids:
        return 0
    ph = ",".join("?" * len(ids))
    n = cx.execute(f"UPDATE {TABLE} SET alerted_at=? WHERE id IN ({ph}) AND alerted_at IS NULL",
                   (_now(), *ids)).rowcount
    cx.commit()
    return n


SOURCE_LABEL = {"email": "email", "portal": "portal chat", "ghl": "GoHighLevel"}


def line_url(base_url, rid):
    return f"{base_url.rstrip('/')}/#req-{int(rid)}"


def describe(row):
    who = row.get("name") or row.get("email") or "Someone"
    payer = row.get("payer_email")
    by = f", asked by {payer}" if payer and payer != row.get("email") else ""
    return f"{who} <{row.get('email') or ''}>{by}"


def compose_alert(rows, base_url):
    """(subject, body) for ONE email covering every row, or (None, None) for none."""
    if not rows:
        return None, None
    added = [r for r in rows if r["status"] in ("requested", "started")]
    pending = [r for r in rows if r["status"] == "pending_yes"]
    dup = [r for r in rows if r["status"] == "duplicate"]
    other = [r for r in rows if r not in added and r not in pending and r not in dup]
    parts = []
    if added:
        parts.append(f"{len(added)} added")
    if pending:
        parts.append(f"{len(pending)} waiting for your yes")
    if dup:
        parts.append(f"{len(dup)} not added")
    subject = "Biofield requests: " + ", ".join(parts or [f"{len(rows)} filed"])
    lines = []

    def block(title, rs, tail):
        if not rs:
            return
        lines.append(title)
        for r in rs:
            lines.append(f"- {describe(r)}, by {SOURCE_LABEL.get(r['source'], r['source'])} "
                         f"on {str(r.get('requested_at') or '')[:10]}")
            lines.append(f"  \"{one_line(r.get('quote'))}\"")
            lines.append(tail(r))
        lines.append("")

    block("Added to Your authored tests:", added,
          lambda r: f"  Line: {line_url(base_url, r['id'])}")
    block("Possible requests. Answer Yes or No on the line in the list:", pending,
          lambda r: f"  Line: {line_url(base_url, r['id'])}")
    block("Not added, so there is no second line:", dup,
          lambda r: f"  Why: {r.get('note') or 'already open'}.")
    block("Other:", other, lambda r: f"  Status: {r['status']}.")
    lines.append("Nobody was replied to. The requester hears from you, Rae or communication.")
    return subject, "\n".join(lines)


# ── Prod: portal-chat flags (Postgres in prod; RETURNING via dbwrite) ────────

def init_flags_table(cx):
    cx.execute("""CREATE TABLE IF NOT EXISTS biofield_request_flags(
        id INTEGER PRIMARY KEY AUTOINCREMENT, email TEXT, name TEXT, certainty TEXT,
        quote TEXT, for_name TEXT, for_email TEXT, message TEXT, created_at TEXT,
        dedupe_key TEXT)""")
    cx.commit()
    try:
        cx.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_biofield_request_flags_dedupe "
                   "ON biofield_request_flags(dedupe_key)")
        cx.commit()
    except Exception as e:  # noqa: BLE001 - two workers racing; the other made it
        cx.rollback()
        print(f"[biofield-requests] dedupe index not created here: {type(e).__name__}",
              flush=True)


def add_flag(cx, email, name, verdict, message, now=None):
    """Store one flagged portal message, once per client, message and 10 minutes.
    Returns (id, created). Portable: RETURNING on Postgres, never lastrowid."""
    from dashboard import db as _db, dbwrite
    from dashboard.portal_triage import dedupe_key, message_key
    init_flags_table(cx)
    e = (email or "").strip().lower()
    now_dt = now or _dt.datetime.now(_dt.timezone.utc)
    dkey = dedupe_key(e, message_key(message), now_dt, 10)
    row = cx.execute("SELECT id FROM biofield_request_flags WHERE dedupe_key=?",
                     (dkey,)).fetchone()
    if row:
        return row[0], False
    try:
        new_id = dbwrite.insert_returning_id(
            cx,
            "INSERT INTO biofield_request_flags(email, name, certainty, quote, for_name, "
            "for_email, message, created_at, dedupe_key) VALUES(?,?,?,?,?,?,?,?,?)",
            (e, (name or "").strip(), verdict["certainty"], one_line(verdict.get("quote")),
             verdict.get("for_name") or "", verdict.get("for_email") or "",
             str(message or "")[:4000], now_dt.strftime("%Y-%m-%dT%H:%M:%SZ"), dkey))
    except _db.IntegrityError:
        cx.rollback()
        row = cx.execute("SELECT id FROM biofield_request_flags WHERE dedupe_key=?",
                         (dkey,)).fetchone()
        if not row:
            raise
        return row[0], False
    cx.commit()
    return new_id, True


def list_flags(cx, since, limit=500):
    """Flags created at or after `since` (UTC ISO), oldest first. Reads only."""
    from dashboard import db as _db
    if _db.backend_of(cx) == "postgres":
        exists = cx.execute(
            "SELECT 1 FROM information_schema.tables WHERE table_schema=current_schema() "
            "AND table_name=?", (FLAGS_TABLE,)).fetchone()
    else:
        exists = cx.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                            (FLAGS_TABLE,)).fetchone()
    if not exists:
        return []
    cols = ("id", "email", "name", "certainty", "quote", "for_name", "for_email",
            "created_at")
    rows = cx.execute(f"SELECT {', '.join(cols)} FROM biofield_request_flags "
                      "WHERE created_at>=? ORDER BY id LIMIT ?",
                      (since, int(limit))).fetchall()
    return [dict(zip(cols, r)) for r in rows]


def flag_to_request(flag):
    """A prod flag as a record() request (source portal, source_ref the flag id)."""
    name, email, payer = request_for(flag, flag.get("name"), flag.get("email"))
    return {"source": "portal", "source_ref": str(flag["id"]), "email": email,
            "name": name, "payer_email": payer, "requested_at": flag.get("created_at"),
            "quote": flag.get("quote"), "certainty": flag.get("certainty")}
