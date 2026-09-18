"""One biofield balancing program from all three stress sources, in Glen's order.

Glen, 2026-09-17: *"Priority sequence: 1. Spirit (transcript based layers, stresses,
and remedies) 2. Mind (history-based symptoms and conditions) - if not already
addressed by relevant remedies from step 1. 3. Body (scan patterns) - if not already
addressed by relevant remedies from 1 & 2."*

Two buttons, also his: *"full (narrow, bigger program) - vs minimum (wide, minimal
program)"*.

- FULL suppresses a later stress only when a placed remedy covers that exact stress.
  It is what the coverage data actually says, so more survives.
- MINIMUM also suppresses it when its FUNCTION is already addressed, via the
  tissue-to-function map he approved on 2026-09-16. It suppresses more.

Pure: the caller supplies the stresses, the transcript's layers, the cover call and
the function lookup. No database, no network, no writes. The apply step lives in the
route, behind a confirm, because the causal chain is the intake's core artifact.
"""
from dashboard.biofield_stress import _norm

FULL = "full"
MINIMUM = "minimum"

# Which stress sources belong to each of Glen's three. 'chain' is left out on
# purpose: it came off the causal chain, so it is already placed, not a finding.
# WHOSE MIND, AND WHOSE SIGNAL. Glen, 2026-09-18:
#
#   "The mind of the patient identifies symptoms and the mind of the physician
#    identifies diagnoses. The mind is dominantly visual, associated with Iridium.
#    The manual biofield stress responses and balancing remedies in remote biofield
#    analysis come from coherent communication between the spirit of the tester as
#    surrogate and the spirit of the client, based on meaning associated with Gold.
#    The frequency patterns in the client's voice come directly from the client's own
#    body, associated with Rhodium."
#
# So each stage is defined by WHERE THE SIGNAL CAME FROM, not by how it was typed:
#
#   SPIRIT  Gold      meaning, surrogate to client. The transcript's layers AND the
#                     MANUAL biofield responses, which are the tester's spirit reading
#                     the client's, not the practitioner's opinion.
#   MIND    Iridium   visual. Symptoms the patient reports and diagnoses the physician
#                     identifies: the Clinical Summary, plus what is mined from tags
#                     and communications.
#   BODY    Rhodium   frequency patterns from the client's own body: the scan.
#
# CORRECTED 2026-09-18 on his instruction. 'manual' sat in MIND, and a test was named
# test_glens_own_manual_stresses_ride_with_mind. A manual biofield response is a
# surrogate reading, which is Spirit. That misplacement is also why "Mind is the
# Clinical Summary" looked like it contradicted an earlier ruling: it never did, the
# manual rows were simply in the wrong stage.
SPIRIT_SOURCES = ("voice", "manual")
BODY_SOURCES = ("scan",)

# Mind keeps what is MINED from the client's history, and gains the Clinical Summary.
# Before 2026-09-18 it was these sources alone, and on his test that meant two 'manual'
# rows -- an E4L code and a flower essence -- so Mind reported "nothing here" while six
# checked conditions sat on the page above it. Those conditions live in
# biofield_clinical_selection and nothing here ever read them.
MIND_SOURCES = ("tag", "comm")
STAGES = (("spirit", SPIRIT_SOURCES), ("mind", MIND_SOURCES), ("body", BODY_SOURCES))


def token_of(stress):
    """The cover token: the E4L code for a scan finding, the normalised label
    otherwise. Mirrors the set-cover's own rule, so a spoken "Grief" and a mined
    "Grief" are one token and the second one is suppressed."""
    if (stress.get("source") or "") in BODY_SOURCES:
        return (stress.get("code") or "").strip()
    return _norm(stress.get("label") or "")


