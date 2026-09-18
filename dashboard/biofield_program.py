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
SPIRIT_SOURCES = ("voice",)
MIND_SOURCES = ("tag", "comm", "manual")
BODY_SOURCES = ("scan",)
STAGES = (("spirit", SPIRIT_SOURCES), ("mind", MIND_SOURCES), ("body", BODY_SOURCES))


def token_of(stress):
    """The cover token: the E4L code for a scan finding, the normalised label
    otherwise. Mirrors the set-cover's own rule, so a spoken "Grief" and a mined
    "Grief" are one token and the second one is suppressed."""
    if (stress.get("source") or "") in BODY_SOURCES:
        return (stress.get("code") or "").strip()
    return _norm(stress.get("label") or "")


def build_program(*, stresses, spirit_layers, cover, functions_of=None, mode=FULL):
    """Three passes in priority order over one shared suppression state.

    `cover(tokens) -> [{"remedy": name, "covers": [tokens]}]` is injected so the real
    set-cover is reused unchanged. `functions_of(token) -> iterable` is only read in
    MINIMUM mode, and a tissue serves SEVERAL functions, so sharing any one of them
    counts as addressed. Returns the stages, what each suppressed and why, and the
    whole program's remedies in order.
    """
    if mode not in (FULL, MINIMUM):
        raise ValueError("mode must be %r or %r" % (FULL, MINIMUM))
    fn = functions_of or (lambda _t: ())

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

        picks = list(cover(pending)) if pending else []
        for p in picks:
            remedy = (p.get("remedy") or "").strip()
            if not remedy:
                continue
            if remedy not in remedies:
                remedies.append(remedy)
            # A pick suppresses everything it covers for every LATER stage.
            for tok in p.get("covers") or []:
                by_token.setdefault(tok, remedy)
                for f in fn(tok) or ():
                    by_function.setdefault(f, remedy)

        out.append({"stage": stage, "picks": picks, "suppressed": suppressed,
                    "pending": pending})

    return {"mode": mode, "stages": out, "remedies": remedies,
            "seed": list(spirit_layers or [])}
