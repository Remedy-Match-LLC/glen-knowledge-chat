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
"""
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
READER_VERSION = 2


def ensure_schema(cx):
    cx.execute("""CREATE TABLE IF NOT EXISTS biofield_scan_bar_colour (
        scan_id TEXT NOT NULL, code TEXT NOT NULL, colours TEXT NOT NULL,
        PRIMARY KEY (scan_id, code)
    )""")
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


def bar_colours(path):
    """{label: {colour, ...}} for every bar in the PDF, by code or by nutrient field
    name. A label can carry more than one colour when it appears on several pages;
    "grey" means every bar it has is empty. Markers on the body diagrams are
    skipped: their labels sit beside them, not above."""
    from pypdf import PdfReader
    reader = PdfReader(path)
    out = {}
    for n, page in enumerate(reader.pages, start=1):
        segments = []
        _segments(reader, page.get_contents(), page["/Resources"], [1, 0, 0, 1, 0, 0],
                  segments)
        if not segments:
            continue
        text = subprocess.run(["pdftotext", "-f", str(n), "-l", str(n), "-bbox-layout",
                               path, "-"],
                              capture_output=True, text=True, check=True, timeout=20).stdout
        height = float(_PAGE.search(text).group(1))
        labels = [(_label(" ".join(_WORD.findall(m.group(3))).strip()),
                   float(m.group(1)), float(m.group(2))) for m in _LINE.finditer(text)]
        for colour, x, y in segments:
            top = height - y
            # The label sits just above its bar, starting at the bar's left edge.
            near = [lab for lab in labels if 0 <= top - lab[2] < 12 and abs(lab[1] - x) < 2]
            if len(near) == 1:
                out.setdefault(near[0][0], set()).add(colour)
    return {label: (found - {"grey"}) or {"grey"} for label, found in out.items()}


def _read_once(cx, scan_id, pdf_path):
    row = cx.execute("SELECT ok,error,version FROM biofield_scan_bar_read WHERE scan_id=?",
                     (scan_id,)).fetchone()
    if row and row[2] == READER_VERSION:
        return bool(row[0]), row[1] or ""
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
        # Not stored: the PDF may arrive later, so the next page load tries again.
        return False, error
    cx.execute("DELETE FROM biofield_scan_bar_colour WHERE scan_id=?", (scan_id,))
    for code, found in colours.items():
        cx.execute("INSERT INTO biofield_scan_bar_colour (scan_id,code,colours) "
                   "VALUES (?,?,?)", (scan_id, code, ",".join(sorted(found))))
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
