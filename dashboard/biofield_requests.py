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
  - A paid Biofield Analysis order is a request too ("yes, paid analysis alerts",
    2026-10-23). An order of the analysis alone is clear. An order that bills it with
    other items, usually the remedies from an analysis already done, is possible and
    waits for his yes ("bundled as possible").

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
SOURCES = ("email", "portal", "ghl", "order")
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
    "If they ask for anyone other than themself (a child, partner, parent or animal in "
    "their care), list each of those people separately in \"people\", with their name, "
    "and their email only if the message states it. If they also want one themself, list "
    "themself too, with their own name. If the request is only for themself, leave "
    "\"people\" empty.\n"
    "Return STRICT JSON only, no prose: {\"certainty\": \"clear\"|\"possible\"|\"none\", "
    "\"quote\": \"the person's own words that make the request, one line, at most 200 "
    "characters\", \"people\": [{\"name\": \"\", \"email\": \"\"}]}"
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


def mentions_biofield(text):
    """True when the text names Biofield (the same pattern the clear guard uses)."""
    return bool(_BIOFIELD.search(text or ""))


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


def _people(data):
    """The people a verdict names: [{name, email}], at most 10. Accepts the older
    for_name/for_email shape too. An email that is not an address is dropped."""
    raw = data.get("people")
    if not isinstance(raw, list):
        raw = ([{"name": data.get("for_name"), "email": data.get("for_email")}]
               if (data.get("for_name") or data.get("for_email")) else [])
    out = []
    for p in raw[:10]:
        if not isinstance(p, dict):
            continue
        name = one_line(p.get("name"), 120)
        email = str(p.get("email") or "").strip().lower()
        if not _EMAIL.match(email):
            email = ""
        if name or email:
            out.append({"name": name, "email": email})
    return out


def classify(complete, text, sender_name="", sender_email=""):
    """{certainty, quote, people}. Never raises.

    certainty is "error" when the model call failed or its reply was unreadable. An
    error is never filed or remembered, so the next run asks again."""
    out = {"certainty": "none", "quote": "", "people": []}
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
    return {"certainty": c, "quote": quote, "people": _people(data)}


def haiku_complete(client=None, max_tokens=300):
    """complete(system, user) through the anthropic SDK, as portal_triage does."""
    if client is None:
        import anthropic
        client = anthropic.Anthropic()

    def _complete(system, user):
        m = client.messages.create(model=MODEL, max_tokens=max_tokens, system=system,
                                   messages=[{"role": "user", "content": user}])
        return m.content[0].text
    return _complete


def requests_for(verdict, sender_name, sender_email):
    """[(name, email, payer_email)], one per person the request is for.

    No people named: the sender's own line. A caregiver asking for household members
    gets one line per member, each with the caregiver as payer; a member with no email
    of their own is filed under the caregiver's address. The sender listed among them
    (same address, or same name with no address) is their own line, with no payer."""
    sender_email = (sender_email or "").strip().lower()
    sender_name = (sender_name or "").strip()
    people = verdict.get("people") or []
    if not people:
        return [(sender_name, sender_email, "")]
    out, seen = [], set()
    for p in people:
        name, email = (p.get("name") or "").strip(), (p.get("email") or "").strip().lower()
        if email == sender_email or (not email and name and
                                     _norm_name(name) == _norm_name(sender_name)):
            line = (sender_name or name, sender_email, "")
        else:
            line = (name, email or sender_email, sender_email)
        key = (line[1], _norm_name(line[0]))
        if key not in seen:
            seen.add(key)
            out.append(line)
    return out


def source_refs(ref, n):
    """One source_ref per line made from one message: the message id for the first,
    then id#2, id#3."""
    return [str(ref)] + [f"{ref}#{i}" for i in range(2, n + 1)]


# ── Local store ──────────────────────────────────────────────────────────────

def ensure_schema(cx):
    cx.execute("""CREATE TABLE IF NOT EXISTS biofield_test_requests(
        id INTEGER PRIMARY KEY AUTOINCREMENT, email TEXT NOT NULL, name TEXT,
        source TEXT NOT NULL, source_ref TEXT NOT NULL, requested_at TEXT, quote TEXT,
        certainty TEXT NOT NULL, payer_email TEXT, status TEXT NOT NULL, test_id TEXT,
        created_at TEXT NOT NULL, note TEXT, decided_at TEXT, alerted_at TEXT,
        noted_at TEXT, quiet INTEGER NOT NULL DEFAULT 0)""")
    cx.commit()
    # A table made by an earlier version lacks the later columns: add each one missing.
    for col, typ in _LATER_COLUMNS[TABLE]:
        add_column(cx, TABLE, col, typ)
    cx.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_biofield_test_requests_source "
               "ON biofield_test_requests(source, source_ref)")
    cx.execute("""CREATE TABLE IF NOT EXISTS biofield_request_seen(
        source TEXT NOT NULL, source_ref TEXT NOT NULL, certainty TEXT, seen_at TEXT,
        PRIMARY KEY(source, source_ref))""")
    cx.commit()


