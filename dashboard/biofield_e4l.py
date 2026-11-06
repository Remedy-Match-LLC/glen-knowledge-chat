"""Pull a client's most recent E4L voice scan into the local Biofield Analysis tool.

E4L scans live in a SEPARATE database, ~/AI-Training/e4l.db (kept fresh by the
e4l-daily-watch cron) — not the chat_log.db the Biofield app otherwise uses. As soon
as the client is identified, `scan_context()` reads that DB read-only and reports:
freshness (within a 2-week window), how many days ago the scan was, and the scan's
ranked findings. It NEVER raises — a missing DB, blank email, unknown client, or no
scan all return status "none" so the intake flow is never blocked.

The query (latest-scan + identity-merge handling + ranked findings) is vendored from
the vault tool `02 Skills/e4l_synthesis.py`, which is not importable by this app.
"""
import datetime
from contextlib import closing
import os
import re
import subprocess
import sqlite3
from dashboard import db


def _db_path(db_path=None):
    """Resolve the e4l.db location: an explicit arg, then the E4L_DB env, then a
    synced copy on the prod persistent disk ($DATA_DIR/e4l.db — written by the
    owner db-sync push), then the local ~/AI-Training default."""
    if db_path:
        return db_path
    env = os.environ.get("E4L_DB")
    if env:
        return os.path.expanduser(env)
    d = os.environ.get("DATA_DIR")
    if d:
        p = os.path.join(d, "e4l.db")
        if os.path.exists(p):
            return p
    return os.path.expanduser("~/AI-Training/e4l.db")


def _connect_ro(path):
    """Open the e4l DB read-only. Returns None if the file is missing/unopenable
    (so we never silently create an empty e4l.db beside the app)."""
    if not os.path.exists(path):
        return None
    try:
        cx = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        cx.row_factory = sqlite3.Row
        return cx
    except db.Error:
        return None


def _merge_group(cx, client_id):
    """All client_ids sharing this client's confirmed identity (inclusive), so a
    person's split duplicate accounts read as one history. {client_id} when the
    merges table is absent or untouched."""
    try:
        rows = cx.execute("SELECT dup_client_id, canonical_client_id "
                          "FROM e4l_identity_merges").fetchall()
    except db.Error:
        return {client_id}
    canon = {int(d): int(c) for d, c in rows}
    c = canon.get(client_id, client_id)
    group = {client_id, c}
    for dup, can in canon.items():
        if can == c:
            group.add(dup)
    return group


def _latest_scan(cx, email, client_id=None):
    """{scan_id, scan_date} for the most recent scan across the client's merged
    identity, or None. With client_id, that one client's identity only: an owner and
    her pet can share an email, and the email reads both of them (Skylar Verches,
    2026-11-06)."""
    if client_id is not None:
        # One id, or several: the same-name rows on the subject's own email, which an
        # unmerged duplicate leaves apart (resolve_intake_subject "scan_ids").
        ids = ([client_id] if isinstance(client_id, (int, str))
               else list(client_id))
        group = set()
        for one in ids:
            group |= _merge_group(cx, int(one))
        if not group:
            return None
    else:
        crows = cx.execute("SELECT client_id FROM e4l_clients WHERE lower(email)=lower(?)",
                           (str(email or "").strip(),)).fetchall()
        if not crows:
            return None
        group = set()
        for cr in crows:
            group |= _merge_group(cx, cr["client_id"])
    ph = ",".join("?" for _ in group)
    r = cx.execute(f"""SELECT s.scan_id, s.scan_date FROM e4l_scans s
                       WHERE s.client_id IN ({ph})
                       ORDER BY s.scan_date DESC, s.scan_id DESC LIMIT 1""",
                   tuple(group)).fetchone()
    return dict(r) if r else None


# Info-only "stress" categories (Glen, 2026-06-24): ER (Energetic Rejuvenators) and
# MR (MR1..MR10 — Super Cell Driver, Calm Mind, …). Good information, but Glen has no
# test vials to balance them, so they're listed separately from the infoceuticals he
# DOES balance (ED/EI/ET/ES/MB/BFA). In real scans, recommendation findings only ever
# carry these categories, so the split is clean.
_STRESS_CATEGORIES = ("ER", "MR")


