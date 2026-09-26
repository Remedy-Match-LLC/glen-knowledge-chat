"""Per-scan biofield reports: one evolving row per (email, scan_date).
Source of truth for the biofield analysis; client_portals.content_json is the
legacy fallback when a client has no rows here. See the 2026-06-17 spec."""
import datetime
import json
import re
import sqlite3


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
            UNIQUE(email, scan_date)
        )
    """)
    cx.execute("CREATE INDEX IF NOT EXISTS ix_pbr_email ON portal_biofield_reports(email)")
    cx.commit()


def upsert_report(cx, email, scan_date, scan_id, content, status):
    email = (email or "").strip().lower()
    now = _now_iso()
    row = cx.execute(
        "SELECT id FROM portal_biofield_reports WHERE email=? AND scan_date=?",
        (email, scan_date)).fetchone()
    cj = json.dumps(content or {})
    if row:
        cx.execute("UPDATE portal_biofield_reports SET scan_id=?, content_json=?, "
                   "status=?, updated_at=? WHERE id=?",
                   (scan_id, cj, status, now, row[0]))
    else:
        cx.execute("INSERT INTO portal_biofield_reports "
                   "(email, scan_date, scan_id, content_json, status, created_at, updated_at) "
                   "VALUES (?,?,?,?,?,?,?)",
                   (email, scan_date, scan_id, cj, status, now, now))
    cx.commit()


def _row_to_dict(row):
    try:
        content = json.loads(row[3] or "{}")
    except Exception:
        content = {}
    return {"scan_date": row[1], "scan_id": row[2], "content": content, "status": row[4]}


def get_report(cx, email, scan_date):
    email = (email or "").strip().lower()
    row = cx.execute("SELECT id, scan_date, scan_id, content_json, status "
                     "FROM portal_biofield_reports WHERE email=? AND scan_date=?",
                     (email, scan_date)).fetchone()
    return _row_to_dict(row) if row else None


def list_report_dates(cx, email):
    email = (email or "").strip().lower()
    rows = cx.execute("SELECT scan_date FROM portal_biofield_reports WHERE email=? "
                      "ORDER BY scan_date DESC", (email,)).fetchall()
    return [r[0] for r in rows]


def latest_report(cx, email):
    email = (email or "").strip().lower()
    row = cx.execute("SELECT id, scan_date, scan_id, content_json, status "
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
    that names the Five Element Voice Scan anywhere keeps every bare "voice scan" and
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
            five = _any_string(content, _FIVE_VOICE.search)
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
