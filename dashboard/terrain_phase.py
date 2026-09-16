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
- "Heavy metals is also important in phase 1 (detox at the intracellular level)."

**A finding can serve more than one phase.** ES15 Heavy Metals Detox is the case that
proves it: Phase 4 as elimination, and Phase 1 as intracellular detox in the anergy
terrain. So this returns a TUPLE, not a number. An earlier version returned a single
phase and would have forced a choice that loses half the answer.

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

# Ruled per finding. A tuple, because a finding can serve several phases.
_RULED = {
    "ET13": (REGENERATE,),        # Fungal Terrain
    "ET14": (REJUVENATE,),        # Bacterial Terrain
    "ED6": (REGULATE,),           # Heart Driver — sympathetic/parasympathetic
    "EI11": (REGULATE,),          # Bone Marrow - Stomach Integrator — same
    # Elimination AND intracellular detox in the anergy terrain. Glen, 2026-09-16.
    "ES15": (RECLAIM, RECHARGE),  # Heavy Metals Detox
}


def phases_for(code):
    """Every phase a finding serves, as a tuple. Empty when Glen has not ruled on it.

    Empty is a real answer and callers must handle it: grouping falls back to another
    axis rather than inventing a healing direction.
    """
    code = str(code or "").strip().upper()
    if not code:
        return ()
    if code in _RULED:
        return _RULED[code]
    if code.startswith("ET"):
        # "Most of the ET terrains ... are linked to particular viral susceptibility
        # patterns which would be Phase 1."
        return (RECHARGE,)
    return ()


def phase_for(code):
    """The FIRST phase, for a caller that can only hold one. Prefer phases_for:
    ES15 serves two, and this hides the second."""
    got = phases_for(code)
    return got[0] if got else None


def phase_name(phase):
    return PHASE_NAMES.get(phase, "")


def group_by_phase(codes):
    """{phase: [codes]} plus an "unplaced" bucket, order preserved within each.

    A finding serving two phases appears under BOTH, which is the point: ES15 is how
    a detox layer and a low-energy layer come to share an anchor.
    """
    out, unplaced = {}, []
    for code in codes or []:
        ps = phases_for(code)
        if not ps:
            unplaced.append(code)
        for p in ps:
            out.setdefault(p, []).append(code)
    return {"by_phase": out, "unplaced": unplaced}