def _group_for(category):
    return "stress" if (category or "").strip().upper() in _STRESS_CATEGORIES else "infoceutical"


def _findings(cx, scan_id):
    """All ranked findings for a scan: {rank, code, name, description, category, group}
    by priority. `group` = 'stress' (ER/MR, info only) or 'infoceutical' (balanceable)."""
    rows = cx.execute(
        """SELECT r.item_code, r.priority_rank, i.name, i.full_name, i.e4l_description, i.category
           FROM e4l_scan_results r LEFT JOIN e4l_items i ON i.code = r.item_code
           WHERE r.scan_id=? ORDER BY (r.priority_rank IS NULL), r.priority_rank ASC, r.id ASC""",
        (scan_id,)).fetchall()
    out = []
    for r in rows:
        code = (r["item_code"] or "").strip()
        if not code:
            continue
        cat = (r["category"] or "").strip()
        out.append({"rank": r["priority_rank"], "code": code,
                    "name": (r["full_name"] or r["name"] or code).strip(),
                    "description": (r["e4l_description"] or "").strip(),
                    "category": cat, "group": _group_for(cat)})
    hrefs = _pattern_hrefs(cx, [f["code"] for f in out])
    for f in out:
        f["pattern_href"] = hrefs.get(f["code"], "")
    return out


def _pattern_hrefs(cx, codes):
    """{code: '/learn/pattern/<slug>' or ''} — link only patterns that have a
    glossary page (a description or >=1 mapped structure). Best-effort; any error
    on a code leaves it unlinked."""
    from dashboard import pattern_glossary as _pg
    out = {}
    for code in codes:
        try:
            slug = _pg.slug_for(code)
            out[code] = ("/learn/pattern/" + slug) if _pg.page_exists(cx, slug) else ""
        except Exception:
            out[code] = ""
    return out


def emotions_for_codes(codes, *, db_path=None):
    """{code: [emotion, ...]} for the codes that carry stype='emotion' structure
    rows in e4l_pattern_structures, ordered by is_primary then structure. Read-only.
    Returns {} on blank input, missing DB, or any sqlite error. Never raises."""
    codes = [str(c).strip() for c in (codes or []) if str(c).strip()]
    if not codes:
        return {}
    cx = _connect_ro(_db_path(db_path))
    if cx is None:
        return {}
    out = {}
    try:
        ph = ",".join("?" for _ in codes)
        rows = cx.execute(
            f"SELECT code, structure FROM e4l_pattern_structures "
            f"WHERE stype='emotion' AND code IN ({ph}) "
            f"ORDER BY is_primary DESC, structure ASC", tuple(codes)).fetchall()
        for r in rows:
            out.setdefault(r["code"], []).append(r["structure"])
    except db.Error:
        return {}
    finally:
        cx.close()
    return out


def organs_for_codes(codes, *, db_path=None):
    """{code: [structure, ...]} for the codes' stype IN ('organ','system') rows in
    e4l_pattern_structures, primary first. These are the anatomical tags used to
    light Body Map zones for a client's findings. Read-only. Returns {} on blank
    input, missing DB, or any sqlite error. Never raises."""
    codes = [str(c).strip() for c in (codes or []) if str(c).strip()]
    if not codes:
        return {}
    cx = _connect_ro(_db_path(db_path))
    if cx is None:
        return {}
    out = {}
    try:
        ph = ",".join("?" for _ in codes)
        rows = cx.execute(
            f"SELECT code, structure FROM e4l_pattern_structures "
            f"WHERE stype IN ('organ','system') AND code IN ({ph}) "
            f"ORDER BY is_primary DESC, structure ASC", tuple(codes)).fetchall()
        for r in rows:
            out.setdefault(r["code"], []).append(r["structure"])
    except db.Error:
        return {}
    finally:
        cx.close()
    return out