# Columns that were not in a table's first version. Each is nullable or defaulted, so
# adding it to an existing table is safe. (2e1b328a's local table already has noted_at.)
_LATER_COLUMNS = {
    "biofield_test_requests": (("note", "TEXT"), ("decided_at", "TEXT"),
                               ("alerted_at", "TEXT"), ("noted_at", "TEXT"),
                               ("quiet", "INTEGER NOT NULL DEFAULT 0")),
    "biofield_request_flags": (("note", "TEXT"),),
}


def add_column(cx, table, col, typ):
    """Add one column if it is missing, on SQLite or Postgres (dashboard.db.column_exists;
    pgcompat makes ADD COLUMN IF NOT EXISTS on Postgres). Race-safe like
    portal_triage._add_column: the loser rolls back, re-checks and carries on."""
    from dashboard import db as _db
    if _db.column_exists(cx, table, col):
        return False
    try:
        cx.execute(f"ALTER TABLE {table} ADD COLUMN {col} {typ}")
        cx.commit()
        return True
    except Exception:
        cx.rollback()
        if not _db.column_exists(cx, table, col):
            raise
        return False


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
    ea = (a.get("email") or "").strip().lower()
    if not ea or ea != (b.get("email") or "").strip().lower():
        # Two lines with no address are not one person (a paid order can lack one).
        return False
    if _shared(a) or _shared(b):
        na, nb = _norm_name(a.get("name")), _norm_name(b.get("name"))
        if na and nb and na != nb:
            return False
    return True


OPEN_TEST_DAYS = 60
DUP_QUIET_DAYS = 7


def unfinished_tests(cx, now=None):
    """[{test_id, email, name, payer_email, household}] for authored tests that still
    block a new line: the report has not been emailed (no biofield_test_notified row)
    and the test was created in the last OPEN_TEST_DAYS days. Orders play no part.

    payer_email comes from the request a test was started from, so a member's test
    filed under the caregiver's address is compared by name. Reads only."""
    if not _has_table(cx, "biofield_auth_tests"):
        return []
    now = now or _dt.datetime.now(_dt.timezone.utc)
    since = (now - _dt.timedelta(days=OPEN_TEST_DAYS)).strftime("%Y-%m-%dT%H:%M:%S")
    notified = _has_table(cx, "biofield_test_notified")
    linked = _has_table(cx, TABLE)
    rows = cx.execute(
        "SELECT t.id, t.name, t.email, t.created_at, "
        + ("(SELECT r.payer_email FROM biofield_test_requests r WHERE r.test_id='a'||t.id "
           "AND COALESCE(r.payer_email,'')<>'' LIMIT 1) " if linked else "NULL ")
        + "FROM biofield_auth_tests t WHERE TRIM(COALESCE(t.email,''))<>''"
        + (" AND NOT EXISTS (SELECT 1 FROM biofield_test_notified n "
           "WHERE n.test_id='a'||t.id)" if notified else "")).fetchall()
    every = cx.execute("SELECT email, name FROM biofield_auth_tests").fetchall()
    names = {}
    for e, n in every:
        if _norm_name(n):
            names.setdefault((e or "").strip().lower(), set()).add(_norm_name(n))
    out = []
    for tid, name, email, created, payer in rows:
        if str(created or "")[:19] < since:
            continue
        e = email.strip().lower()
        out.append({"test_id": f"a{tid}", "email": e,
                    "name": "" if name == "(unnamed)" else (name or ""),
                    "payer_email": (payer or "").strip().lower(),
                    "household": len(names.get(e, ())) > 1, "note": ""})
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
                       "requested_at=?, alerted_at=NULL, noted_at=NULL, decided_at=? "
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
        alerted = None
        if note:
            status, kind = "duplicate", "duplicate"
            # Glen was emailed that this person was not added, by this source, in the
            # last DUP_QUIET_DAYS: file it quiet, so the alert does not repeat. Only a
            # duplicate that was really alerted counts, never a quiet one, so a person
            # who writes every week is alerted at most once in each DUP_QUIET_DAYS.
            since = (_dt.datetime.now(_dt.timezone.utc)
                     - _dt.timedelta(days=DUP_QUIET_DAYS)).strftime("%Y-%m-%dT%H:%M:%SZ")
            for oe, on, op in cx.execute(
                    f"SELECT email, name, payer_email FROM {TABLE} WHERE status='duplicate' "
                    "AND source=? AND COALESCE(quiet,0)=0 AND alerted_at IS NOT NULL "
                    "AND alerted_at>=?", (source, since)).fetchall():
                if same_person(me, {"email": oe, "name": on, "payer_email": op}):
                    alerted = _now()
                    note += f"; alert not repeated within {DUP_QUIET_DAYS} days"
                    break
        elif clear:
            status, kind = "requested", "added"
        else:
            status, kind = "pending_yes", "pending"
        from dashboard import dbwrite
        new_id = dbwrite.insert_returning_id(
            cx,
            f"INSERT INTO {TABLE}(email, name, source, source_ref, requested_at, quote, "
            "certainty, payer_email, status, created_at, note, alerted_at, noted_at, quiet) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (me["email"], me["name"], source, ref, req.get("requested_at") or _now(),
             one_line(req.get("quote")), req["certainty"], me["payer_email"] or None, status,
             _now(), (note or req.get("note") or None), alerted, alerted,
             1 if alerted else 0))
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


