from pathlib import Path


HTML = (Path(__file__).resolve().parents[1] / "static" / "order-new.html").read_text()


def test_order_editor_supports_line_reordering():
    assert 'draggable="true"' in HTML
    assert "function wireLineDragging" in HTML
    assert "function moveLine" in HTML
    assert "LINES.splice(from,1)" in HTML


def test_reordered_model_is_the_saved_payload_source():
    assert "function linesPayload(){ return LINES.map" in HTML
    assert "lines: linesPayload()" in HTML
