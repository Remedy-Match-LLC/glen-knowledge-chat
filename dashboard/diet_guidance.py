"""Diet guidance by terrain phase, as Glen approved it on 16 October 2026.

The text lives in data/diet-guidance.md, copied verbatim from the rnd source
(rnd/review/2026-10-16-client-diet-guidance/client-diet-guidance-draft-v1.md, from
"## Eating for your terrain" to the end). Any wording change goes back to Glen through
rnd; tests/test_diet_guidance.py pins the file's hash so a silent edit fails.

A client sees the intro (every caution line) and only their own phase's section, never
another phase's. With no phase, nothing shows.
"""
import html
import os
import re

from dashboard.terrain_phase import phase_display, phase_num

_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "data", "diet-guidance.md")
_TITLE = "Eating for your terrain"


def _parse(text):
    """(intro paragraphs, {phase: (heading, paragraphs)}). Raises on a file that does not
    have the approved shape, so a broken copy fails loudly in tests, not quietly live."""
    blocks = re.split(r"^### ", text, flags=re.M)
    head, rest = blocks[0], blocks[1:]
    if not head.startswith(f"## {_TITLE}\n"):
        raise ValueError("diet guidance: the title line is missing")
    intro = [p.strip() for p in head.split("\n", 1)[1].split("\n\n") if p.strip()]
    sections = {}
    for b in rest:
        heading, _, body = b.partition("\n")
        heading = heading.strip()
        n = next((k for k in range(1, 6) if phase_display(k) == heading), None)
        if n is None or n in sections:
            raise ValueError(f"diet guidance: unexpected heading {heading!r}")
        sections[n] = (heading, [p.strip() for p in body.split("\n\n") if p.strip()])
    if sorted(sections) != [1, 2, 3, 4, 5] or not intro:
        raise ValueError("diet guidance: expected an intro and five phase sections")
    return intro, sections


_LOADED = None


def _load():
    """(intro, sections), read once. A missing or malformed file logs and shows nothing,
    so it can never take down the intake form or the report around it (review round 2).
    tests/test_diet_guidance.py fails loudly on the same file instead."""
    global _LOADED
    if _LOADED is None:
        try:
            with open(_PATH, encoding="utf-8") as f:
                _LOADED = _parse(f.read())
        except Exception as e:  # noqa: BLE001
            print(f"[diet-guidance] not loaded: {e!r}", flush=True)
            _LOADED = ([], {})
    return _LOADED


def guidance(phase):
    """{"title", "intro", "heading", "paragraphs"} for one phase, or None with no valid
    phase. The intro always comes with the section."""
    n = phase_num(phase)
    intro, sections = _load()
    if not n or n not in sections or not intro:
        return None
    heading, paras = sections[n]
    return {"title": _TITLE, "intro": list(intro), "heading": heading,
            "paragraphs": list(paras), "phase": n}


def guidance_html(phase):
    """The guidance as HTML, each sentence its own paragraph as in the source, or ""."""
    g = guidance(phase)
    if not g:
        return ""
    e = html.escape
    return (f'<section class="diet-guidance"><h2>{e(g["title"])}</h2>'
            + "".join(f"<p>{e(p)}</p>" for p in g["intro"])
            + f'<h3>{e(g["heading"])}</h3>'
            + "".join(f"<p>{e(p)}</p>" for p in g["paragraphs"])
            + "</section>")


def intake_phase(answers):
    """The phase an intake answer scores. Every writer stores `terrain` as the lowest
    number ticked, and an edit can leave older `terrain_selections` behind (review
    round 1), so `terrain` wins; the lowest selection is used only when it is empty."""
    answers = answers or {}
    n = phase_num(answers.get("terrain"))
    if n:
        return n
    picks = answers.get("terrain_selections")
    if isinstance(picks, (list, tuple)):
        nums = [m for m in (phase_num(p) for p in picks) if m]
        if nums:
            return min(nums)
    return None


def report_fallback_answers(row):
    """The intake answers the report may fall back to: a submitted intake only, never a
    draft and never an imported stub. A draft is unfinished, and the public form saves
    one for any typed address (review round 1)."""
    row = row or {}
    answers = row.get("answers")
    if row.get("status") != "submitted" or not isinstance(answers, dict):
        return None
    if answers.get("_external"):
        return None
    return answers


def report_phase(tested_phase, intake_answers=None):
    """Glen, 2026-10-16: the report uses the tested phase; the intake answer is the
    fallback."""
    return phase_num(tested_phase) or intake_phase(intake_answers)


def form_with_guidance(form):
    """A copy of the intake form whose terrain field carries every phase's guidance, keyed
    "1".."5", so the page can show the one section the client's own answer scores as soon
    as they answer. The form itself is never changed."""
    import copy
    out = copy.deepcopy(form)
    by_phase = {str(n): g for n, g in ((n, guidance(n)) for n in range(1, 6)) if g}
    if len(by_phase) != 5:
        return out
    for sec in out.get("sections") or []:
        for f in sec.get("fields") or []:
            if f.get("id") == "terrain":
                f["diet_guidance"] = by_phase
    return out
