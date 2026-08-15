"""Increment 2: verbal-notes + narrative store, the Glen-voice prompt, and a
generate function with an injectable LLM (no live API call in tests)."""
import sqlite3
import dashboard.biofield_narrative as narrative_mod
from dashboard.biofield_narrative import (
    init_notes_tables, get_notes, save_notes, get_narrative, save_narrative,
    build_narrative_prompt, generate_narrative,
    get_video_script, save_video_script, build_video_script_prompt, generate_video_script,
    get_notes_updated, fmt_saved_hst,
)


def _report():
    return {
        "test_id": "10", "client": {"name": "Lewis Zardo", "email": "lz@x.com"},
        "date": "2026-06-01",
        "layers": [
            {"layer": 1, "head": "Night", "most_affected": "Night",
             "remedy": "TMG Powder", "dosage": "1 scoop", "frequency": "daily", "timing": "at night"},
            {"layer": 2, "head": "Acid", "most_affected": "Liver",
             "remedy": "Sterol Max", "dosage": "3 caps", "frequency": "daily", "timing": "with food"},
        ],
        "schedule": {"slots": [], "entries": []},
    }


def test_notes_roundtrip(tmp_path):
    db = str(tmp_path / "chat_log.db")
    cx = sqlite3.connect(db)
    init_notes_tables(cx)
    assert get_notes(cx, "10") == ""
    save_notes(cx, "10", "kidney felt weak; mercury history")
    assert get_notes(cx, "10") == "kidney felt weak; mercury history"
    save_notes(cx, "10", "updated note")
    assert get_notes(cx, "10") == "updated note"


def test_notes_updated_tracks_last_save(tmp_path):
    db = str(tmp_path / "chat_log.db")
    cx = sqlite3.connect(db)
    init_notes_tables(cx)
    # never saved -> no timestamp
    assert get_notes_updated(cx, "10") == ""
    # save returns a UTC 'Z' timestamp that get_notes_updated reads back
    ts = save_notes(cx, "10", "first pass")
    assert ts.endswith("Z")
    assert get_notes_updated(cx, "10") == ts


def test_fmt_saved_hst_converts_utc_to_hst():
    # 22:14 UTC minus 10h = 12:14 PM HST, same calendar day
    assert fmt_saved_hst("2026-07-10T22:14:03Z") == "Jul 10, 2026 · 12:14 PM HST"
    # crosses midnight backward: 05:30 UTC -> 19:30 (7:30 PM) prior day HST
    assert fmt_saved_hst("2026-07-10T05:30:00Z") == "Jul 9, 2026 · 7:30 PM HST"
    # midnight HST reads as 12:00 AM, not 0:00
    assert fmt_saved_hst("2026-07-10T10:00:00Z") == "Jul 10, 2026 · 12:00 AM HST"
    # empty / unparseable -> "" so the caller shows nothing
    assert fmt_saved_hst("") == ""
    assert fmt_saved_hst("not-a-date") == ""


def test_narrative_roundtrip(tmp_path):
    db = str(tmp_path / "chat_log.db")
    cx = sqlite3.connect(db)
    init_notes_tables(cx)
    assert get_narrative(cx, "10") == ""
    save_narrative(cx, "10", "Aloha Lewis,")
    assert get_narrative(cx, "10") == "Aloha Lewis,"


def test_prompt_carries_voice_rules_layers_and_notes():
    report = {**_report(), "phase": 4, "location": "Liver"}
    p = build_narrative_prompt(report, "kidney felt weak; mercury history")
    sys, usr = p["system"], p["user"]
    # voice rules
    assert "Aloha" in sys
    assert "Dr. Glen & Rae" in sys
    assert "observation" in sys.lower()
    # voice: plain and grounded, no literary flourish
    assert "metaphor" in sys.lower()
    # the chain, top-down, with remedies
    assert usr.index("Night") < usr.index("Acid")
    assert "TMG Powder" in usr and "Sterol Max" in usr
    # the verbal notes are handed to the model
    assert "kidney felt weak; mercury history" in usr
    assert "Lewis Zardo" in usr
    assert "first paragraph" in sys
    assert "Phase 4, Cleanse" in usr
    assert "located this phase at Liver" in usr
    assert "If you tend to be highly sensitive or reactive" in sys
    assert "introduce each layer or each remedy one at a time" in sys
    assert "visualize the desired healing effects" in sys
    assert "portal chat interface" in sys
    assert "do not need to absorb every detail" not in sys


def test_prompt_omits_terrain_block_without_phase():
    p = build_narrative_prompt(_report(), "")
    assert "TERRAIN READING" not in p["user"]


def test_prompt_keeps_multiple_remedies_on_one_numbered_layer():
    report = {**_report(), "client": {"name": "Michael Hill"}, "layers": [
        {"layer": 2, "head": "Spleen", "most_affected": "Spleen",
         "remedy": "Fibrosolve", "dosage": "1-2 caps", "frequency": "daily",
         "timing": "on an empty stomach"},
        {"layer": 2, "head": "Spleen", "most_affected": "Spleen",
         "remedy": "Fibrolysis Factors", "dosage": "1 cap", "frequency": "daily",
         "timing": "with food"},
    ]}
    p = build_narrative_prompt(report, "splenic infarct")
    assert p["user"].count("- Layer 1 ") == 1
    assert "Layer 1 (ONE layer; 2 remedies)" in p["user"]
    assert "Fibrosolve" in p["user"] and "Fibrolysis Factors" in p["user"]
    assert "same causal-layer identifier" in p["system"]
    assert "never describe them as separate layers" in p["system"]