def unnoted(cx):
    """Rows not yet written to platform/alerts.md (written whatever the email does)."""
    if not _has_table(cx, TABLE):
        return []
    return _rows(cx, f"SELECT * FROM {TABLE} WHERE noted_at IS NULL ORDER BY id")


def mark_noted(cx, ids):
    ids = [int(i) for i in ids]
    if not ids:
        return 0
    ph = ",".join("?" * len(ids))
    n = cx.execute(f"UPDATE {TABLE} SET noted_at=? WHERE id IN ({ph}) AND noted_at IS NULL",
                   (_now(), *ids)).rowcount
    cx.commit()
    return n


def mark_alerted(cx, ids):
    ids = [int(i) for i in ids]
    if not ids:
        return 0
    ph = ",".join("?" * len(ids))
    n = cx.execute(f"UPDATE {TABLE} SET alerted_at=? WHERE id IN ({ph}) AND alerted_at IS NULL",
                   (_now(), *ids)).rowcount
    cx.commit()
    return n


SOURCE_LABEL = {"email": "email", "portal": "portal chat", "ghl": "GoHighLevel",
                "order": "paid order"}


def line_url(base_url, rid):
    return f"{base_url.rstrip('/')}/#req-{int(rid)}"


def line_ref(base_url, row):
    """Where the line is: the client's name in "Your authored tests", and the link on
    this Mac. The name finds it from another Mac, where the link does not open."""
    who = row.get("name") or row.get("email") or "the client"
    return f"{who} in Your authored tests, {line_url(base_url, row['id'])}"