def _days_ago(scan_date, today):
    """Whole days from scan_date to today (both YYYY-MM-DD). Clamped >= 0 so a future
    scan_date (data glitch) never reads as negative. None if either is unparseable."""
    try:
        s = datetime.date.fromisoformat((scan_date or "").strip())
        t = datetime.date.fromisoformat((today or "").strip())
    except ValueError:
        return None
    return max(0, (t - s).days)


def _none(window_days):
    return {"status": "none", "found": False, "scan_id": None, "scan_date": None,
            "days_ago": None, "fresh": False, "window_days": window_days,
            "findings": [], "infoceuticals": [], "stresses": [],
            "message": "No E4L scan on file"}


def scan_context(email, today, *, db_path=None, window_days=14, limit=12, client_id=None):
    """Most recent E4L scan for `email` as of `today` (YYYY-MM-DD). Returns a dict:
      status: "fresh" | "stale" | "none"
      found, scan_id, scan_date, days_ago, fresh, window_days, findings, message
    Fresh = a scan exists within `window_days`. Never raises."""
    none = _none(window_days)
    if not (email or "").strip() and client_id is None:
        return none
    cx = _connect_ro(_db_path(db_path))
    if cx is None:
        return none
    try:
        scan = _latest_scan(cx, email, client_id)
        if not scan:
            return none
        days = _days_ago(scan["scan_date"], today)
        all_findings = _findings(cx, scan["scan_id"])
    except db.Error:
        return none
    finally:
        cx.close()
    # Two lists, each capped at `limit`: infoceuticals Glen can balance vs. ER/MR
    # "stresses" (info only). Rank order preserved within each.
    infoceuticals = [f for f in all_findings if f["group"] == "infoceutical"]
    stresses = [f for f in all_findings if f["group"] == "stress"]
    if limit:
        infoceuticals, stresses = infoceuticals[:limit], stresses[:limit]
    findings = infoceuticals + stresses
    fresh = days is not None and days <= window_days
    status = "fresh" if fresh else "stale"
    if fresh:
        message = f"Recent E4L scan · {days} day{'s' if days != 1 else ''} ago"
    elif days is None:
        message = "No fresh voice scan — last scan date unreadable"
    else:
        message = (f"No fresh voice scan — last scan {days} day"
                   f"{'s' if days != 1 else ''} ago (stale)")
    return {"status": status, "found": True, "scan_id": scan["scan_id"],
            "scan_date": scan["scan_date"], "days_ago": days, "fresh": fresh,
            "window_days": window_days, "findings": findings,
            "infoceuticals": infoceuticals, "stresses": stresses, "message": message}


def findings_for_scan_date(email, scan_date, *, db_path=None, limit=12):
    """Findings for the client's scan on a SPECIFIC scan_date (within the merged
    identity), same shape and per-group caps as scan_context()['findings'].
    Returns [] when email/scan_date is blank, the DB is missing, or no scan exists
    on that date. Never raises. scan_context() always resolves the LATEST scan
    (its `today` arg only affects the freshness message); this targets the exact
    date, which is what backfilling a historical report row requires."""
    if not (email or "").strip() or not (scan_date or "").strip():
        return []
    cx = _connect_ro(_db_path(db_path))
    if cx is None:
        return []
    try:
        crows = cx.execute("SELECT client_id FROM e4l_clients WHERE lower(email)=lower(?)",
                           (str(email).strip(),)).fetchall()
        if not crows:
            return []
        group = set()
        for cr in crows:
            group |= _merge_group(cx, cr["client_id"])
        ph = ",".join("?" for _ in group)
        row = cx.execute(
            f"SELECT scan_id FROM e4l_scans WHERE client_id IN ({ph}) AND scan_date=? "
            f"ORDER BY scan_id DESC LIMIT 1", (*group, str(scan_date).strip())).fetchone()
        if not row:
            return []
        all_findings = _findings(cx, row["scan_id"])
    except db.Error:
        return []
    finally:
        cx.close()
    infoceuticals = [f for f in all_findings if f["group"] == "infoceutical"]
    stresses = [f for f in all_findings if f["group"] == "stress"]
    if limit:
        infoceuticals, stresses = infoceuticals[:limit], stresses[:limit]
    return infoceuticals + stresses


