"""People pages: every box with a literal light background has a dark-mode rule.

Glen, 2026-10-02: "In People: Clients in dark mode, the yellow font is too low contrast to
read." op-nav.js sets dark-mode body text to pale cream (#fdf4d8) and darkens only its own
list of boxes (details, .card, .stage, .qcard). A page box with a literal white background
kept it, so cream text sat on white. Measured in headless Chrome: 1.02 to 1.10 contrast on
four Client boxes and the Members tier pill.
"""
import re
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parent.parent / "static"
NAV = (STATIC / "op-nav.js").read_text()
PAGES = ["console-client.html", "console-crm.html", "console-members.html",
         "console-practitioner-drafts.html", "console-practitioners.html"]
# Darkened by the shared override in op-nav.js, so a page need not repeat them.
SHARED = {"details", ".card", ".stage", ".qcard", "body", "html", "input", "select", "textarea",
          "th", ".pill", "#importResult"}


def _css(page):
    html = (STATIC / page).read_text()
    css = "\n".join(re.findall(r"<style[^>]*>(.*?)</style>", html, flags=re.S))
    return re.sub(r"/\*.*?\*/", "", css, flags=re.S)


def _light(hexcol):
    h = hexcol.lstrip("#")
    h = "".join(c * 2 for c in h) if len(h) == 3 else h
    r, g, b = (int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b > 0.8


def _light_box_selectors(css):
    out = []
    for m in re.finditer(r"([^{}]+)\{([^{}]*)\}", css):
        sel, body = m.group(1).strip(), m.group(2)
        if sel.startswith("@") or "data-theme" in sel:
            continue
        bg = re.search(r"background(?:-color)?:\s*(#[0-9a-fA-F]{3,6})\b", body)
        if bg and _light(bg.group(1)) and "color:" not in re.sub(r"(background|border)-?color:", "", body):
            out += [s.strip() for s in sel.split(",") if " " not in s.strip()]
    # `.stage.done` is covered: the shared `:root[data-theme="dark"] :is(.stage)` (0,3,0)
    # outranks it (0,2,0). Chrome agreed: it is not among the low-contrast boxes.
    return [s for s in dict.fromkeys(out)
            if s not in SHARED and not ({"." + c for c in re.findall(r"\.([\w-]+)", s)} & SHARED)]


def _dark_rules(css):
    return {m.group(1).strip() for m in
            re.finditer(r':root\[data-theme="dark"\]\s+([^{,]+)\{[^}]*background', css)}


@pytest.mark.parametrize("page", PAGES)
def test_every_light_box_has_a_dark_mode_rule(page):
    css = _css(page)
    missing = [s for s in _light_box_selectors(css) if s not in _dark_rules(css)]
    assert not missing, f"{page}: light boxes with no dark-mode rule: {missing}"


def test_the_shared_override_still_sets_cream_text():
    """The premise: if op-nav stops forcing cream text, this guard needs rethinking."""
    assert '#fdf4d8' in NAV and ':root[data-theme="dark"] body{' in NAV


def test_the_check_finds_the_boxes_glen_reported():
    """The control: on the page as it was, the check must have flagged these four."""
    before = re.sub(r':root\[data-theme="dark"\][^{]*\{[^}]*\}', "", _css("console-client.html"))
    found = set(_light_box_selectors(before)) - _dark_rules(before)
    assert {".commerce-card", ".health-item", ".health-item.good", ".health-item.attn"} <= found
