"""Glen, 2026-09-18: "'Combine another row...' covers most of Stress pattern (head and
tail)." The combine picker sized itself to its longest condition label and squeezed
the stress field to 69px on his own intake. Measured in headless Chrome after the fix:
482px at a 1280px window, 427px at 1000px.

This pins the two rules that make that true, read from the page the app serves."""
import re

import pytest

from biofield_local_app import create_app


@pytest.fixture(autouse=True)
def _no_gate(monkeypatch):
    monkeypatch.delenv("CONSOLE_SECRET", raising=False)
    import dashboard as _d; monkeypatch.setattr(_d, "CONSOLE_SECRET", "", raising=False)


def _rule(css, selector):
    m = re.search(re.escape(selector) + r"\{([^}]*)\}", css)
    return m.group(1) if m else ""


def test_stress_field_keeps_its_width_beside_the_combine_picker(tmp_path):
    profile = {"email": "s@x.com", "conditions": "Adrenal Fatigue; Suspect of Glaucoma"}
    app = create_app(str(tmp_path / "chat_log.db"), fetch_profile=lambda e: profile,
                     scan_lookup=lambda e: {})
    c = app.test_client()
    tid = c.post("/author/new").headers["Location"].rstrip("/").split("/")[-1]
    c.post(f"/author/{tid}/header", json={"name": "S", "email": "s@x.com"})
    page = c.get(f"/author/{tid}").get_data(as_text=True)
    assert "clinical-combine-pick" in page, "the combine picker must render for this test to mean anything"
    css = " ".join(re.findall(r"<style[^>]*>(.*?)</style>", page, re.S))

    assert "min-width:260px" in _rule(css, ".clinical-stress-label")
    assert "flex-wrap:wrap" in _rule(css, ".clinical-stress-row")
    assert "max-width:240px" in _rule(css, ".clinical-combine-pick")
