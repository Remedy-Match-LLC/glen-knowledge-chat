"""A product intro keeps its line breaks on the page.

renderIntroBody() sets the intro with textContent, and `.sp-text` has no white-space rule,
so an intro written as three paragraphs showed as one. Found 2026-10-02 by R and D: the
approved Mithreal copy is a study summary, a source line and a caution, one per line.

Only the intro gets the rule. 248 descriptions carry scraped line breaks that nobody has
reviewed, so `.sp-text` itself stays as it is.
"""
import re
from pathlib import Path

PAGE = (Path(__file__).parents[1] / "static" / "begin-product.html").read_text()


def _css():
    css = "\n".join(re.findall(r"<style[^>]*>(.*?)</style>", PAGE, flags=re.S))
    return re.sub(r"/\*.*?\*/", "", css, flags=re.S)


def _render_intro_fn():
    m = re.search(r"function renderIntroBody\(body\)\{(.*?)\n    \}", PAGE, flags=re.S)
    assert m, "renderIntroBody not found"
    return re.sub(r"//[^\n]*", "", m.group(1))


def test_intro_paragraph_carries_the_intro_class():
    assert "p.className = 'sp-text sp-intro';" in _render_intro_fn()


def test_intro_class_keeps_line_breaks():
    rule = re.search(r"\.sp-intro\{([^}]*)\}", _css())
    assert rule, "no rule for .sp-intro"
    assert "white-space:pre-line" in rule.group(1).replace(" ", "")


def test_shared_text_class_is_unchanged():
    rule = re.search(r"\.sp-text\{([^}]*)\}", _css())
    assert rule and "white-space" not in rule.group(1)