# --- Client name picker -----------------------------------------------------

def search_clients(q, *, db_path=None, limit=20):
    """Match e4l_clients by name (or email substring) for the intake autocomplete.
    Groups each NAME's distinct emails so a same-name/different-email client is
    pickable. Returns [] for a blank query or missing DB (never raises):
      [{"name": str, "emails": [{"email", "client_id", "last_scan_date"}, ...]}]
    Name-groups are capped at `limit` (ordered by name)."""
    q = (q or "").strip()
    if not q:
        return []
    cx = _connect_ro(_db_path(db_path))
    if cx is None:
        return []
    like = f"%{q}%"
    try:
        rows = cx.execute(
            """SELECT c.client_id, c.name, c.email,
                      (SELECT MAX(s.scan_date) FROM e4l_scans s
                         WHERE s.client_id = c.client_id) AS last_scan_date
               FROM e4l_clients c
               WHERE c.name LIKE ? COLLATE NOCASE OR c.email LIKE ? COLLATE NOCASE
               ORDER BY c.name""", (like, like)).fetchall()
    except db.Error:
        return []
    finally:
        cx.close()
    groups, order = {}, []
    for r in rows:
        name = (r["name"] or "").strip() or "(unnamed)"
        email = (r["email"] or "").strip()
        if name not in groups:
            if len(order) >= limit:
                continue
            groups[name] = []
            order.append(name)
        if email and not any(e["email"] == email for e in groups[name]):
            groups[name].append({"email": email, "client_id": r["client_id"],
                                 "last_scan_date": r["last_scan_date"]})
    return [{"name": n, "emails": groups[n]} for n in order]


# --- On-demand live E4L fetch ----------------------------------------------

def _vault_dir():
    return os.path.expanduser(os.environ.get("E4L_VAULT", "~/AI-Training"))


def _default_fetch_runner(client_id=None, name=None):
    """Pull this one client's NEW scans from the LIVE E4L portal, then parse them
    into e4l.db — the same two vault steps the daily cron runs, minus the Pinecone
    vectorize (not needed for the biofield panel). Inherits the process env, so
    E4L_USERNAME/E4L_PASSWORD must already be present (the app runs under `doppler
    run`). Raises on a non-zero exit (fetch_live converts that to {"ok": False})."""
    vault = _vault_dir()
    scraper = os.path.join(vault, "02 Skills", "scrape-e4l-http.py")
    parser = os.path.join(vault, "02 Skills", "parse-e4l-scans.py")
    sel = (["--client", str(client_id)] if client_id is not None
           else ["--client-name", str(name)])
    scraped = subprocess.run(["python3", scraper, *sel], check=True, capture_output=True,
                             text=True, timeout=180)
    subprocess.run(["python3", parser], check=True, capture_output=True,
                   text=True, timeout=120)
    return {"ok": True, **scrape_counts(scraped.stdout)}


def scrape_counts(stdout):
    """What the scraper reported: scans the portal listed as new, and PDFs that failed
    to download. It exits 0 either way, so this is the only record of either."""
    text = stdout or ""
    return {"new_scans": sum(int(n) for n in re.findall(r"(\d+) new scan\(s\)", text)),
            "download_errors": len(re.findall(r"download error scan", text))}


def fetch_live(client_id=None, name=None, *, runner=None):
    """Trigger an on-demand live fetch+parse for one client. `runner` is injectable
    so tests never hit the portal. Never raises — a scraper/login failure comes back
    as {"ok": False, "error": ...}."""
    if client_id is None and not (name or "").strip():
        return {"ok": False, "error": "no client identifier (client_id or name)"}
    runner = runner or _default_fetch_runner
    try:
        out = runner(client_id=client_id, name=name) or {}
        return {"ok": bool(out.get("ok", True)), **{k: v for k, v in out.items() if k != "ok"}}
    except subprocess.CalledProcessError as e:
        return {"ok": False, "error": (e.stderr or str(e))[:300]}
    except Exception as e:
        return {"ok": False, "error": str(e)[:300]}


