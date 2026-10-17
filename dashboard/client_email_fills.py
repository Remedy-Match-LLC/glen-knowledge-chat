"""Emails found for FileMaker clients that have none (Glen, 2026-10-16).

A client's FileMaker row had no email, so every email-keyed lookup missed her three
legacy invoices. Glen: "Add other clients' emails always when missing and when possible."

The legacy fmp_* tables are replaced whole on every ingest, so a found email lives here
instead, keyed by the FileMaker client id, and the order lookups read it beside
fmp_clients.email.

A fill is made only when it is unambiguous (Glen approved the rule 2026-10-16):
  - exactly one FileMaker client carries that first and last name (a surname shared with
    family and pet records is never enough);
  - every source that knows the name gives the same single email;
  - that email is not already on another FileMaker client, or filled for one.
Anything else is reported, never guessed.
"""
import re
import unicodedata
from collections import defaultdict
from datetime import datetime, timezone

SOURCES = ("e4l", "people", "gmail")


def ensure_table(cx):
    cx.execute("""CREATE TABLE IF NOT EXISTS client_email_fills (
        fmp_client_id TEXT PRIMARY KEY,
        email         TEXT NOT NULL,
        source        TEXT NOT NULL,
        created_at    TEXT NOT NULL)""")
    # One email, one client: a second worker's insert is dropped (review round 2).
    cx.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_client_email_fills_email "
               "ON client_email_fills(lower(email))")


# Degrees are dropped. Generations stay: "Smith Sr" and "Smith" may be father and son
# (review round 3). A title makes a name unusable: "Mrs John Smith" is his wife.
_DEGREES = {"md", "phd", "nd", "od", "dc", "dds"}
_TITLES = {"dr", "mr", "mrs", "ms", "miss", "mx", "prof", "rev"}
_AFFIXES = _DEGREES


def _part(s):
    """One name part, normalised whole: accents dropped, hyphens and apostrophes joined
    ("Smith-Jones" is "smithjones", never "jones"), affixes removed."""
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    s = re.sub(r"[-'’.]", "", s)
    words = [w for w in re.sub(r"[^a-z]+", " ", s).split() if w not in _AFFIXES]
    return " ".join(words)


def _first_names(raw):
    """Each first name a FileMaker row gives. "Josephine\nJosie" is two. Only a
    one-word line counts as an alternative, so "Ann Lee\nBob" builds no new name."""
    lines = [_part(x) for x in re.split(r"[\n/;]+", raw or "")]
    lines = [x for x in lines if x]
    if len(lines) <= 1:
        return lines
    return [x for x in lines if len(x.split()) == 1]


def _has_title(*parts):
    words = set()
    for p in parts:
        s = unicodedata.normalize("NFKD", p or "").encode("ascii", "ignore").decode().lower()
        words |= set(re.sub(r"[^a-z]+", " ", s).split())
    return bool(words & _TITLES)


def name_keys(first, last):
    """(whole first name, whole last name) keys for one person; empty when either half
    is missing or carries a title. Glen's rule is the exact first and last name, so a
    middle name, an initial or half a double surname never matches."""
    if _has_title(first, last):
        return set()
    ln = _part(last)
    if not ln:
        return set()
    return {(f, ln) for f in _first_names(first)}


def full_name_key(name):
    """A one-field name ("Josie Doe") is used only when it is exactly two words, so
    "Mary Beth Smith" never matches Mary Smith (review round 1)."""
    if _has_title(name):
        return None
    words = _part(name).split()
    return (words[0], words[1]) if len(words) == 2 else None


def _valid_email(e):
    e = (e or "").strip().lower()
    return e if re.fullmatch(r"[^@\s]+@[^@\s]+\.[a-z]{2,}", e) else ""


def _emails_in(raw):
    """Every address in a FileMaker email field, however it was typed."""
    return set(re.findall(r"[^@\s;,<>]+@[^@\s;,<>]+\.[a-z]{2,}", (raw or "").lower()))


