"""Per-scan biofield reports: one evolving row per (email, scan_date).
Source of truth for the biofield analysis; client_portals.content_json is the
legacy fallback when a client has no rows here. See the 2026-06-17 spec."""
import datetime
import json
import re
import sqlite3

from dashboard import db as _db

# What made a report (Glen, 2026-09-10 and 2026-10-20). An AI match of the Functional
# Formulations to a scan is a "Remedy Match". "Biofield" is the paid manual analysis.
# Status cannot tell them apart: a paid hand-off also arrives as ai_draft.
REMEDY_MATCH = "remedy_match"
BIOFIELD = "biofield"
REPORT_KINDS = (REMEDY_MATCH, BIOFIELD)


def _now_iso() -> str:
    return datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z"


def init_table(cx) -> None:
    cx.execute("""
        CREATE TABLE IF NOT EXISTS portal_biofield_reports (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            email        TEXT,
            scan_date    TEXT,
            scan_id      TEXT,
            content_json TEXT,
            status       TEXT,
            created_at   TEXT,
            updated_at   TEXT,
            report_kind  TEXT,
            UNIQUE(email, scan_date)
        )
    """)
    cx.execute("CREATE INDEX IF NOT EXISTS ix_pbr_email ON portal_biofield_reports(email)")
    # Nullable on purpose: NULL means "not yet classified", and only NULL rows are
    # touched by backfill_report_kinds. Checked first so a Postgres read path never
    # takes the ALTER's table lock once the column exists.
    if not _db.column_exists(cx, "portal_biofield_reports", "report_kind"):
        try:
            cx.execute("ALTER TABLE portal_biofield_reports ADD COLUMN report_kind TEXT")
        except Exception:
            # On Postgres a failed ALTER (a lock timeout on first deploy) aborts the
            # transaction. Roll back so this request's later reads still work; the next
            # init_table call retries the add.
            # SQLite needs no rollback: a failed ALTER leaves its transaction usable,
            # and rolling back would discard a caller's pending writes.
            if _db.backend_of(cx) == "postgres":
                try:
                    cx.rollback()
                except Exception:
                    pass
    cx.commit()


def normalize_kind(kind):
    """'remedy_match' or 'biofield', or None for anything else. The animal writer's
    content key value 'animal_infoceuticals' is an AI match, so it is a Remedy Match."""
    k = (kind or "").strip().lower() if isinstance(kind, str) else ""
    if k == "animal_infoceuticals":
        return REMEDY_MATCH
    return k if k in REPORT_KINDS else None


_PAID_KEYS = ("legacy_source", "phase", "location", "schedule", "comped_intake", "client_id")
_AI_LAYER_KEYS = ("alternatives", "second_match", "ranked_second")


def infer_kind(content, scan_id=None):
    """Classify a report that has no stored kind, from the marks its writer left.

    1. Biofield: a legacy Healing Oasis import, or any paid-publish field (phase,
       location, schedule, comped_intake, client_id, a report PDF), or the paid
       hand-off greeting.
    2. Otherwise Remedy Match: scan patterns or ranked alternatives on a layer, the
       animal writer's marker, or a numeric E4L scan_id.
    3. Otherwise None: the content cannot say. Callers read None as Biofield, which
       is what every report showed before this column existed.
    See platform/plans/2026-10-20-remedy-match-label-scope.md, section 2."""
    c = content if isinstance(content, dict) else {}
    sid = str(scan_id or "").strip()
    if sid.startswith("legacy-fmp:"):
        return BIOFIELD
    if any(k in c for k in _PAID_KEYS):
        return BIOFIELD
    pdf = c.get("report_pdf")
    if isinstance(pdf, dict) and str(pdf.get("url") or "").strip():
        return BIOFIELD
    if "Here is your Biofield Analysis" in str(c.get("greeting") or ""):
        return BIOFIELD
    for L in (c.get("layers") or []):
        if not isinstance(L, dict):
            continue
        if isinstance(L.get("patterns"), list) and L.get("patterns"):
            return REMEDY_MATCH
        if any(k in L for k in _AI_LAYER_KEYS):
            return REMEDY_MATCH
    if c.get("report_kind") == "animal_infoceuticals":
        return REMEDY_MATCH
    if sid.isdigit():
        return REMEDY_MATCH
    return None