def species_from_e4l(e4l_db, email):
    """Species for `email` straight from e4l.db, or None.

    The local Biofield Intake reads species HERE, not from dashboard.client_species.
    That table is the prod mirror; on Glen's Mac chat_log.db has no such table, so a
    lookup there threw, was caught, and every animal read as a person (Sasha
    Takahashi, a cat, 2026-09-21). None means unknown. The Biofield Intake then asks
    Glen before proposing remedies; the automatic reveal paths still read unknown as a
    person (Glen, 2026-10-01: "automatic reveals keep human as default").

    Lives in this module, not client_species.py, because this is the file the
    raw-connect guard (tests/test_no_raw_logdb_connect.py) already allows for e4l.db.
    client_species.py writes the production database, so allow-listing it would let a
    raw chat_log connect added there later pass unnoticed.

    When the E4L scrape has no species, Glen's own answer (species_answers, written by
    record_species_answer) is used. Glen, 2026-10-01: "if species is blank, ask me
    first". None now means nobody knows, and the Intake asks him before proposing
    remedies. Skylar, a dog with a blank scrape, was given FFs that day.

    An answer is read here only when the email holds at most one E4L client: that
    client's own answer, or an old email-only answer. None of them is a person with no
    E4L record, whose old answer still holds. On an email an owner shares with
    her pet, an answer given for one of them must not answer for the other
    (2026-11-06). resolve_intake_subject reads the per-subject answers."""
    e = (email or "").strip().lower()
    if not e or not e4l_db:
        return None
    try:
        with closing(sqlite3.connect(f"file:{e4l_db}?mode=ro", uri=True)) as ecx:
            row = ecx.execute(
                "SELECT species FROM e4l_clients "
                "WHERE lower(trim(email))=? AND species IS NOT NULL AND species<>'' "
                "ORDER BY client_id DESC LIMIT 1", (e,)).fetchone()
            if row:
                return (row[0] or "").strip() or None
            ids = [r[0] for r in ecx.execute(
                "SELECT client_id FROM e4l_clients WHERE lower(trim(email))=?", (e,))]
            if len(ids) > 1:
                return None
            return _answer_for_clients(ecx, ids) or _answer_legacy(ecx, e)
    except sqlite3.Error:
        return None


def _person_key(name):
    return " ".join("".join(ch for ch in (name or "").lower()
                            if ch.isalnum() or ch == " ").split())


# --- Glen's species answers, one per subject (2026-11-06) ----------------------------
# species_answers began keyed by email alone (email TEXT PRIMARY KEY), so an answer for
# a pet leaked to its owner on the same email. Migrated additively: three nullable
# columns. A new row keeps the primary key unique by holding its subject key in the
# email column ("cid:<id>" or "subject:<email>|<name>"); an old row holds a real email.

_ANSWER_COLS = (("client_id", "INTEGER"), ("subject_email", "TEXT"), ("name_key", "TEXT"))


def _answer_cols(ecx):
    try:
        return {r[1] for r in ecx.execute("PRAGMA table_info(species_answers)")}
    except sqlite3.Error:
        return set()


def _one_answer(ecx, where, args):
    try:
        row = ecx.execute(f"SELECT species FROM species_answers WHERE {where} "
                          "AND species IS NOT NULL AND species<>'' "
                          "ORDER BY answered_at DESC LIMIT 1", args).fetchone()
    except sqlite3.Error:
        return None
    return ((row[0] if row else "") or "").strip() or None


def _answer_for_clients(ecx, ids):
    ids = [int(i) for i in ids or []]
    if not ids or "client_id" not in _answer_cols(ecx):
        return None
    return _one_answer(ecx, f"client_id IN ({','.join('?' for _ in ids)})", tuple(ids))