def describe(row):
    who = one_line(row.get("name") or row.get("email") or "Someone", 80)
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
            if r["source"] == "order":
                # Written by this code from the order, never by the client.
                lines.append(f"  Order: {one_line(r.get('quote'))}")
            else:
                lines.append(f"  \"{one_line(r.get('quote'))}\"")
            lines.append(tail(r))
        lines.append("")

    def tail(r):
        extra = f"\n  Note: {r['note']}." if r.get("note") else ""
        return f"  Line: {line_ref(base_url, r)}{extra}"
    block("Added to Your authored tests:", added, tail)
    block("Possible requests. Answer Yes or No on the line in the list:", pending, tail)
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
        dedupe_key TEXT, note TEXT)""")
    cx.commit()
    for col, typ in _LATER_COLUMNS[FLAGS_TABLE]:
        add_column(cx, FLAGS_TABLE, col, typ)
    try:
        cx.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_biofield_request_flags_dedupe "
                   "ON biofield_request_flags(dedupe_key)")
        cx.commit()
    except Exception as e:  # noqa: BLE001 - two workers racing; the other made it
        cx.rollback()
        print(f"[biofield-requests] dedupe index not created here: {type(e).__name__}",
              flush=True)


def add_flag(cx, email, name, verdict, message, person=None, note="", now=None):
    """Store one flagged portal message for one person, once per client, message,
    person and 10 minutes. `person` is {name, email} when the request is for someone
    other than the sender (one row each). Returns (id, created). Portable: RETURNING
    on Postgres, never lastrowid."""
    from dashboard import db as _db, dbwrite
    from dashboard.portal_triage import dedupe_key, message_key
    init_flags_table(cx)
    e = (email or "").strip().lower()
    now_dt = now or _dt.datetime.now(_dt.timezone.utc)
    person = person or {}
    dkey = dedupe_key(e, message_key(message), now_dt, 10)
    if person:
        dkey += "|" + _norm_name(person.get("name")) + "|" + (person.get("email") or "")
    row = cx.execute("SELECT id FROM biofield_request_flags WHERE dedupe_key=?",
                     (dkey,)).fetchone()
    if row:
        return row[0], False
    try:
        new_id = dbwrite.insert_returning_id(
            cx,
            "INSERT INTO biofield_request_flags(email, name, certainty, quote, for_name, "
            "for_email, message, created_at, dedupe_key, note) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (e, (name or "").strip(), verdict["certainty"], one_line(verdict.get("quote")),
             person.get("name") or "", (person.get("email") or "").strip().lower(),
             str(message or "")[:4000], now_dt.strftime("%Y-%m-%dT%H:%M:%SZ"), dkey,
             note or None))
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
            "created_at", "note")
    rows = cx.execute(f"SELECT {', '.join(cols)} FROM biofield_request_flags "
                      "WHERE created_at>=? ORDER BY id LIMIT ?",
                      (since, int(limit))).fetchall()
    return [dict(zip(cols, r)) for r in rows]


def flag_to_request(flag):
    """A prod flag as a record() request (source portal, source_ref the flag id)."""
    people = ([{"name": flag.get("for_name") or "", "email": flag.get("for_email") or ""}]
              if (flag.get("for_name") or flag.get("for_email")) else [])
    name, email, payer = requests_for({"people": people}, flag.get("name"),
                                      flag.get("email"))[0]
    return {"source": "portal", "source_ref": str(flag["id"]), "email": email,
            "name": name, "payer_email": payer, "requested_at": flag.get("created_at"),
            "quote": flag.get("quote"), "certainty": flag.get("certainty"),
            "note": flag.get("note") or ""}


ANALYSIS_SLUG = "biofield-analysis"
# A card checkout (app.py _price_biofield) stores the line by this name with no slug.
# Mirrors app.py _BIOFIELD_ITEM_NAME; a test keeps the two equal.
ANALYSIS_NAME = "Causal Biofield Analysis"
_NAMED_ITEMS = 5


def _text(v):
    return v.strip() if isinstance(v, str) else ""


def is_analysis_item(item):
    """The rule of app.py _is_biofield_line: the slug when there is one, else the name."""
    slug = _text(item.get("slug"))
    if slug:
        return slug == ANALYSIS_SLUG
    return _text(item.get("name")) == ANALYSIS_NAME


def _utc_z(ts):
    """An ISO timestamp as UTC "YYYY-MM-DDTHH:MM:SSZ", or None when it does not parse."""
    try:
        t = _dt.datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if t.tzinfo is None:
        t = t.replace(tzinfo=_dt.timezone.utc)
    return t.astimezone(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _money(cents):
    return "$%d" % (cents // 100) if cents % 100 == 0 else "$%.2f" % (cents / 100)


def order_to_request(order):
    """A prod order (GET /api/orders) as a record() request, or None when it is not a
    paid Biofield Analysis order. Source "order", source_ref the order id.

    Paid means pay_status "paid" with a paid_at. A superseded order is skipped: its
    replacement carries the request. Certainty is "clear" when the analysis is the only
    item and "possible" when anything else is on the order: that is usually the remedies
    from an analysis already done, so it waits for Glen's yes. The quote is a summary
    this code writes, never the client's words. A refund later changes nothing here."""
    if (_text(order.get("pay_status"))).lower() != "paid" or order.get("superseded_by_order_id"):
        return None
    paid_at = _utc_z(order.get("paid_at"))
    items = [i for i in (order.get("items") or []) if isinstance(i, dict)]
    if not paid_at or not any(is_analysis_item(i) for i in items):
        return None
    others = [i for i in items if not is_analysis_item(i)]
    try:
        total = "order total %s" % _money(int(order.get("total_cents") or 0))
    except (TypeError, ValueError):
        total = "order total unknown"
    note = ""
    if others:
        n = len(others)
        quote = (f"Paid order {order['id']}, {total}: Biofield Analysis with {n} other "
                 f"item{'' if n == 1 else 's'}")
        named = [one_line(_text(i.get("name")) or _text(i.get("slug")) or "unnamed", 60)
                 for i in others[:_NAMED_ITEMS]]
        more = f" and {n - _NAMED_ITEMS} more" if n > _NAMED_ITEMS else ""
        note = ("billed with " + ", ".join(named) + more
                + ", so the analysis may already be done")
    else:
        quote = f"Paid order {order['id']}, {total}: Biofield Analysis only"
    return {"source": "order", "source_ref": str(order["id"]),
            "email": _text(order.get("email")).lower(),
            "name": one_line(_text(order.get("name")), 80), "payer_email": "",
            "requested_at": paid_at, "quote": quote,
            "certainty": "possible" if others else "clear", "note": note}
