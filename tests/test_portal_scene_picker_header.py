"""Static layout contract for the client portal scene picker."""
from pathlib import Path


HTML = (Path(__file__).resolve().parents[1] / "static" / "client-portal.html").read_text()


def test_scene_picker_shares_header_controls_with_theme_toggle():
    controls = HTML.split('<div class="portal-header-controls">', 1)[1].split("</div>", 2)
    block = "</div>".join(controls[:2])
    assert 'id="elPicker"' in block
    assert 'id="themeToggle"' in block


def test_scene_picker_is_not_a_bottom_right_floater():
    picker_css = HTML.split(".el-picker{", 1)[1].split("}", 1)[0]
    assert "position:fixed" not in picker_css
    assert "bottom:" not in picker_css
    assert ".portal-header-controls{position:fixed;top:14px;right:14px" in HTML


def test_header_controls_reserve_separate_space_without_overlap():
    assert ".portal-header-controls .theme-toggle{position:static;flex:0 0 auto" in HTML
    assert ".portal-header-controls .rm-theme-seg-btn" in HTML
    assert "flex:0 1 132px" in HTML
    assert "max-width:calc(100vw - 28px)" in HTML
