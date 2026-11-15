"""Which infoceuticals a client's E4L scan recommends, and each bar's colour on it.

Glen, 2026-10-17: "Indicate which infoceuticals are among the 5 recommended, and
also which are purple." The 5 recommended are the scan's rows under
"Infoceuticals" in e4l.db. Purple is not in e4l.db: pdftotext drops colour, and
scan_trends.py only estimates it from rank. So it is read from the scan PDF
itself. The PDF draws each bar as a vector path with an exact fill colour; this
reads the fill and ties it to the code label printed just above the bar. Checked
by eye against Josie Gagnon's scan of 2026-10-11 (pages 1, 7, 10 and 19).

Glen, 2026-10-18: "Just show an icon with the color", and "Since nutrients are
included in e4l scans, show their color as well." So every bar is read, grey
(empty) included, under its code or, on the Nutrition Fields pages, its name.

A PDF is read once per scan and the colours are kept in chat_log.db, so a page
load never re-reads it. A scan whose PDF cannot be read says so; it never reads
as "nothing is purple".

Glen, 15 Nov 2026: the drop-down's BODY section splits the purple bars that are not
infoceuticals into "Body stresses" and "Psyche", by the scan section the bar sits on.
So each bar's page heading is kept beside its colours (bar_sections).
"""
import html
import json
import re
import sqlite3
import subprocess

# Fill colours on E4L scan PDFs. Grey is an empty bar, and also the unfilled part
# of a coloured one, so a bar is grey only when nothing else is on it.
COLOURS = {(0.584, 0.506, 0.706): "purple", (0.459, 0.718, 0.447): "green",
           (0.949, 0.949, 0.102): "yellow", (1.0, 0.749, 0.247): "orange",
           (0.871, 0.878, 0.886): "grey"}
_PAGE = re.compile(r'<page width="[\d.]+" height="([\d.]+)"')
_LINE = re.compile(r'<line xMin="([\d.]+)" yMin="[\d.]+" xMax="[\d.]+" yMax="([\d.]+)">'
                   r'(.*?)</line>', re.S)
_WORD = re.compile(r'>([^<]+)</word>')
_CODE = re.compile(r"^([A-Z]{2,3}\d+|BFA)\b")
# Bump when COLOURS or the matching changes: scans read by an older reader re-read.
# 3: each bar's page section is stored too (15 Nov 2026).
READER_VERSION = 3

# Glen, 15 Nov 2026, measured on a real scan: these sections are the psyche pages. Every
# other section (ENVIRONMENTAL, AGRICULTURE, NUTRITION, DIGESTION / METABOLISM,
# SENSITIVITIES, and the rest) is the body. Matched at the start of the heading, so
# "MIND BODY - CORE EMOTIONS" is MIND BODY.
PSYCHE_SECTIONS = ("MIND BODY", "LIFE JOURNEY", "LIFE PURPOSE", "HERO'S JOURNEY",
                   "ARCHETYPES", "BELIEFS", "EMOTIONAL ATTRACTION", "TRANSFORMATIONS",
                   "UNCONSCIOUS PATTERNS")
_PAGE_TEXT = re.compile(r"<page [^>]*>(.*?)</page>", re.S)


def ensure_schema(cx):
    cx.execute("""CREATE TABLE IF NOT EXISTS biofield_scan_bar_colour (
        scan_id TEXT NOT NULL, code TEXT NOT NULL, colours TEXT NOT NULL,
        sections TEXT NOT NULL DEFAULT '',
        PRIMARY KEY (scan_id, code)
    )""")
    # A table made before READER_VERSION 3 has no sections column. Added once; '' is
    # "section unknown", which reads as Body stresses.
    cols = {r[1] for r in cx.execute("PRAGMA table_info(biofield_scan_bar_colour)")}
    if "sections" not in cols:
        cx.execute("ALTER TABLE biofield_scan_bar_colour "
                   "ADD COLUMN sections TEXT NOT NULL DEFAULT ''")
    # One row per scan read, so a scan with no coloured bars is not re-read and a
    # failed read keeps its reason.
    cx.execute("""CREATE TABLE IF NOT EXISTS biofield_scan_bar_read (
        scan_id TEXT PRIMARY KEY, ok INTEGER NOT NULL, error TEXT NOT NULL DEFAULT '',
        version INTEGER NOT NULL DEFAULT 0,
        read_at TEXT DEFAULT CURRENT_TIMESTAMP
    )""")


