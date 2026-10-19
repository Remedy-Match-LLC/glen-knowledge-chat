"""Move balances to: the layer's heading beside its number (Glen, 2026-10-19)."""
from dashboard.biofield_report_html import _move_target_label, _render_chain_cards


def test_the_label_names_the_layer():
    assert _move_target_label({"layer": 3, "head": "Liver and gallbladder"}) == \
        "Layer 3: Liver and gallbladder"


def test_a_layer_with_no_heading_shows_its_number():
    assert _move_target_label({"layer": 2, "head": "  "}) == "Layer 2"


def test_a_long_heading_is_cut_at_a_word_with_an_ellipsis():
    label = _move_target_label({"layer": 1, "head": "Connective tissue autoimmunity "
                                "with Sjögren's syndrome and dry eye"})
    assert label == "Layer 1: Connective tissue autoimmunity with…"


def test_the_cards_list_the_other_layers_by_name_and_escape_them():
    report = {"layers": [
        {"rid": 1, "layer": 1, "stored_layer": 1, "head": "Gut", "remedy": "Microbiome"},
        {"rid": 2, "layer": 2, "stored_layer": 5, "head": "<b>Liver</b>", "remedy": "TMG"}]}
    html = _render_chain_cards(report, {})
    assert "<option value='5'>Layer 2: &lt;b&gt;Liver&lt;/b&gt;</option>" in html
    assert "<option value='1'>Layer 1: Gut</option>" in html
