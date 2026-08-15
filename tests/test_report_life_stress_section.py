import pytest
from dashboard import biofield_report_present as pres

REPORT = {"client": {"email": "a@b.com"}, "date": "2026-07-14"}


def test_disabled_returns_empty(monkeypatch):
    monkeypatch.delenv("LIFE_STRESS_ENABLED", raising=False)
    assert pres._life_stress(REPORT) == ""


def test_enabled_still_omits_ai_matched_essences(monkeypatch):
    monkeypatch.setenv("LIFE_STRESS_ENABLED", "1")
    report = {"client": {"email": "a@b.com"}, "date": "2026-07-14",
              "stresses": [{"code": "ER3", "name": "Adrenal Stress Marker"}]}
    html = pres._life_stress(report)
    assert html == ""


def test_rendered_report_has_no_supportive_life_stress_section(monkeypatch):
    monkeypatch.setenv("LIFE_STRESS_ENABLED", "1")
    html = pres.render_present(REPORT, "Life Stress narrative belongs here.")
    assert "Supportive Life Stress Essences" not in html


def test_never_raises_on_bad_recommend(monkeypatch):
    monkeypatch.setenv("LIFE_STRESS_ENABLED", "1")
    assert pres._life_stress(REPORT) == ""


def test_curation_present_overrides_auto_pool(monkeypatch):
    monkeypatch.setenv("LIFE_STRESS_ENABLED", "1")

    def _spy(email, day):
        return {"label": "Life Stress", "patterns": [],
                "items": [{"name": "Mimulus Flower Essence", "url": "", "note": "auto-pool pick"}]}

    report = {"client": {"email": "a@b.com"}, "date": "2026-07-14",
              "stresses": [{"code": "ER3", "name": "Adrenal Stress Marker"}],
              "life_stress_curation": {"slugs": ["Forsythia Flower Essence"], "note": "take it"}}
    html = pres._life_stress(report)
    assert html == ""


def test_curation_absent_keeps_auto_pool(monkeypatch):
    monkeypatch.setenv("LIFE_STRESS_ENABLED", "1")

    def _spy(email, day):
        return {"label": "Life Stress", "patterns": [],
                "items": [{"name": "Mimulus Flower Essence", "url": "", "note": "auto-pool pick"}]}

    report = {"client": {"email": "a@b.com"}, "date": "2026-07-14"}
    html = pres._life_stress(report)
    assert html == ""