def _mul(a, b):
    return [a[0]*b[0]+a[1]*b[2], a[0]*b[1]+a[1]*b[3], a[2]*b[0]+a[3]*b[2],
            a[2]*b[1]+a[3]*b[3], a[4]*b[0]+a[5]*b[2]+b[4], a[4]*b[1]+a[5]*b[3]+b[5]]


def _segments(reader, stream, resources, ctm, out):
    from pypdf.generic import ContentStream
    stack, fill = [], None
    for ops, op in ContentStream(stream, reader).operations:
        if op == b"q":
            stack.append((ctm, fill))
        elif op == b"Q":
            ctm, fill = stack.pop()
        elif op == b"cm":
            ctm = _mul([float(x) for x in ops], ctm)
        elif op == b"rg":
            fill = tuple(round(float(x), 3) for x in ops)
        elif op == b"m" and fill in COLOURS:
            x, y = float(ops[0]), float(ops[1])
            out.append((COLOURS[fill], ctm[0]*x+ctm[2]*y+ctm[4], ctm[1]*x+ctm[3]*y+ctm[5]))
        elif op == b"Do":
            xo = resources["/XObject"][ops[0]].get_object()
            if xo.get("/Subtype") == "/Form":
                matrix = [float(v) for v in xo.get("/Matrix", [1, 0, 0, 1, 0, 0])]
                _segments(reader, xo, xo.get("/Resources", resources),
                          _mul(matrix, ctm), out)


def _label(text):
    """A bar's label: its code ("ET1 - Immune 1" is ET1), else the whole line
    ("Vitamin B12", "Omega 3 - EPA")."""
    found = _CODE.match(text)
    return found.group(1) if found else text


def page_heading(lines):
    """A scan page's section heading, or None for a continuation page.

    `lines` are the page's text lines in pdftotext's order. The first two are the
    header (the practitioner line, then the client and scan date). The heading is the
    line after them when it is in capitals ("NUTRITION FIELDS - PROTEIN FIELDS"). A
    continuation page starts with body text there instead ("easy. We mustn't ...").
    A line that starts with a code ("ET0 - CNS") is a bar label, never a heading."""
    if len(lines) < 3:
        return None
    text = " ".join(html.unescape(str(lines[2] or "")).split())
    if not re.search(r"[A-Za-z]", text) or text != text.upper() or _CODE.match(text):
        return None
    return text


def page_sections(headings):
    """Each page's section: its own heading, else the last page's before it. A
    continuation page inherits; pages before the first heading have None."""
    out, current = [], None
    for heading in headings:
        current = heading or current
        out.append(current)
    return out


def _heading_key(heading):
    return " ".join(str(heading or "").replace("\u2019", "'").replace("\u2018", "'")
                    .upper().split())


def is_psyche_section(heading):
    """True for a psyche page's heading (PSYCHE_SECTIONS, matched at the start)."""
    key = _heading_key(heading)
    return any(key == p or key.startswith(p + " ") for p in PSYCHE_SECTIONS)


def is_psyche(sections):
    """A bar is Psyche when any page it sits on is a psyche page. No sections known
    (an older read, or a heading that could not be read) is not Psyche."""
    return any(is_psyche_section(s) for s in sections or ())


class BarColours(dict):
    """bar_colours' answer: {label: {colour}}, with `.sections` = {label: {heading}}
    for the labels whose page section is known."""
    sections = None