def _answer_for_subject(ecx, email, want):
    if not email or "name_key" not in _answer_cols(ecx):
        return None
    return _one_answer(ecx, "client_id IS NULL AND subject_email=? AND name_key=?",
                       (email, want or ""))


def _answer_legacy(ecx, email):
    cols = _answer_cols(ecx)
    if not cols or not email:
        return None
    if "client_id" in cols:
        return _one_answer(ecx, "email=? AND client_id IS NULL AND name_key IS NULL",
                           (email,))
    return _one_answer(ecx, "email=?", (email,))


# --- whose intake is it --------------------------------------------------------------

_COLS = "client_id, email, species, animal_name, name"


# --- whose name is it ------------------------------------------------------------------
# E4L stores a pet with animal_name set, and its `name` is often the OWNER's name
# (2026-11-06 review, round 3). So a record with an animal_name is named by it; one
# without is named by `name`. Every match below reads names this way.

def _is_animal_species(sp):
    return (sp or "").strip().lower() not in ("", "human", "person")


def subject_name(r):
    """The name an E4L record's subject goes by: its animal_name, else its name."""
    return (r[3] or "").strip() or (r[4] or "").strip()


def _subject_tokens(r):
    """The words of the subject's name. A pet adds its owner's surname, so "Skylar
    Verches" names Skylar, whose record says name "Agnes Verches"."""
    pet = _person_key(r[3])
    if not pet:
        return _person_key(r[4]).split()
    toks = pet.split()
    last = (_person_key(r[4]).split() or [""])[-1]
    return toks + ([last] if last and last not in toks else [])


def _subject_keys(r):
    """The full names an intake may give this record's subject."""
    pet = _person_key(r[3])
    if not pet:
        return {_person_key(r[4])} - {""}
    return {pet, " ".join(_subject_tokens(r))}


def _kind(r):
    """("animal", pet name) or ("human", name). Two rows of one kind and one name on an
    email are one subject split in two; a pet and its owner never are."""
    pet = _person_key(r[3])
    if pet:
        return ("animal", pet)
    return ("animal" if _is_animal_species(r[2]) else "human", _person_key(r[4]))


def _pack(r, by):
    return {"client_id": r[0], "email": (r[1] or "").strip().lower(),
            "species": (r[2] or "").strip() or None,
            "animal_name": (r[3] or "").strip(), "name": (r[4] or "").strip(),
            "subject_name": subject_name(r), "by": by}


def _same_kind_rows(ecx, rec):
    """Every row on the subject's own email of the same kind and name, rec included.

    A person's E4L record can be split in two before anyone merges it, with the scans
    on the older row, so both are read. Never a pet with its owner, never two names,
    and never a row whose species says it is someone else."""
    kind = _kind(rec)
    sp = (rec[2] or "").strip().lower()
    out = [rec]
    for r in ecx.execute(f"SELECT {_COLS} FROM e4l_clients WHERE lower(trim(email))=? "
                         "ORDER BY client_id DESC", ((rec[1] or "").strip().lower(),)):
        if int(r[0]) == int(rec[0]):
            continue
        other = (r[2] or "").strip().lower()
        if _kind(r) == kind and not (sp and other and other != sp):
            out.append(r)
    return out


def _same_name_ids(ecx, rec):
    return tuple(sorted(int(r[0]) for r in _same_kind_rows(ecx, rec)))


RESOLVED = ("id", "email+name", "email+first")