def plan(fmp_clients, candidates, filled=None):
    """Work out the fills. Pure: no database.

    fmp_clients: [(id, name_first, name_last, email)]
    candidates:  [{"source", "key": (first, last), "email"}]
    filled:      {fmp_client_id: email} already on file here
    Returns {"fills", "ambiguous", "taken", "conflicts", "counts"}. A conflict is an
    earlier fill that a source now contradicts: it is reported for Glen, never changed."""
    filled = {str(k): v.lower() for k, v in (filled or {}).items()}
    taken_emails = set()
    for c in fmp_clients:
        taken_emails |= _emails_in(c[3])
    taken_emails |= set(filled.values())
    by_key = defaultdict(set)
    for cid, first, last, _em in fmp_clients:
        for k in name_keys(first, last):
            by_key[k].add(str(cid))
    src_emails = defaultdict(lambda: defaultdict(set))   # key -> source -> {emails}
    for c in candidates or []:
        e = _valid_email(c.get("email"))
        k = tuple(c.get("key") or ())
        if e and len(k) == 2 and c.get("source") in SOURCES:
            src_emails[k][c["source"]].add(e)

    fills, ambiguous, taken, conflicts = [], [], [], []
    claimed = defaultdict(list)
    for cid, first, last, em in fmp_clients:
        cid = str(cid)
        if (em or "").strip(" "):
            continue        # any value counts as on file, even one typed badly; strip(" ")
            #               matches the SQL trim() the lookup uses (review round 3)
        keys = name_keys(first, last)
        found = {}                                       # source -> {emails}
        shared = False
        for k in keys:
            if len(by_key[k]) > 1:
                shared = True
            for s, es in src_emails.get(k, {}).items():
                found.setdefault(s, set()).update(es)
        if cid in filled:
            others = set().union(*found.values()) - {filled[cid]} if found else set()
            if others or shared:
                conflicts.append({"fmp_client_id": cid,
                                  "reason": "a source now gives a different email" if others
                                  else "the name is now shared by another FileMaker client"})
            continue
        if not found:
            continue
        emails = set().union(*found.values())
        if shared or len(emails) != 1:
            ambiguous.append({"fmp_client_id": cid,
                              "reason": "name shared by another FileMaker client" if shared
                              else "sources give different emails"})
            continue
        email = next(iter(emails))
        if email in taken_emails:
            taken.append({"fmp_client_id": cid, "reason": "email already on another client"})
            continue
        source = next(s for s in SOURCES if s in found)
        claimed[email].append(cid)
        fills.append({"fmp_client_id": cid, "email": email, "source": source})
    # One email found for two clients is ambiguous for both.
    dup = {e for e, ids in claimed.items() if len(ids) > 1}
    for f in [f for f in fills if f["email"] in dup]:
        fills.remove(f)
        ambiguous.append({"fmp_client_id": f["fmp_client_id"],
                          "reason": "the same email was found for two clients"})
    counts = {"missing": sum(1 for c in fmp_clients
                             if not (c[3] or "").strip(" ") and str(c[0]) not in filled),
              "fills": len(fills), "ambiguous": len(ambiguous), "taken": len(taken),
              "conflicts": len(conflicts)}
    for s in SOURCES:
        counts["from_" + s] = sum(1 for f in fills if f["source"] == s)
    return {"fills": fills, "ambiguous": ambiguous, "taken": taken,
            "conflicts": conflicts, "counts": counts}


def filled_map(cx):
    ensure_table(cx)
    return {str(r[0]): r[1] for r in
            cx.execute("SELECT fmp_client_id, email FROM client_email_fills").fetchall()}


def apply(cx, fills):
    """Write the planned fills. A client already filled, or an email already filled for
    another client, is skipped by the database itself, so two runs at once cannot both
    write (review round 2)."""
    ensure_table(cx)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    n = 0
    for f in fills:
        cur = cx.execute("INSERT INTO client_email_fills(fmp_client_id, email, source, "
                         "created_at) VALUES(?,?,?,?) ON CONFLICT DO NOTHING",
                         (f["fmp_client_id"], f["email"].lower(), f["source"], now))
        n += max(0, getattr(cur, "rowcount", 0) or 0)
    cx.commit()
    return n


def people_candidates(cx):
    """Candidates from the people table (GHL-synced contacts and portal sign-ups)."""
    out = []
    cols = [d[0] for d in cx.execute("SELECT * FROM people LIMIT 0").description]
    if "first_name" in cols and "last_name" in cols:
        sql = "SELECT email, first_name, last_name, name FROM people"
    else:   # an older people table carries only the one-field name (review round 3)
        sql = "SELECT email, '', '', name FROM people"
    for email, first, last, name in cx.execute(sql).fetchall():
        keys = name_keys(first, last) if (first and last) else set()
        if (first or last) and not keys:
            continue                      # half a name: never fall back to the whole
        if not keys and name:
            k = full_name_key(name)
            keys = {k} if k else set()
        for k in keys:
            out.append({"source": "people", "key": k, "email": email})
    return out
