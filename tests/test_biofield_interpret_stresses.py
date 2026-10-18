from dashboard.biofield_interpret import interpret_stresses


def _c(payload):
    return lambda system, user: payload


def test_extracts_distinct_stress_labels():
    out = interpret_stresses(
        "the stress is liver congestion, also adrenal fatigue, liver congestion again",
        _c('{"stresses": ["Liver congestion", "Adrenal fatigue", "liver congestion"]}'))
    assert out == ["Liver congestion", "Adrenal fatigue"]   # deduped case-insensitively, order kept


def test_empty_transcript_returns_empty():
    assert interpret_stresses("   ", _c('{"stresses": ["x"]}')) == []


def test_handles_garbage_completion():
    assert interpret_stresses("something", _c("not json at all")) == []


def test_family_history_is_kept_and_labelled():
    """Glen, 2026-10-16: family history is a prevention risk factor. Mining must keep
    the 'family' in the label, or grouping cannot tell it from the client's own."""
    from dashboard.biofield_interpret import build_stress_prompt
    from dashboard.biofield_clinical_groups import build_prompt
    assert "'family history of X'" in build_stress_prompt("x")["system"]
    system, _ = build_prompt([{"factor": "family history of diabetes", "kind": "stress"}])
    assert "named 'Family history'" in system


def test_the_clients_own_and_the_familys_are_both_kept():
    import sqlite3
    from dashboard.biofield_stress import add_stress, init_stress_tables
    cx = sqlite3.connect(":memory:")
    init_stress_tables(cx)
    assert add_stress(cx, "a3", "diabetes", source="comm")
    assert add_stress(cx, "a3", "family history of diabetes", source="comm")


def test_spoken_family_history_reaches_the_grouping():
    import sqlite3
    from dashboard.biofield_stress import add_stress, init_stress_tables
    from dashboard.biofield_clinical_groups import factors_for
    cx = sqlite3.connect(":memory:")
    init_stress_tables(cx)
    add_stress(cx, "a3", "Family history of glaucoma", source="voice")
    add_stress(cx, "a3", "grief", source="voice")
    add_stress(cx, "a3", "family history of anything", source="scan")
    assert [f["factor"] for f in factors_for(cx, "a3")] == ["Family history of glaucoma"]


import os as _os  # noqa: E402
import pytest as _pytest  # noqa: E402


@_pytest.mark.skipif(_os.environ.get("BIOFIELD_LIVE_EVAL") != "1",
                     reason="live model check; run with BIOFIELD_LIVE_EVAL=1 and a real key")
def test_live_family_history_extraction_and_grouping():
    """Behaviour, not wording (review round 2). Costs two model calls."""
    from biofield_local_app import openai_json
    from dashboard import biofield_clinical_groups as cg
    text = ("I have diabetes and a cataract. My mother has diabetes too, my son has eczema, "
            "and heart failure runs in our family.")
    labels = [l.lower() for l in interpret_stresses(text, openai_json)]
    assert "diabetes" in labels and "family history of diabetes" in labels
    assert "family history of eczema" in labels and "eczema" not in labels
    assert "family history of heart failure" in labels
    facs = [{"factor": l, "kind": "stress"} for l in labels]
    s, u = cg.build_prompt(facs)
    groups = cg.parse_reply(openai_json(s, u), facs)
    fam = [g for g in groups if g["name"] == "Family history"]
    assert len(fam) == 1
    assert sorted(f["factor"] for f in fam[0]["factors"]) == sorted(
        l for l in labels if l.startswith("family history of"))