def resolve_intake_subject(e4l_db, client_id=None, email=None, name=None):
    """Who an intake is for, from one read of e4l.db. A dict:

      by        "id"          its stored E4L client id;
                "email+name"  the subject on its email with its name. A record with
                              an animal_name is named by it (its `name` is often the
                              owner's), optionally with the owner's surname;
                "email+first" no exact name, but the one subject on its email whose
                              name starts with the first name typed ("Sasha");
                "email"       no name on the intake: the newest client on the email
                              with a species, as before;
                "unresolved"  it has a name, its email holds E4L clients, and none of
                              them is this subject, or a pet and a person both answer
                              to it. Species unknown, no scans, no photo;
                "none"        e4l.db says nothing: unreadable, or a nameless intake on
                              an email with no species, or a named one on an email
                              E4L does not hold. Callers keep the old email reads.
      client    the E4L row ({client_id, email, species, animal_name, name,
                subject_name, by}) or None
      scan_ids  ids whose scans are this subject's (the three resolved kinds), else None
      species   the species, Glen's answer for this subject included, or None
      ambiguous True when a pet and a person on the email both answer to the name

    A namesake on ANOTHER email is never taken on its own: suggest_intake_client
    offers it and Glen confirms. Never raises."""
    e = (email or "").strip().lower()
    want = _person_key(name)
    cid = str(client_id or "").strip()
    out = {"by": "none", "client": None, "scan_ids": None, "species": None,
           "ambiguous": False}
    if not e4l_db or not os.path.exists(e4l_db):
        out["species"] = species_from_e4l(e4l_db, email)
        return out
    try:
        with closing(sqlite3.connect(f"file:{e4l_db}?mode=ro", uri=True)) as ecx:
            on_email = ecx.execute(
                f"SELECT {_COLS} FROM e4l_clients WHERE lower(trim(email))=? "
                "ORDER BY client_id DESC", (e,)).fetchall() if e else []
            rec, by = None, None
            if cid.isdigit():
                rec = ecx.execute(f"SELECT {_COLS} FROM e4l_clients WHERE client_id=?",
                                  (int(cid),)).fetchone()
                by = "id" if rec else None
            if rec is None and want:
                toks = want.split()
                exact = [r for r in on_email if want in _subject_keys(r)]
                # A first name typed alone ("Sasha" for "Sasha Takahashi"): the same
                # first name, and every word typed is in the subject's name. An
                # owner's surname alone ("Verches") is not her pet.
                first = [r for r in on_email
                         if (_subject_tokens(r) or [""])[0] == toks[0]
                         and set(toks) <= set(_subject_tokens(r))]
                for hits, kind in ((exact, "email+name"), (first, "email+first")):
                    if not hits:
                        continue
                    if len({_kind(h) for h in hits}) == 1:
                        # One subject, maybe split over rows: the one with a species.
                        rec = next((h for h in hits if (h[2] or "").strip()), hits[0])
                        by = kind
                    else:
                        out["ambiguous"] = True
                    break
            if rec is None and not want and e:
                rec = next((r for r in on_email if (r[2] or "").strip()), None)
                by = "email" if rec else None
            if rec is not None:
                out["by"], out["client"] = by, _pack(rec, by)
                if by == "email":
                    out["species"] = out["client"]["species"]
                    return out
                rows = _same_kind_rows(ecx, rec)
                out["scan_ids"] = tuple(sorted(int(r[0]) for r in rows))
                sp = next(((r[2] or "").strip() for r in rows if (r[2] or "").strip()),
                          None)
                if not sp:
                    # No species on the subject's own record. Only Glen's answer for
                    # THIS subject counts: by its client id, then by its own email and
                    # name. Never the intake's email, which may be the owner's.
                    own = (rec[1] or "").strip().lower()
                    sp = (_answer_for_clients(ecx, out["scan_ids"])
                          or _answer_for_subject(ecx, own, _person_key(subject_name(rec))))
                    if not sp and own and ecx.execute(
                            "SELECT COUNT(*) FROM e4l_clients WHERE lower(trim(email))=?",
                            (own,)).fetchone()[0] <= 1:
                        sp = _answer_legacy(ecx, own)    # its email holds it alone
                out["species"] = sp
                return out
            answer = _answer_for_subject(ecx, e, want)
            if want and on_email:
                out["by"], out["species"] = "unresolved", answer
                return out
    except sqlite3.Error:
        out = {"by": "none", "client": None, "scan_ids": None, "species": None,
               "ambiguous": False}
        answer = None
    out["species"] = answer or species_from_e4l(e4l_db, email)
    return out


def intake_e4l_client(e4l_db, client_id=None, email=None, name=None):
    """The E4L client an intake is for ({client_id, email, species, animal_name, name,
    subject_name, by}), or None when unresolved. See resolve_intake_subject."""
    return resolve_intake_subject(e4l_db, client_id, email, name)["client"]


