"""Which of Glen's Five Phases of Terrain an E4L finding belongs to.

The phases (the 5 R's, see the `five-phases-of-terrain` skill):

    1 Recharge   anergy, catabolic      cancer, degeneration, viral, low energy
    2 Rejuvenate anabolic swing         aging, bacterial, parasite, metabolism, enzymes
    3 Regenerate stable regenerative    regeneration, fungal, tissue cleansing, lymph, immune
    4 Reclaim    allergy, elimination   allergy, toxicity / detoxification
    5 Regulate   balance                stress, endocrine

Glen's rules, 2026-09-16, given in his own words:

- "Most of the ET terrains in e4l are linked to particular viral susceptibility
  patterns which would be Phase 1. The exceptions are specified as Bacterial (2) and
  Fungal (3)."
- "Autonomic Nervous System: link to Phase 5." ED6 and EI11 are the two findings whose
  own descriptions name the sympathetic/parasympathetic nerves.

Deliberately NOT guessed: the other 240 findings. ER, ENV, NUT, ES, MR, MB and most of
ED and EI carry no phase here, and `phase_for` returns None for them rather than a
plausible number. A wrong phase would group a layer around the wrong healing
direction, which is worse than grouping on something else.

Do not read the `terrain-phase:*` tags in e4l.db as a signal for this. 672 of the ~700
tagged clients are `regenerate`, set by the `e4l:matrix-regulators` rule rather than by
a reading; only two were set by a clinician.
"""

RECHARGE, REJUVENATE, REGENERATE, RECLAIM, REGULATE = 1, 2, 3, 4, 5

PHASE_NAMES = {
    RECHARGE: "Recharge",
    REJUVENATE: "Rejuvenate",
    REGENERATE: "Regenerate",
    RECLAIM: "Reclaim",
    REGULATE: "Regulate",
}

# The ET exceptions, by name rather than by number, because the number is positional
# and the name is what Glen ruled on.
_ET_EXCEPTIONS = {"ET13": REGENERATE,      # Fungal Terrain
                  "ET14": REJUVENATE}      # Bacterial Terrain

# Findings whose own text names the autonomic nervous system.
_AUTONOMIC = {"ED6": REGULATE,             # Heart Driver
              "EI11": REGULATE}            # Bone Marrow – Stomach Integrator


def phase_for(code):
    """The phase for one finding code, or None when Glen has not ruled on it.

    None is a real answer here and callers must handle it. Grouping falls back to
    another axis rather than inventing a healing direction.
    """
    code = str(code or "").strip().upper()
    if not code:
        return None
    if code in _AUTONOMIC:
        return _AUTONOMIC[code]
    if code in _ET_EXCEPTIONS:
        return _ET_EXCEPTIONS[code]
    if code.startswith("ET"):
        # "Most of the ET terrains ... are linked to particular viral susceptibility
        # patterns which would be Phase 1."
        return RECHARGE
    return None


def phase_name(phase):
    return PHASE_NAMES.get(phase, "")


def group_by_phase(codes):
    """{phase: [codes]} plus an "unplaced" bucket, order preserved within each."""
    out, unplaced = {}, []
    for code in codes or []:
        p = phase_for(code)
        if p is None:
            unplaced.append(code)
        else:
            out.setdefault(p, []).append(code)
    return {"by_phase": out, "unplaced": unplaced}
