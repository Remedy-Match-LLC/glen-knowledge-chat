"""The journey shell must not show a client an em dash.

Glen's rule is no em dashes in anything a client reads, and `static/shell.js` renders
on nearly every page. Found 2026-09-16 in a screenshot of the live membership page:
the nav trail read "Wellness — Remedies — Healing — Oasis". A grep of the page's own
HTML found nothing, because the trail is built in JavaScript.

Three visible strings carried one: the trail separator, the "My Path" heading, and
the empty-offers line. Comments are exempt, since no client reads them.
"""
import pathlib
import re

SHELL = pathlib.Path(__file__).resolve().parent.parent / "static" / "shell.js"
EM_DASH = "—"


def _without_line_comments(src):
    """Drop `//` comments so the check sees only code and string literals.

    Crude on purpose. A `//` inside a string would be stripped too, which can only
    make this test MISS a regression, never invent one. That is the safe direction
    for a guard whose job is to fail loudly on real copy.
    """
    out = []
    for line in src.splitlines():
        i = line.find("//")
        out.append(line if i == -1 else line[:i])
    return "\n".join(out)


def test_shell_js_shows_no_em_dash():
    src = _without_line_comments(SHELL.read_text(encoding="utf-8"))
    hits = [(n, ln.strip()) for n, ln in enumerate(src.splitlines(), 1)
            if EM_DASH in ln]
    assert not hits, (
        "an em dash is rendered to clients by shell.js. Use a middle dot for a "
        "separator, or a comma, colon or full stop in prose:\n"
        + "\n".join(f"  line {n}: {ln}" for n, ln in hits))


def test_the_three_corrected_strings_stay_corrected():
    """A control, so the guard above cannot be satisfied by deleting the copy."""
    src = SHELL.read_text(encoding="utf-8")
    assert '"My Path: this visit"' in src
    assert "No offers yet. Complete a step to earn one." in src
    assert "\\u00b7" in src or "·" in src, "the trail separator is gone"


def test_the_guard_itself_catches_an_em_dash_in_code():
    """The regex-free check is simple enough to be wrong, so it is tested."""
    assert EM_DASH in _without_line_comments('var a = "x — y";')
    assert EM_DASH not in _without_line_comments("// a comment — here")
    assert re.search(r"\w", _without_line_comments("var a = 1; // note"))