def effective_kind(stored, content, scan_id=None):
    """The kind a page or email should use: the stored one, else the rule, else
    Biofield (what an unclassifiable report has always shown)."""
    return normalize_kind(stored) or infer_kind(content, scan_id) or BIOFIELD


def upsert_report(cx, email, scan_date, scan_id, content, status, report_kind=None):
    """Write one report row. `report_kind` is set by the writers that know what made
    the report. An update that passes no kind keeps the stored one, so the console's
    confirm (which rebuilds content and drops the paid marks) never changes a row's
    kind. A row with no stored kind is classified from its OLD content before that
    content is replaced. A new row with no kind given is classified by infer_kind."""
    email = (email or "").strip().lower()
    now = _now_iso()
    kind = normalize_kind(report_kind)
    row = cx.execute(
        "SELECT id, report_kind, content_json, scan_id FROM portal_biofield_reports "
        "WHERE email=? AND scan_date=?", (email, scan_date)).fetchone()
    cj = json.dumps(content or {})
    if row:
        # The rule's answer for the content being replaced, used only if the row still
        # has no kind when the UPDATE runs.
        from_old = None
        if kind is None and normalize_kind(row[1]) is None:
            try:
                old = json.loads(row[2] or "{}")
            except Exception:
                old = {}
            from_old = infer_kind(old, row[3])
        # One statement, so the kind is chosen against the row as it is at write time:
        # an explicit kind wins, else the stored kind stands, else the old content's.
        # A kind another writer committed after the SELECT above is never overwritten
        # by a caller that passed none.
        cx.execute("UPDATE portal_biofield_reports SET scan_id=?, content_json=?, "
                   "status=?, updated_at=?, report_kind=COALESCE(CAST(? AS TEXT), report_kind, CAST(? AS TEXT)) "
                   "WHERE id=?",
                   (scan_id, cj, status, now, kind, from_old, row[0]))
    else:
        if kind is None:
            kind = infer_kind(content, scan_id)
        cx.execute("INSERT INTO portal_biofield_reports "
                   "(email, scan_date, scan_id, content_json, status, created_at, updated_at, "
                   "report_kind) VALUES (?,?,?,?,?,?,?,?)",
                   (email, scan_date, scan_id, cj, status, now, now, kind))
    cx.commit()


_COLS = "id, scan_date, scan_id, content_json, status, report_kind"


def _row_to_dict(row):
    try:
        content = json.loads(row[3] or "{}")
    except Exception:
        content = {}
    return {"scan_date": row[1], "scan_id": row[2], "content": content, "status": row[4],
            "report_kind": effective_kind(row[5], content, row[2]),
            "report_kind_stored": normalize_kind(row[5])}


def get_report(cx, email, scan_date):
    email = (email or "").strip().lower()
    row = cx.execute(f"SELECT {_COLS} "
                     "FROM portal_biofield_reports WHERE email=? AND scan_date=?",
                     (email, scan_date)).fetchone()
    return _row_to_dict(row) if row else None


def kinds_by_date(cx, email):
    """{scan_date: 'remedy_match' | 'biofield'} for every report row of one email."""
    email = (email or "").strip().lower()
    rows = cx.execute("SELECT scan_date, scan_id, content_json, report_kind "
                      "FROM portal_biofield_reports WHERE email=?", (email,)).fetchall()
    out = {}
    for sd, sid, cj, rk in rows:
        try:
            content = json.loads(cj or "{}")
        except Exception:
            content = {}
        out[sd] = effective_kind(rk, content, sid)
    return out


def has_biofield_report(cx, email):
    """True when this email has at least one paid Biofield report. A Remedy Match
    does not count: it must not tick the paid Biofield step or hide its order card."""
    init_table(cx)
    return BIOFIELD in kinds_by_date(cx, email).values()