def bar_colours(path):
    """{label: {colour, ...}} for every bar in the PDF, by code or by nutrient field
    name. A label can carry more than one colour when it appears on several pages;
    "grey" means every bar it has is empty. Markers on the body diagrams are
    skipped: their labels sit beside them, not above. The answer's `.sections` holds
    each label's page sections (page_heading, inherited by continuation pages)."""
    from pypdf import PdfReader
    reader = PdfReader(path)
    # One pdftotext run for the whole document: a continuation page needs the heading
    # of the page before it, even when that page has no bars.
    doc = subprocess.run(["pdftotext", "-bbox-layout", path, "-"],
                         capture_output=True, text=True, check=True, timeout=120).stdout
    pages = [(m.group(0), m.group(1)) for m in _PAGE_TEXT.finditer(doc)]
    if len(pages) != len(reader.pages):
        raise ValueError(f"pdftotext read {len(pages)} pages, pypdf {len(reader.pages)}")
    lines = [[(" ".join(_WORD.findall(m.group(3))).strip(), float(m.group(1)),
               float(m.group(2))) for m in _LINE.finditer(body)] for _, body in pages]
    sections = page_sections([page_heading([t for t, _, _ in ls]) for ls in lines])
    out, where = {}, {}
    for n, page in enumerate(reader.pages):
        segments = []
        _segments(reader, page.get_contents(), page["/Resources"], [1, 0, 0, 1, 0, 0],
                  segments)
        if not segments:
            continue
        height = float(_PAGE.search(pages[n][0]).group(1))
        labels = [(_label(t), x, y) for t, x, y in lines[n]]
        for colour, x, y in segments:
            top = height - y
            # The label sits just above its bar, starting at the bar's left edge.
            near = [lab for lab in labels if 0 <= top - lab[2] < 12 and abs(lab[1] - x) < 2]
            if len(near) == 1:
                out.setdefault(near[0][0], set()).add(colour)
                if sections[n]:
                    where.setdefault(near[0][0], set()).add(sections[n])
    found = BarColours({label: (c - {"grey"}) or {"grey"} for label, c in out.items()})
    found.sections = where
    return found


def _read_once(cx, scan_id, pdf_path):
    row = cx.execute("SELECT ok,error,version FROM biofield_scan_bar_read WHERE scan_id=?",
                     (scan_id,)).fetchone()
    if row and row[2] == READER_VERSION:
        return bool(row[0]), row[1] or ""
    # A good read by an older reader is kept if this re-read fails (the PDF may have
    # moved since): its colours stand, and its sections read as unknown.
    older_ok = bool(row and row[0])
    try:
        if not pdf_path:
            raise ValueError("no scan PDF is recorded for this scan")
        colours = bar_colours(pdf_path)
        if not colours or all(found == {"grey"} for found in colours.values()):
            # Every scan has coloured bars. None found means the reader missed them,
            # which must not read as "nothing is purple".
            raise ValueError("no coloured bars were found in the scan PDF")
    except Exception as exc:  # a missing file, pdftotext or pypdf: say why
        error = str(exc)[:300] or exc.__class__.__name__
        if older_ok:
            return True, ""
        # Not stored: the PDF may arrive later, so the next page load tries again.
        return False, error
    where = getattr(colours, "sections", None) or {}
    cx.execute("DELETE FROM biofield_scan_bar_colour WHERE scan_id=?", (scan_id,))
    for code, found in colours.items():
        cx.execute("INSERT INTO biofield_scan_bar_colour (scan_id,code,colours,sections) "
                   "VALUES (?,?,?,?)", (scan_id, code, ",".join(sorted(found)),
                                        json.dumps(sorted(where.get(code) or ()))))
    cx.execute("INSERT OR REPLACE INTO biofield_scan_bar_read (scan_id,ok,error,version) "
               "VALUES (?,1,'',?)", (scan_id, READER_VERSION))
    cx.commit()
    return True, ""


