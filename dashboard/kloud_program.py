"""The Kloud PEMF mat's program, set by the scan's Phase.

Glen, 2026-10-18: "yes, the Phase always sets the Kloud program". Programs 1 to 5
are Relax, Balance, Energize, Synchronize and Transform; Phases 1 to 5 map to
programs 3, 4, 5, 1 and 2. The line reads "Program 3 (Energize)".

Where the Phase comes from, in order (Glen, same day):
1. this test's saved Phase;
2. the client's most recent earlier Biofield with a Phase;
3. the terrain answer on the client's submitted Intake form.
With none of these the line names no program, and Glen's note says so.
"""
from dashboard.terrain_phase import phase_num

# Phase -> (Kloud program number, program name).
PROGRAMS = {1: (3, "Energize"), 2: (4, "Synchronize"), 3: (5, "Transform"),
            4: (1, "Relax"), 5: (2, "Balance")}


def is_kloud(name):
    return "kloud" in str(name or "").lower()


def program_label(phase):
    n = phase_num(phase)
    if not n:
        return ""
    number, name = PROGRAMS[n]
    return f"Program {number} ({name})"


def _auth_id(test_id):
    """The biofield_auth_tests id of an authored test ("a49" -> 49), else None."""
    s = str(test_id or "")
    return int(s[1:]) if s.startswith("a") and s[1:].isdigit() else None


def _own_row(cx, test_id):
    tid = _auth_id(test_id)
    if tid is None:
        return None
    try:
        return cx.execute(
            "SELECT id,email,client_id,e4l_client_id,date_test,phase "
            "FROM biofield_auth_tests WHERE id=?", (tid,)).fetchone()
    except Exception:  # table absent
        return None


def _date(value):
    """A test date as a date, from ISO or FileMaker's M/D/YY or M/D/YYYY; else None."""
    from datetime import date, datetime
    text = str(value or "").strip()
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y"):
        try:
            return datetime.strptime(text[:10] if fmt == "%Y-%m-%d" else text, fmt).date()
        except ValueError:
            continue
    return None


def earlier_phase(cx, test_id, email="", date=""):
    """(phase, test date) from the client's most recent earlier authored Biofield that
    has a Phase, else (None, ""). The same client is the same email, client id or E4L
    client id. Earlier is an earlier test date, or the same date and a lower id; a test
    whose date cannot be read is never counted, and an undated test looks at none.
    A test with no authored row (an E4L-era test) passes its client's email and date."""
    row = _own_row(cx, test_id)
    if row:
        tid, email, client_id, e4l_id, date = row[0], row[1], row[2], row[3], row[4]
    else:
        tid, client_id, e4l_id = None, None, None
    when = _date(date)
    if not when:
        return None, ""
    email = str(email or "").strip().lower()
    same, args = [], []
    for column, value in (("lower(trim(email))", email), ("client_id", client_id),
                          ("e4l_client_id", e4l_id)):
        if str(value or "").strip():
            same.append(f"{column}=?")
            args.append(value)
    if not same:
        return None, ""
    try:
        rows = cx.execute("SELECT id,phase,date_test FROM biofield_auth_tests WHERE ("
                          + " OR ".join(same) + ")", args).fetchall()
    except Exception:  # table absent
        return None, ""
    best = None
    for oid, phase, other in rows:
        d = _date(other)
        if oid == tid or not d or not phase_num(phase):
            continue
        if d > when or (d == when and (tid is None or oid > tid)):
            continue
        if best is None or (d, oid) > best[0]:
            best = ((d, oid), phase_num(phase), str(other).strip())
    return (best[1], best[2]) if best else (None, "")


def resolve(cx, test_id, this_phase=None, intake_answers=None, email="", date=""):
    """(phase, where it came from) for the Kloud program, or (None, "").
    `this_phase` is the test's own Phase when the caller has it; an authored test's
    saved Phase is read otherwise. `intake_answers` is a callable, so the Intake form
    is fetched only when nothing earlier gives a Phase. `email` and `date` identify a
    test that has no authored row."""
    from dashboard.diet_guidance import intake_phase
    n = phase_num(this_phase)
    if not n:
        row = _own_row(cx, test_id)
        n = phase_num(row[5]) if row else None
    if n:
        return n, "this Biofield"
    n, when = earlier_phase(cx, test_id, email, date)
    if n:
        return n, f"the Biofield of {when}"
    n = intake_phase(intake_answers() if intake_answers else None)
    if n:
        return n, "the Intake form"
    return None, ""