def backfill_report_kinds(cx, *, dry_run=True):
    """Write the rule's answer into report_kind for every row that has none.

    Dry run unless dry_run=False. Only NULL rows are written, and only when the rule
    gives an answer; an unclassifiable row stays NULL (it reads as Biofield) and is
    listed for Glen by email and date. The write is compare-and-set on NULL, so a
    writer that set a kind mid-run is never overwritten. content_json, status and
    updated_at are left alone. Returns counts plus the unknown rows."""
    init_table(cx)
    rows = cx.execute("SELECT id, email, scan_date, scan_id, content_json, status "
                      "FROM portal_biofield_reports WHERE report_kind IS NULL "
                      "ORDER BY id").fetchall()
    out = {"dry_run": bool(dry_run), "rows_without_kind": len(rows),
           REMEDY_MATCH: 0, BIOFIELD: 0, "written": 0, "raced": 0, "unknown": []}
    for rid, email, sd, sid, cj, status in rows:
        try:
            content = json.loads(cj or "{}")
        except Exception:
            content = {}
        kind = infer_kind(content, sid)
        if kind is None:
            out["unknown"].append({"id": rid, "email": email, "scan_date": sd,
                                   "status": status})
            continue
        out[kind] += 1
        if not dry_run:
            cur = cx.execute("UPDATE portal_biofield_reports SET report_kind=? "
                             "WHERE id=? AND report_kind IS NULL", (kind, rid))
            if cur.rowcount:
                out["written"] += 1
            else:
                out["raced"] += 1
    if not dry_run:
        cx.commit()
    return out


def list_report_dates(cx, email):
    email = (email or "").strip().lower()
    rows = cx.execute("SELECT scan_date FROM portal_biofield_reports WHERE email=? "
                      "ORDER BY scan_date DESC", (email,)).fetchall()
    return [r[0] for r in rows]


def latest_report(cx, email):
    email = (email or "").strip().lower()
    row = cx.execute(f"SELECT {_COLS} "
                     "FROM portal_biofield_reports WHERE email=? "
                     "ORDER BY scan_date DESC LIMIT 1", (email,)).fetchone()
    return _row_to_dict(row) if row else None


def set_report_status(cx, email, scan_date, status):
    email = (email or "").strip().lower()
    cur = cx.execute("UPDATE portal_biofield_reports SET status=?, updated_at=? "
                     "WHERE email=? AND scan_date=?", (status, _now_iso(), email, scan_date))
    cx.commit()
    return cur.rowcount > 0


def is_actionable(scan_date, today):
    """A scan is actionable (CTAs/transitions allowed) within 30 days of `today`.
    Both args are 'YYYY-MM-DD'. Bad/empty scan_date -> False."""
    try:
        sd = datetime.date.fromisoformat(scan_date)
        td = datetime.date.fromisoformat(today)
    except (ValueError, TypeError):
        return False
    return 0 <= (td - sd).days <= 30


def report_pdf_urls(cx, emails):
    """{email_lower: url} for each email whose LATEST confirmed report carries a
    non-empty content.report_pdf.url. Emails without one are omitted. None-raising."""
    wanted = sorted({(e or "").strip().lower() for e in (emails or []) if (e or "").strip()})
    if not wanted:
        return {}
    ph = ",".join("?" * len(wanted))
    rows = cx.execute(
        f"SELECT lower(email), content_json FROM portal_biofield_reports "
        f"WHERE lower(email) IN ({ph}) AND status='confirmed' "
        f"ORDER BY scan_date DESC", wanted).fetchall()
    out = {}
    for em, content_json in rows:
        if em in out:
            continue                      # rows are newest-first; keep the first per email
        try:
            url = ((json.loads(content_json or "{}").get("report_pdf") or {}).get("url") or "").strip()
        except Exception:
            url = ""
        if url:
            out[em] = url
    return out


# Link and file fields are never reworded: ".../voice-scan-2026.pdf" renamed is a
# broken link (review round 1).
_LINK_KEYS = {"url", "href", "src", "path", "file", "filename", "link", "pdf_url", "audio_url"}


def _looks_like_link(text):
    t = text.strip()
    return bool(t) and " " not in t and ("://" in t or t.startswith("/") or
                                         bool(re.search(r"\.[a-z0-9]{2,4}$", t, re.I)))


# A web address, markdown link target or href inside prose is left exactly as written;
# only the words around it are renamed (review round 2).
_LINK_SPAN = re.compile(r"""https?://[^\s)\]"'<>]+|\]\([^)]*\)|href=["'][^"']*["']|"""
                        r"""(?<![\w])/[\w./-]*voice[-_]scan[\w./-]*""", re.IGNORECASE)


def _fix_prose(text, fix):
    out, last = [], 0
    for m in _LINK_SPAN.finditer(text):
        out.append(fix(text[last:m.start()]) if m.start() > last else "")
        out.append(m.group(0))
        last = m.end()
    out.append(fix(text[last:]) if last < len(text) else "")
    return "".join(out)


