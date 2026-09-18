"""Which of the 5 terrain phases an E4L FINDING belongs to.

Distinct from `dashboard/terrain_phase.py`, which names the phases for the client
report. That module answers "what is phase 4 called"; this one answers "which phase
is this finding in". The names come from there rather than being restated here — an
earlier version of this file duplicated them under a different set of R-words, which
is exactly how two vocabularies for one thing get into a codebase.

Glen's rules, in his words, 2026-09-16 and 2026-09-17:

- "Most of the ET terrains in e4l are linked to particular viral susceptibility
  patterns which would be Phase 1. The exceptions are specified as Bacterial (2) and
  Fungal (3)."
- "Autonomic Nervous System: link to Phase 5." ED6 and EI11 are the two findings whose
  own descriptions name the sympathetic/parasympathetic nerves.
- "Heavy metals is also important in phase 1 (detox at the intracellular level)."
- "Only ETs are directly phase related."

**A finding can serve more than one phase.** ES15 Heavy Metals Detox is Phase 4 as
elimination and Phase 1 as intracellular detox, so this returns a TUPLE. A single
value would force a choice that loses half the answer.

**And a phase describes the CASE, not a layer.** Since only ETs are directly phase
related, phase cannot be the axis that groups findings into layers; that is
`dashboard/layer_grouping.py`, which keys on tissue function.

Deliberately NOT guessed: the other 240 findings. ER, ENV, NUT, ES, MR, MB and most of
ED and EI return (), not a plausible number. A wrong phase would describe the wrong
terrain.

Do not read the `terrain-phase:*` tags in e4l.db as a signal: 672 of ~700 tagged
clients are `regenerate`, set by the `e4l:matrix-regulators` rule rather than by a
reading, and only two by a clinician.
"""
from dashboard.terrain_phase import PHASE_CLINICAL_NAMES, phase_name  # noqa: F401

ENERGIZE, REJUVENATE, REGENERATE, CLEANSE, BALANCE = 1, 2, 3, 4, 5

# Ruled per finding. A tuple, because a finding can serve several phases.
_RULED = {
    "ET13": (REGENERATE,),        # Fungal Terrain
    "ET14": (REJUVENATE,),        # Bacterial Terrain
    "ED6": (BALANCE,),            # Heart Driver — sympathetic/parasympathetic
    "EI11": (BALANCE,),           # Bone Marrow - Stomach Integrator — same
    "ES15": (CLEANSE, ENERGIZE),  # Heavy Metals Detox — elimination AND intracellular
}


def phases_for(code):
    """Every phase a finding serves, as a tuple. Empty when Glen has not ruled on it.

    Empty is a real answer and callers must handle it.
    """
    code = str(code or "").strip().upper()
    if not code:
        return ()
    if code in _RULED:
        return _RULED[code]
    if code.startswith("ET"):
        # "Most of the ET terrains ... are linked to particular viral susceptibility
        # patterns which would be Phase 1."
        return (ENERGIZE,)
    return ()


def phase_for(code):
    """The FIRST phase, for a caller that can hold only one. Prefer phases_for:
    ES15 serves two and this hides the second."""
    got = phases_for(code)
    return got[0] if got else None


def clinical_name(phase):
    """'Cleanse' for 4 — the clinical vocabulary, not the client-report R-name."""
    return PHASE_CLINICAL_NAMES.get(phase, "")


def group_by_phase(codes):
    """{phase: [codes]} plus an "unplaced" bucket, order preserved within each.

    A finding serving two phases appears under BOTH.
    """
    out, unplaced = {}, []
    for code in codes or []:
        ps = phases_for(code)
        if not ps:
            unplaced.append(code)
        for p in ps:
            out.setdefault(p, []).append(code)
    return {"by_phase": out, "unplaced": unplaced}
