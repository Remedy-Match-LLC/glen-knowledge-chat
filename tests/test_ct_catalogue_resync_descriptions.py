"""Seven Clinical Theory descriptions match the site (Glen "yes", production, 2026-10-08).

Spec: production/05 Formulations/_store-updates/2026-10-08-ct-catalogue-resync.md.
The FDA disclaimer leaves three entries (Vitreous Humor becomes empty), the stray
"Biochemistry:" label leaves Phase 5, Iron and B17 name the Syntropy products, and SAMe
gains its closing sentence. Only `description` changed; unserved snapshot fields are left alone.
"""
import json

BANNED = ("Food and Drug", "Iron Synergy", "B17 Synergy")


def _dims():
    return {d["key"]: {e["slug"]: e for e in d["entries"]}
            for d in json.load(open("data/clinical_theory_catalog.json"))["dimensions"]}


def test_no_description_holds_the_disclaimer_or_old_names():
    for key, entries in _dims().items():
        for slug, e in entries.items():
            for phrase in BANNED:
                assert phrase not in (e.get("description") or ""), (key, slug, phrase)


def test_the_seven_descriptions():
    d = _dims()
    assert d["organs"]["vitreous-humor"]["description"] == ""
    assert d["meridians"]["triple-warmer-meridian"]["description"].endswith(
        "Sleep Syntropy for Endocrine Meridian support.")
    chem = d["chemistry"]
    assert chem["phase-5-balance"]["description"] == (
        "Phase 5 deals with balancing life stresses as well as hormonal regulation.")
    assert chem["iron"]["description"].endswith("Iron Syntropy is the ideal source.")
    assert chem["vitamin-b17-amygdalin"]["description"].endswith(
        "B17 Syntropy provides a comprehensive Amygdalin support system.")
    assert chem["s-adenosyl-methionine"]["description"].endswith(
        "beneficial SAMe form.That way the dosage of SAMe also keeps working longer and better.")
    assert chem["thyroxine"]["description"].endswith("along with synergistic botanicals.")