def test_prompt_uses_authored_stored_layer_not_per_remedy_display_position():
    report = {**_report(), "layers": [
        {"layer": 1, "stored_layer": 4, "head": "Spleen", "most_affected": "Spleen",
         "remedy": "Fibrosolve", "dosage": "1 cap", "frequency": "daily", "timing": ""},
        {"layer": 2, "stored_layer": 4, "head": "Spleen", "most_affected": "Spleen",
         "remedy": "Fibrolysis Factors", "dosage": "1 cap", "frequency": "daily", "timing": ""},
        {"layer": 3, "stored_layer": 5, "head": "Liver", "most_affected": "Liver",
         "remedy": "Liver Support", "dosage": "1 cap", "frequency": "daily", "timing": ""},
    ]}
    user = build_narrative_prompt(report, "")["user"]

    assert user.count("- Layer 1 ") == 1
    assert "Layer 1 (ONE layer; 2 remedies): Spleen" in user
    assert "Layer 2 (ONE layer; 1 remedy): Liver" in user


def test_life_stress_prompt_describes_associated_and_therapeutic_essences(monkeypatch):
    descriptions = {
        "Spectrolite Gem Elixir": "Indications: cloudy or outdated perspective on life.",
        "Motivation Flower Essence (Fox) in Terrain Restore":
            "Helps restore motivation, focus, and efficient action.",
    }
    monkeypatch.setattr(narrative_mod, "_catalog_description",
                        lambda name: descriptions.get(name, ""))
    report = {**_report(), "layers": [{
        "layer": 1, "stored_layer": 5, "head": "Life Stress",
        "most_affected": "Spectrolite Gem Elixir",
        "remedy": "Motivation Flower Essence (Fox) in Terrain Restore",
        "dosage": "10 drops", "frequency": "3 times a day", "timing": "before meals",
    }]}
    prompt = build_narrative_prompt(report, "")
    user, system = prompt["user"], prompt["system"]

    assert "LIFE STRESS ASSOCIATED ESSENCE / PATTERN: Spectrolite Gem Elixir" in user
    assert "cloudy or outdated perspective" in user
    assert "THERAPEUTIC ESSENCE: Motivation Flower Essence (Fox)" in user
    assert "restore motivation, focus" in user
    assert "do not list ai-matched" in system.lower()
    assert "associated essence identifies the pattern" in system


def test_essence_at_tail_triggers_indications_without_life_stress_head(monkeypatch):
    descriptions = {
        "Ecstasy": "Indicated for rigidity, bitterness, jealousy, and feeling unloved.",
        "Green Jasper Gem Elixir in Terrain Restore":
            "Supports steadiness, renewal, and compassionate emotional healing.",
    }
    monkeypatch.setattr(narrative_mod, "_catalog_description",
                        lambda name: descriptions.get(name, ""))
    monkeypatch.setattr(narrative_mod, "_is_essence", lambda name: name == "Ecstasy")
    report = {**_report(), "layers": [{
        "layer": 1, "head": "Stomach Driver, ED8", "most_affected": "Ecstasy",
        "remedy": "Green Jasper Gem Elixir in Terrain Restore",
        "dosage": "10 drops", "frequency": "3 times a day", "timing": "before meals",
    }]}
    prompt = build_narrative_prompt(report, "")
    user, system = prompt["user"], prompt["system"]

    assert "LIFE STRESS ASSOCIATED ESSENCE / PATTERN: Ecstasy" in user
    assert "rigidity, bitterness, jealousy" in user
    assert "THERAPEUTIC ESSENCE: Green Jasper Gem Elixir" in user
    assert "compassionate emotional healing" in user
    assert "inspect BOTH the Head and Tail" in system


def test_video_script_roundtrip(tmp_path):
    db = str(tmp_path / "chat_log.db")
    cx = sqlite3.connect(db)
    init_notes_tables(cx)
    assert get_video_script(cx, "10") == ""
    save_video_script(cx, "10", "Aloha Lewis, let me walk you through this.")
    assert get_video_script(cx, "10") == "Aloha Lewis, let me walk you through this."


def test_video_script_prompt_is_short_spoken_and_carries_chain():
    p = build_video_script_prompt(_report(), "mercury history")
    sys, usr = p["system"], p["user"]
    assert "Aloha" in sys
    assert "spoken" in sys.lower() or "out loud" in sys.lower() or "say" in sys.lower()
    assert "short" in sys.lower() or "brief" in sys.lower() or "60" in sys or "90" in sys
    assert "TMG Powder" in usr or "Night" in usr
    assert "mercury history" in usr


def test_generate_video_script_uses_injected_llm():
    seen = {}
    def fake(system, user):
        seen["user"] = user
        return "Aloha Lewis, here's the short version."
    out = generate_video_script(_report(), "mercury history", fake)
    assert out.startswith("Aloha Lewis")
    assert "mercury history" in seen["user"]


def test_generate_uses_injected_llm_and_returns_text():
    seen = {}
    def fake_llm(system, user):
        seen["system"] = system
        seen["user"] = user
        return "Aloha Lewis,\n\nYour body identified..."
    out = generate_narrative(_report(), "mercury history", fake_llm)
    assert out.startswith("Aloha Lewis,")
    assert "mercury history" in seen["user"]
