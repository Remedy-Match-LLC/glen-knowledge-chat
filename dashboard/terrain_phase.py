"""Consumer-facing names for the 5 terrain phases (the BSI 'phase P' reading Glen
speaks into a biofield transcript). Phase number (1-5) -> the Terrain R-name Glen
reports on the client report + invoice. See the five-phases-of-terrain skill: the
number ordering matches the depleted->excess terrain spectrum
(Energize / Rejuvenate / Regenerate / Cleanse / Balance)."""

PHASE_NAMES = {
    1: "Terrain Revive",
    2: "Terrain Repair",
    3: "Terrain Renew",
    4: "Terrain Refresh",
    5: "Terrain Relief",
}

PHASE_CLINICAL_NAMES = {
    1: "Energize",
    2: "Rejuvenate",
    3: "Regenerate",
    4: "Cleanse",
    5: "Balance",
}

PHASE_DESCRIPTIONS = {
    1: "This phase focuses on restoring foundational cellular energy and the body's capacity to begin healing.",
    2: "This phase focuses on rebuilding compromised systems, including enzyme and microbial function.",
    3: "This phase focuses on tissue restoration and restoring microbial coherence.",
    4: "This phase focuses on clearing accumulated burdens after the body has built the capacity to move them.",
    5: "This phase focuses on restoring endocrine, life-stress, and regulatory balance.",
}


def phase_num(v):
    """Coerce a spoken/stored phase to an int in 1..5, else None. Never raises.
    Only a bare integer counts -- 'phase 3' returns None (the caller passes P)."""
    try:
        n = int(str(v).strip())
    except (TypeError, ValueError):
        return None
    return n if n in PHASE_NAMES else None


def phase_name(v):
    """'Terrain Refresh' for phase 4, '' when the phase is missing/out of range."""
    return PHASE_NAMES.get(phase_num(v), "")


def phase_display(v):
    """'Terrain Refresh (Phase 4)', or '' when there is no valid phase."""
    n = phase_num(v)
    return f"{PHASE_NAMES[n]} (Phase {n})" if n else ""


def phase_narrative_description(v, location=""):
    """Plain-language terrain context for the generated patient narrative."""
    n = phase_num(v)
    if not n:
        return ""
    loc = str(location or "").strip()
    where = f" The reading located this phase at {loc}." if loc else ""
    return f"Phase {n}, {PHASE_CLINICAL_NAMES[n]}: {PHASE_DESCRIPTIONS[n]}{where}"


# Biological Score Index (BSI): four numbers, each 1 to 21, spoken "A x B to C x D".
# The Regulation level compares the second reading with the first: C with A, D with B.
# Glen's table, confirmed 7 November 2026 (platform/plans/2026-11-07-live-session-segments.md).
BSI_MIN, BSI_MAX = 1, 21

REGULATION_NAMES = {
    1: "Blocked",
    2: "Negative",
    3: "Mixed",
    4: "Positive",
    5: "Optimum",
}


def bsi_num(v):
    """Coerce one BSI number to an int in 1..21, else None. Never raises.
    A bool is not a number here, and neither is 3.5."""
    if isinstance(v, bool):
        return None
    if isinstance(v, float):
        if not v.is_integer():
            return None
        v = int(v)
    try:
        n = int(str(v).strip())
    except (TypeError, ValueError):
        return None
    return n if BSI_MIN <= n <= BSI_MAX else None


def regulation_level(a, b, c, d):
    """(level, name) from the four BSI numbers, or None when any is missing or out of
    range. Checked top down in exactly this order (Glen, 7 November 2026):

      5 Optimum   C = 1 and D = 1
      1 Blocked   C = A and D = B
      3 Mixed     (C > A and D < B) or (C < A and D > B)
      2 Negative  C >= A and D >= B, not both equal
      4 Positive  C <= A and D <= B, not both equal

    B > A or D > C is not rejected: the plan says it never happens, so the page shows
    a warning (bsi_warnings) and the level is still computed.
    Mirrored in JS as regulationLevel() in dashboard/biofield_report_html.py."""
    a, b, c, d = bsi_num(a), bsi_num(b), bsi_num(c), bsi_num(d)
    if None in (a, b, c, d):
        return None
    if c == 1 and d == 1:
        lvl = 5
    elif c == a and d == b:
        lvl = 1
    elif (c > a and d < b) or (c < a and d > b):
        lvl = 3
    elif c >= a and d >= b:
        lvl = 2
    elif c <= a and d <= b:
        lvl = 4
    else:  # unreachable for valid input: the five rules cover every combination
        return None
    return lvl, REGULATION_NAMES[lvl]


def bsi_warnings(a, b, c, d):
    """Quiet warnings for readings the plan says never happen. Never blocks."""
    a, b, c, d = bsi_num(a), bsi_num(b), bsi_num(c), bsi_num(d)
    out = []
    if a is not None and b is not None and b > a:
        out.append("B is greater than A")
    if c is not None and d is not None and d > c:
        out.append("D is greater than C")
    return out


def regulation_display(a, b, c, d):
    """'Level 4 · Positive', or 'Enter all four numbers'."""
    r = regulation_level(a, b, c, d)
    return f"Level {r[0]} · {r[1]}" if r else "Enter all four numbers"