def infoceutical_marks(cx, e4l_db, scan_id):
    """{"scan_id", "recommended": {code} or None, "recommended_error",
    "purple": {code} or None, "colours": {label: [colour]} or None, "purple_error"}.
    `colours` holds every bar by code or nutrient field name. A value is None when it
    could not be read, with the reason beside it. The result is None with no scan."""
    if not scan_id or not e4l_db:
        return None
    scan_id = str(scan_id)
    # e4l.db keeps scan ids as numbers; '7' never equals 7 in a column without affinity.
    key = int(scan_id) if scan_id.isdigit() else scan_id
    unread = {"scan_id": scan_id, "recommended": None, "purple": None, "colours": None,
              "recommended_error": "the scan database could not be read",
              "purple_error": "the scan database could not be read"}
    try:
        ecx = sqlite3.connect(f"file:{e4l_db}?mode=ro", uri=True)  # raw-sqlite-ok: e4l.db, read only
    except sqlite3.Error:
        return unread
    try:
        recommended = {r[0].upper() for r in ecx.execute(
            "SELECT item_code FROM e4l_scan_results WHERE scan_id=? AND "
            "section_context='Infoceuticals' AND item_code IS NOT NULL", (key,))}
        row = ecx.execute("SELECT raw_pdf_path FROM e4l_scans WHERE scan_id=?",
                          (key,)).fetchone()
    except sqlite3.Error:
        return unread
    finally:
        ecx.close()
    recommended_error = ""
    if not recommended:
        # Every scan recommends infoceuticals; none stored means the import missed
        # them (49 of 3,373 scans on 2026-10-17), not that none are recommended.
        recommended, recommended_error = None, "the scan's recommended list is not stored"
    ensure_schema(cx)
    ok, error = _read_once(cx, scan_id, row[0] if row else "")
    purple = colours = None
    if ok:
        colours = {r[0]: r[1].split(",") for r in cx.execute(
            "SELECT code,colours FROM biofield_scan_bar_colour WHERE scan_id=?", (scan_id,))}
        purple = {code for code, found in colours.items() if "purple" in found}
    return {"scan_id": scan_id, "recommended": recommended,
            "recommended_error": recommended_error, "purple": purple,
            "colours": colours, "purple_error": error}


def bar_sections(cx, scan_id):
    """{label: [heading]} from the stored read of this scan, for labels whose page
    section is known. {} when nothing is stored or the read predates sections. Call
    after infoceutical_marks, which does the read."""
    if not scan_id:
        return {}
    try:
        ensure_schema(cx)
        rows = cx.execute("SELECT code,sections FROM biofield_scan_bar_colour "
                          "WHERE scan_id=?", (str(scan_id),)).fetchall()
    except sqlite3.Error:
        return {}
    out = {}
    for code, raw in rows:
        try:
            found = [str(s) for s in json.loads(raw or "[]") if s]
        except (TypeError, ValueError):
            found = []
        if found:
            out[code] = found
    return out


# "12. Choice Point", "8 - Humility": the bar's position on its page, not its name.
_POSITION = re.compile(r"^\d+\s*(?:\.|-)\s+")


def purple_finding_names(purple, code_names=None):
    """Every bar purple on the scan, for the Location drop-down's BODY section (Glen,
    15 Nov 2026: "the purple findings from the E4L scan").

    `purple` is infoceutical_marks()["purple"]: bar labels, an E4L code ("ED4") or a
    printed name ("Molybdenum", "12. Choice Point"). An infoceutical code stays a code:
    the drop-down shows it as "ED4: Nerve" (biofield_layer_match.stress_display). Any
    other code (ER, MR) is shown by its name from `code_names` ({CODE: name}), or as
    the bare code when no name is known. A leading position number is dropped. Each
    once; sectioned_vocab puts them in order."""
    from dashboard.biofield_clinical_sections import _INFOCEUTICAL_CODE
    names = {str(k).upper(): v for k, v in (code_names or {}).items() if v}
    out, seen = [], set()
    for label in purple or ():
        label = str(label or "").strip()
        code = _CODE.match(label)
        if code and code.group(1) == label:
            name = label.upper() if _INFOCEUTICAL_CODE.match(label) else (
                names.get(label.upper()) or label).strip()
        else:
            name = _POSITION.sub("", label).strip()
        key = re.sub(r"\s+", "", name.lower())
        if name and key not in seen:
            seen.add(key)
            out.append(name)
    return out


def e4l_code_names(e4l_db, codes):
    """{CODE: name} from e4l.db's e4l_items, read only. {} when it cannot be read."""
    codes = sorted({str(c).upper() for c in codes or () if c})
    if not codes or not e4l_db:
        return {}
    try:
        ecx = sqlite3.connect(f"file:{e4l_db}?mode=ro", uri=True)  # raw-sqlite-ok: e4l.db, read only
    except sqlite3.Error:
        return {}
    try:
        marks = ",".join("?" for _ in codes)
        return {str(c).upper(): n for c, n in ecx.execute(
            f"SELECT code,name FROM e4l_items WHERE UPPER(code) IN ({marks})", codes)
            if n}
    except sqlite3.Error:
        return {}
    finally:
        ecx.close()
