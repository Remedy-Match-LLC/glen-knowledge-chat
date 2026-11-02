"""Molybdenum's Clinical Theory entry names Sulfur Syntropy only (Glen, 2026-11-02).

Thyroid Support v7's panel (#2209) has no molybdenum. Glen ruled in the knowledge tab that
the entry reads "You can find Molybdenum in Sulfur Syntropy.", and "yes" in the platform tab
to the chat's copy matching. Production fixed its own copy the same day.
"""
import json


def _molybdenum():
    d = json.load(open("data/clinical_theory_catalog.json"))
    found = [e for dim in d["dimensions"] for e in dim["entries"] if e.get("slug") == "molybdenum"]
    assert len(found) == 1
    return found[0]


def test_the_description_is_glens_wording():
    assert _molybdenum()["description"] == (
        "Molybdenum is important for endocrine regulation.You can find Molybdenum in "
        "Sulfur Syntropy.")


def test_the_only_remedy_is_sulfur_syntropy():
    assert _molybdenum()["remedies"] == [
        {"name": "Sulfur Syntropy",
         "url": "https://myhealingoasis.com/begin/product/sulfur-syntropy"}]
