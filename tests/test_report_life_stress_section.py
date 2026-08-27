import pytest
from dashboard import biofield_report_present as pres

REPORT = {"client": {"email": "a@b.com"}, "date": "2026-07-14"}


def test_disabled_returns_empty(monkeypatch):
    monkeypatch.delenv("LIFE_STRESS_ENABLED", raising=False)
    assert pres._life_stress(REPORT) == ""


def test_enabled_lists_hand_tested_essence_benefits_no_raw_stresses(monkeypatch):
    monkeypatch.setenv("LIFE_STRESS_ENABLED", "1")
    report = {"client": {"email": "a@b.com"}, "date": "2026-07-14",
              "stresses": [{"code": "ER3", "name": "Adrenal Stress Marker"}],
              "life_stress_curation": {
                  "slugs": ["mimulus-flower-essence-in-terrain-restore"]}}
    html = pres._life_stress(report)
    assert "Mimulus Flower Essence" in html
    assert "Encourages courage" in html
    assert "tested and selected for you by hand" in html
    assert "ER3" not in html and "Adrenal Stress Marker" not in html


def test_no_hand_tested_curation_means_no_section(monkeypatch):
    monkeypatch.setenv("LIFE_STRESS_ENABLED", "1")
    assert pres._life_stress(REPORT) == ""


def test_curation_present_overrides_auto_pool(monkeypatch):
    monkeypatch.setenv("LIFE_STRESS_ENABLED", "1")

    report = {"client": {"email": "a@b.com"}, "date": "2026-07-14",
              "stresses": [{"code": "ER3", "name": "Adrenal Stress Marker"}],
              "life_stress_curation": {"slugs": ["Forsythia Flower Essence"], "note": "take it"}}
    html = pres._life_stress(report)
    assert "Forsythia Flower Essence" in html
    assert "Mimulus Flower Essence" not in html
    assert "ER3" not in html and "Adrenal Stress Marker" not in html


def test_curation_absent_never_uses_ai_pool(monkeypatch):
    monkeypatch.setenv("LIFE_STRESS_ENABLED", "1")
    assert pres._life_stress(REPORT) == ""


def test_essence_stress_indication_precedes_balancing_essence_benefit(monkeypatch):
    monkeypatch.setenv("LIFE_STRESS_ENABLED", "1")
    report = {
        "layers": [{"head": "Forsythia Flower Essence",
                    "remedy": "Mimulus Flower Essence in Terrain Restore"}],
        "life_stress_curation": {
            "slugs": ["mimulus-flower-essence-in-terrain-restore"]}}
    html = pres._life_stress(report)
    assert "Stress indication &mdash; Forsythia Flower Essence" in html
    assert "Balancing essence &mdash; Mimulus Flower Essence" in html
    assert html.index("Stress indication") < html.index("Balancing essence")


def test_non_essence_stress_does_not_get_an_indication_block(monkeypatch):
    monkeypatch.setenv("LIFE_STRESS_ENABLED", "1")
    report = {
        "layers": [{"head": "Adrenal Stress Marker",
                    "remedy": "Mimulus Flower Essence in Terrain Restore"}],
        "life_stress_curation": {
            "slugs": ["mimulus-flower-essence-in-terrain-restore"]}}
    html = pres._life_stress(report)
    assert "Stress indication" not in html
    assert "Balancing essence &mdash; Mimulus Flower Essence" in html
