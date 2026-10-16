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


with open(_PATH, encoding="utf-8") as _f:
    _INTRO, _SECTIONS = _parse(_f.read())


def guidance(phase):
    """{"title", "intro", "heading", "paragraphs"} for one phase, or None with no valid
    phase. The intro always comes with the section."""
    n = phase_num(phase)
    if not n:
        return None
    heading, paras = _SECTIONS[n]
    return {"title": _TITLE, "intro": list(_INTRO), "heading": heading,
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
    """The phase an intake answer scores: the lowest terrain number ticked, else the
    stored single value, else None."""
    answers = answers or {}
    picks = answers.get("terrain_selections")
    if isinstance(picks, (list, tuple)):
        nums = [n for n in (phase_num(p) for p in picks) if n]
        if nums:
            return min(nums)
    return phase_num(answers.get("terrain"))


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
    for sec in out.get("sections") or []:
        for f in sec.get("fields") or []:
            if f.get("id") == "terrain":
                f["diet_guidance"] = {str(n): guidance(n) for n in range(1, 6)}
    return out
