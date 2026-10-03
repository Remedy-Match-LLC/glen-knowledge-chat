"""The AI prompts describe Glen as he describes himself.

He is retired from licensed practice. Three prompts called him a "naturopathic physician"
(found by R and D, 2026-10-02). Glen, 2026-10-02: "I tend to say Naturopathic Optometrist,
or Doctor of Optometry and Natural Medicine."
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_no_ai_prompt_calls_glen_a_naturopathic_physician():
    for rel in ("app.py", "dashboard/portal_concierge.py"):
        assert "naturopathic physician" not in (ROOT / rel).read_text().lower(), rel


def test_each_prompt_names_him_a_naturopathic_optometrist():
    import dashboard.portal_concierge as pc
    assert "naturopathic optometrist" in pc.system_prompt({})
    app_src = (ROOT / "app.py").read_text()
    for anchor in ("_REMEDY_MATCH_SYSTEM = (", "_CONCIERGE_SYSTEM = ("):
        block = app_src[app_src.index(anchor):app_src.index(anchor) + 400]
        assert "(naturopathic optometrist, Hilo" in block, anchor
