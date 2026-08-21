"""Static layout contract for the client portal scene picker."""
from pathlib import Path


HTML = (Path(__file__).resolve().parents[1] / "static" / "client-portal.html").read_text()


SHELL = (Path(__file__).resolve().parents[1] / "static" / "shell.js").read_text()


def test_scene_picker_is_mounted_in_journey_header_before_theme_toggle():
    assert 'var scenePicker = document.getElementById("elPicker")' in SHELL
    assert 'bar.querySelector(".rm-theme-seg")' in SHELL
    assert "bar.insertBefore(scenePicker, themeControl || null)" in SHELL


def test_scene_picker_is_not_a_bottom_right_floater():
    picker_css = HTML.split(".el-picker{", 1)[1].split("}", 1)[0]
    assert "position:fixed" not in picker_css
    assert "bottom:" not in picker_css
    assert ".portal-header-controls" not in HTML


def test_scene_picker_reserves_space_in_header_without_overlap():
    assert "flex:0 1 132px" in HTML
    assert "#journey-shell .el-picker" in HTML