def species_for_intake(e4l_db, client_id=None, email=None, name=None):
    """Species for an intake, keyed on the intake's own subject. None means nobody
    knows. The one species read for every Biofield Intake surface."""
    return resolve_intake_subject(e4l_db, client_id, email, name)["species"]


def suggest_intake_client(e4l_db, email=None, name=None):
    """The one E4L subject on ANOTHER email with exactly this name, or None.

    Only a suggestion: a namesake elsewhere can be an unrelated person or animal, so the
    intake page asks "Is this <name>, <species>, on another email?" and Glen confirms.
    A full name matches a subject's name; one word matches a pet's animal_name only
    ("Skylar"). The name scan reads the whole table, so it runs only for that
    question, never on a normal request."""
    e = (email or "").strip().lower()
    want = _person_key(name)
    if not want or not e4l_db or not os.path.exists(e4l_db):
        return None
    one_word = len(want.split()) < 2
    try:
        with closing(sqlite3.connect(f"file:{e4l_db}?mode=ro", uri=True)) as ecx:
            if e and any(want in _subject_keys(r) for r in ecx.execute(
                    f"SELECT {_COLS} FROM e4l_clients WHERE lower(trim(email))=?", (e,))):
                return None
            first = f"%{want.split()[0]}%"
            cands = ecx.execute(f"SELECT {_COLS} FROM e4l_clients WHERE name LIKE ? "
                                "COLLATE NOCASE OR animal_name LIKE ? COLLATE NOCASE",
                                (first, first)).fetchall()
    except sqlite3.Error:
        return None
    if one_word:
        named = [r for r in cands if _person_key(r[3]) == want]
    else:
        named = [r for r in cands if want in _subject_keys(r)]
    named = [r for r in named if (r[1] or "").strip().lower() != e]
    return _pack(named[0], "suggested") if len(named) == 1 else None


def record_species_answer(e4l_db, email, species, client_id=None, name=None):
    """Store Glen's answer for a subject whose E4L species is blank, so he is asked once.
    `species` is "Human" or the animal ("Dog", "Cat", "Horse", ...). Returns the stored
    value, or None when the input is unusable. Glen, 2026-10-01.

    Keyed per subject (2026-11-06): by `client_id` when the intake's E4L client is
    known, else by the email plus the intake's `name`. With neither, the old email-only
    row, which resolve_intake_subject applies only to an email holding one client."""
    e = (email or "").strip().lower()
    sp = (species or "").strip()
    cid = str(client_id or "").strip()
    if not sp or not e4l_db or not (e or cid.isdigit()):
        return None
    sp = sp[:1].upper() + sp[1:].lower()
    if cid.isdigit():
        row = (f"cid:{int(cid)}", int(cid), e or None, None)
    elif name is not None:
        nk = _person_key(name)
        row = (f"subject:{e}|{nk}", None, e, nk)
    else:
        row = (e, None, None, None)
    with closing(sqlite3.connect(e4l_db)) as ecx:
        ecx.execute("CREATE TABLE IF NOT EXISTS species_answers ("
                    "email TEXT PRIMARY KEY, species TEXT NOT NULL, answered_at TEXT NOT NULL)")
        have = _answer_cols(ecx)
        for col, typ in _ANSWER_COLS:
            if col not in have:
                ecx.execute(f"ALTER TABLE species_answers ADD COLUMN {col} {typ}")
        ecx.execute("INSERT INTO species_answers (email, species, answered_at, client_id, "
                    "subject_email, name_key) VALUES (?, ?, datetime('now'), ?, ?, ?) "
                    "ON CONFLICT(email) DO UPDATE SET species=excluded.species, "
                    "answered_at=excluded.answered_at, client_id=excluded.client_id, "
                    "subject_email=excluded.subject_email, name_key=excluded.name_key",
                    (row[0], sp, row[1], row[2], row[3]))
        ecx.commit()
    return sp