def _rewrite_strings(value, fix, path="", key=""):
    """Apply fix to every prose string inside a JSON-shaped value.
    Returns (new value, [changed field paths])."""
    if isinstance(value, str):
        if key.lower() in _LINK_KEYS or _looks_like_link(value):
            return value, []
        new = _fix_prose(value, fix)
        return new, ([path or "."] if new != value else [])
    if isinstance(value, list):
        out, paths = [], []
        for i, v in enumerate(value):
            nv, p = _rewrite_strings(v, fix, f"{path}[{i}]", key)
            out.append(nv)
            paths += p
        return out, paths
    if isinstance(value, dict):
        out, paths = {}, []
        for k, v in value.items():
            nv, p = _rewrite_strings(v, fix, f"{path}.{k}" if path else str(k), str(k))
            out[k] = nv
            paths += p
        return out, paths
    return value, []


# The report names Glen's own instrument: "Five Element" beside "voice", in either order.
# A plain "5 elements" or a product like "Five Elements Tea" does not count (review round 2).
_FIVE_VOICE = re.compile(r"(?:five|5)[\s-]*elements?\W{0,6}voice|voice[\s-]+scans?\W{0,6}\(?\s*"
                         r"(?:five|5)[\s-]*element", re.IGNORECASE)


def _strings(value):
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [s for v in value for s in _strings(v)]
    if isinstance(value, dict):
        return [s for v in value.values() for s in _strings(v)]
    return []


def _any_string(value, pred):
    if isinstance(value, str):
        return bool(pred(value))
    if isinstance(value, list):
        return any(_any_string(v, pred) for v in value)
    if isinstance(value, dict):
        return any(_any_string(v, pred) for v in value.values())
    return False


def fix_scan_names_in_reports(cx, *, apply=False):
    """Rename E4L's scan to the Bioenergetic Wellness Scan in every stored report.

    Glen, 2026-09-25, via clinical: "Let platform fix the older reports on portals." The
    current reports were patched by clinical; the older rows could not be, because
    /admin/portal/upsert also rewrites the portal's main content and would move a client
    back to that report. This writes content_json ONLY: never status, updated_at,
    client_portals or mail. Dry run unless apply=True.

    A row is written only if its content is unchanged since it was read, so another
    session's edit mid-run is never reverted; such rows are counted as "raced". A report
    that names the Five Element Voice Analysis (once "Voice Scan") anywhere keeps every bare "voice scan" and
    is listed for Glen. Returns {"rows", "changed", "left_for_glen", "raced", "skipped"};
    each item is {"id", "scan_date", "status", "fields"}: no email, no text."""
    from dashboard.narrative_grounding import fix_scan_names, scan_name_problems
    init_table(cx)
    rows = cx.execute("SELECT id, scan_date, content_json, status "
                      "FROM portal_biofield_reports ORDER BY id").fetchall()
    changed, left, raced, skipped = [], [], [], []
    for rid, scan_date, cj, status in rows:
        item = {"id": rid, "scan_date": scan_date, "status": status}
        try:
            content = json.loads(cj or "{}")
            # Read the whole report as one text: {"title": "Five Element", "sub": "Voice
            # Scan"} names Glen's instrument across two fields (review round 3).
            five = bool(_FIVE_VOICE.search(" ".join(_strings(content))))
            new, fields = _rewrite_strings(content, lambda t: fix_scan_names(t, five))
            if _any_string(new, lambda t: scan_name_problems(t) or (
                    five and fix_scan_names(t) != t)):
                left.append(item)
        except (TypeError, ValueError, RecursionError):
            skipped.append(item)
            continue
        if not fields:
            continue
        item = dict(item, fields=fields)
        if apply:
            # Compare-and-set on the text read above. updated_at is left alone: the
            # wording changed, the report did not.
            cur = cx.execute("UPDATE portal_biofield_reports SET content_json=? "
                             "WHERE id=? AND content_json=?", (json.dumps(new), rid, cj))
            if not cur.rowcount:
                raced.append(item)
                continue
        changed.append(item)
    if apply and changed:
        cx.commit()
    return {"rows": len(rows), "changed": changed, "left_for_glen": left,
            "raced": raced, "skipped": skipped}
