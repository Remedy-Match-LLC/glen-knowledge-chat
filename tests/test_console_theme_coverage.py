from pathlib import Path


STATIC = Path(__file__).parents[1] / "static"


def test_every_console_page_uses_shared_theme_navigation():
    """A console page without op-nav has no shared theme controller or palette."""
    missing = []
    for page in sorted(STATIC.glob("console*.html")):
        if "/static/op-nav.js" not in page.read_text():
            missing.append(page.name)
    assert not missing, f"console pages missing shared theme navigation: {missing}"


def test_shared_theme_covers_legacy_and_semantic_console_palettes():
    nav = (STATIC / "op-nav.js").read_text()
    assert "data-console-page" in nav
    assert ':root[data-theme="dark"] body' in nav
    assert ':root[data-theme="light"] body' in nav
    for token in ("--card", "--card2", "--line", "--fg", "--mut"):
        assert token in nav
