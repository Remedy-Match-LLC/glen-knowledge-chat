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


# Round 1 of the review: with no `intro`, the page falls back to the description's first
# sentence. 170 of those fallbacks carry scraped line breaks nobody reviewed, and the new
# rule would have shown them. The fallback is flattened so only a written intro keeps breaks.

def _page_intro(monkeypatch, tmp_path, product):
    import importlib
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SALES_PAGES_ENABLED", "true")
    monkeypatch.setenv("SALES_PAGES_AI_COPY", "false")
    import app as appmod
    importlib.reload(appmod)
    slug = next(iter(appmod._PRODUCTS["products"].keys()))
    p = dict(appmod._PRODUCTS["products"][slug])
    p.pop("intro", None)
    p.update(product)
    monkeypatch.setitem(appmod._PRODUCTS["products"], slug, p)
    monkeypatch.setattr(appmod, "_product_card", lambda prod: {"description": prod.get("description", ""),
                                                               "ingredients": [], "benefits": []})
    data = appmod.app.test_client().get(f"/begin/product-page-data/{slug}").get_json()
    return {s["id"]: s for s in data["sections"]}["intro"]["body"]


def test_a_fallback_intro_from_the_description_is_flattened(monkeypatch, tmp_path):
    body = _page_intro(monkeypatch, tmp_path, {"description": "Line one\r\n- bullet\n\nline three. Second sentence."})
    assert body == "Line one - bullet line three"


def test_a_written_intro_keeps_its_line_breaks(monkeypatch, tmp_path):
    """The control."""
    body = _page_intro(monkeypatch, tmp_path, {"intro": "Para one.\nPara two.", "description": "x. y."})
    assert body == "Para one.\nPara two."
