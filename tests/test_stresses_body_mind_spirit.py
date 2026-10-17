"""Stresses by layer is grouped Body, Mind, Spirit (Glen, 2026-10-16).

"The list needs to be categorized: 1. Body Energetics (e4l findings) 2. Mind
Observations (symptoms, conditions), and 3. Spirit (Biofield findings: Terrain phase)".
"""
from dashboard.biofield_report_html import render_stress_panel, stress_group


def _s(i, source, label, balanced=False):
    return {"id": i, "source": source, "label": label, "code": "", "balanced": balanced,
            "balance": "required"}


def test_each_source_has_glens_group():
    assert stress_group("scan") == "body"
    for src in ("tag", "comm", "paste", "clinical"):
        assert stress_group(src) == "mind", src
    for src in ("voice", "manual", "chain"):
        assert stress_group(src) == "spirit", src


def test_a_layer_lists_body_then_mind_then_spirit():
    html = render_stress_panel({"by_layer": [{"layer": 1, "head": "Liver", "stresses": [
        _s(1, "voice", "Grief"), _s(2, "comm", "Insomnia"), _s(3, "scan", "Toxicity")]}],
        "unassigned": []})
    body = html.index("Body Energetics")
    mind = html.index("Mind Observations")
    spirit = html.index("Spirit <span")
    assert body < html.index("Toxicity") < mind < html.index("Insomnia") < spirit \
        < html.index("Grief")


def test_an_empty_group_is_not_shown():
    html = render_stress_panel({"by_layer": [{"layer": 1, "stresses": [
        _s(1, "scan", "Toxicity")]}], "unassigned": [_s(2, "tag", "Fatigue")]})
    assert html.count("Body Energetics") == 1 and html.count("Mind Observations") == 1
    assert "Spirit <span" not in html