def build_program(*, stresses, spirit_layers, cover, functions_of=None, mode=FULL,
                  mind_items=None, covers_of=None):
    """Three passes in priority order over one shared suppression state.

    `cover(tokens) -> [{"remedy": name, "covers": [tokens]}]` is injected so the real
    set-cover is reused unchanged. `functions_of(token) -> iterable` is only read in
    MINIMUM mode, and a tissue serves SEVERAL functions, so sharing any one of them
    counts as addressed. Returns the stages, what each suppressed and why, and the
    whole program's remedies in order.

    `mind_items` is [{"label": str, "remedies": [str]}] -- the CHECKED Clinical Summary
    factors and the remedies checked against each. Glen's own remedy is placed, never
    substituted: the set-cover chooses for Spirit and Body, but here he has already
    chosen. A factor he checked with no remedy yet is reported in `uncovered` rather
    than dropped, because a condition with nothing against it is the thing he most needs
    to see.

    Suppression still applies to Mind. If Spirit already covers a factor, MINIMUM drops
    it, which is the entire point of the Minimum button.

    `covers_of(remedy) -> iterable` gives the E4L codes a Mind remedy already covers, so
    it suppresses the BODY findings it addresses. Without it a Mind pick only ever covers
    the condition's own label, and Glen's rule -- "Body (scan patterns) if not already
    addressed by relevant remedies from 1 & 2" -- silently does nothing, because a scan
    token is an E4L code and a condition token is a normalised label. They never match by
    accident. The Spirit seed already unions the same coverage in; this is the same step
    for Mind.
    """
    if mode not in (FULL, MINIMUM):
        raise ValueError("mode must be %r or %r" % (FULL, MINIMUM))
    fn = functions_of or (lambda _t: ())
    cov = covers_of or (lambda _r: ())

    # What the transcript's own layers already address. They lead the program.
    by_token, by_function, remedies = {}, {}, []
    for layer in spirit_layers or []:
        remedy = (layer.get("remedy") or "").strip()
        if not remedy:
            continue
        if remedy not in remedies:
            remedies.append(remedy)
        for tok in layer.get("covers") or []:
            by_token.setdefault(tok, remedy)
            for f in fn(tok) or ():
                by_function.setdefault(f, remedy)

    out = []

    def _absorb(remedy, toks):
        """Record a placed remedy so it suppresses these tokens for LATER stages."""
        if remedy not in remedies:
            remedies.append(remedy)
        for tok in toks:
            by_token.setdefault(tok, remedy)
            for f in fn(tok) or ():
                by_function.setdefault(f, remedy)

    mind_uncovered = []

    for stage, sources in STAGES:
        pending, suppressed = [], []
        for s in stresses or []:
            if (s.get("source") or "") not in sources:
                continue
            if (s.get("balance") or "optional") != "required":
                continue          # only a required stress is balanced
            tok = token_of(s)
            if not tok:
                continue
            if tok in by_token:
                suppressed.append({"label": s.get("label") or tok, "token": tok,
                                   "by": by_token[tok], "on": tok})
                continue
            shared = next((f for f in fn(tok) or () if f in by_function), None)
            if mode == MINIMUM and shared:
                suppressed.append({"label": s.get("label") or tok, "token": tok,
                                   "by": by_function[shared], "on": shared})
                continue
            pending.append(tok)

        picks = [] if stage == "mind" else (list(cover(pending)) if pending else [])
        for p in picks:
            remedy = (p.get("remedy") or "").strip()
            if not remedy:
                continue
            _absorb(remedy, p.get("covers") or [])

        if stage == "mind":
            # His checked factors LEAD the stage, and the stage runs AFTER Spirit.
            #
            # These were first built before the loop, so they could suppress later
            # stages. That also made them suppress SPIRIT, which runs after them, and a
            # manual surrogate reading was being dropped in favour of a checked
            # condition. Glen's order is Spirit, Mind, Body; Mind cannot pre-empt Spirit.
            clin_picks, clin_suppressed = [], []
            for item in mind_items or []:
                label = (item.get("label") or "").strip()
                if not label:
                    continue
                ctok = _norm(label)
                chosen = [r.strip() for r in (item.get("remedies") or [])
                          if (r or "").strip()]
                if ctok in by_token:
                    clin_suppressed.append({"label": label, "token": ctok,
                                            "by": by_token[ctok], "on": ctok})
                    continue
                cshared = next((f for f in fn(ctok) or () if f in by_function), None)
                if mode == MINIMUM and cshared:
                    clin_suppressed.append({"label": label, "token": ctok,
                                            "by": by_function[cshared], "on": cshared})
                    continue
                if not chosen:
                    mind_uncovered.append({"label": label, "token": ctok})
                    continue
                for remedy in chosen:
                    toks = [ctok] + [c for c in cov(remedy) or () if c]
                    clin_picks.append({"remedy": remedy, "covers": sorted(set(toks))})
                    _absorb(remedy, toks)
            # Re-run the stress pass now that his picks are absorbed, so a tag saying the
            # same thing is suppressed rather than placed a second time.
            pending = [tok for tok in pending if tok not in by_token]
            picks = clin_picks + (list(cover(pending)) if pending else [])
            suppressed = clin_suppressed + suppressed
            # Absorb the cover's picks too. clin_picks were absorbed as they were built;
            # these were not, and skipping them meant a Mind remedy chosen by the cover
            # never suppressed anything in Body.
            for cp in picks[len(clin_picks):]:
                remedy = (cp.get("remedy") or "").strip()
                if remedy:
                    _absorb(remedy, cp.get("covers") or [])
        out.append({"stage": stage, "picks": picks, "suppressed": suppressed,
                    "pending": pending,
                    "uncovered": mind_uncovered if stage == "mind" else []})

    return {"mode": mode, "stages": out, "remedies": remedies,
            "seed": list(spirit_layers or [])}
