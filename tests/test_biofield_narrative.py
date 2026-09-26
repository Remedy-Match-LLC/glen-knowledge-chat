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
    assert "MUST name that associated essence" in system
    assert "at least two of its supplied indications" in system
    assert "Never omit either half" in system


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


def test_phase_2_is_rejuvenate_and_wrong_llm_term_is_corrected():
    report = {**_report(), "phase": 2, "location": "toxicity"}
    prompt = build_narrative_prompt(report, "")
    assert "Phase 2, Rejuvenate" in prompt["user"]
    assert "Phase 2 = Rejuvenate" in prompt["system"]
    assert "Regenerate belongs only to Phase 3" in prompt["system"]

    out = generate_narrative(
        report, "", lambda _system, _user: "Aloha Lewis,\n\nPhase 2, Regenerate, was identified."
    )
    assert "Phase 2, Rejuvenate" in out
    assert "Phase 2, Regenerate" not in out


def test_narrative_numbers_layers_the_way_the_report_table_does():
    """Glen, 2026-09-18, on Michael Hill's report: the narrative listed each remedy of
    one layer as its own layer (4, 5 and 6 for his three spleen remedies), and every
    later number then disagreed with the report. His rows are stored as layers 4, 5
    and 6 under one head. The editor and the Causal Chain table group by head, so the
    narrative must use that same grouping, from the same function."""
    from dashboard.biofield_report_html import group_layers
    rows = [("Thymus", "Spike Shield"), ("Mercury", "Mercury Detox Syntropy Powder"),
            ("Splenic infarct, Current", "Fibrolysis Factors"),
            ("Splenic infarct, Current", "Fibrosolve"),
            ("Splenic infarct, Current", "Spleen Support"),
            ("A fib, Current", "Rhythm Restore")]
    layers = [{"layer": i, "stored_layer": i, "head": h, "most_affected": "",
               "remedy": r, "dosage": "1 cap", "frequency": "daily", "timing": ""}
              for i, (h, r) in enumerate(rows, 1)]
    user = build_narrative_prompt({**_report(), "layers": layers}, "")["user"]

    assert "Layer 3 (ONE layer; 3 remedies): Splenic infarct, Current" in user
    assert "Layer 4 (ONE layer; 1 remedy): A fib, Current" in user
    assert "- Layer 5 " not in user
    table = {g["head"]: g["layer"] for g in group_layers(layers)}
    assert table["A fib, Current"] == 4


def test_each_layer_is_named_by_its_head_not_its_most_affected_list():
    """Glen, 2026-09-19: he changed Hershey's layer 4 Head to "Lens", and the
    narrative still led with its most-affected entry, "pinpoint cataracts"."""
    s = build_narrative_prompt(_report(), "")["system"]
    assert "NAME EACH LAYER BY ITS HEAD: open each layer's paragraph with the layer's Head" in s
    assert "never let it replace the Head as the layer's subject" in s


def test_scan_guidance_names_the_bioenergetic_wellness_scan():
    """Glen, 2026-09-18: E4L's instrument is the Bioenergetic Wellness Scan, and "voice
    scan" never appears unqualified. The earlier guidance told the writer to say "E4L
    voice scan", and Donna Banks's letter did (clinical, 2026-09-25)."""
    from dashboard.biofield_narrative import _SCAN_GUIDANCE
    assert "Always call it the 'Bioenergetic Wellness Scan'; never write 'voice scan'" in _SCAN_GUIDANCE
    assert "E4L voice" not in _SCAN_GUIDANCE


def test_a_remedy_less_anchor_row_is_not_counted_or_listed_as_a_remedy():
    """Hershey Connour's layer 4 kept an empty anchor row beside Clear Lens Eyedrops.
    Counted, it read as two remedies and the writer said the dose was not detailed."""
    layers = [
        {"layer": 1, "head": "Lens", "most_affected": "pinpoint cataracts",
         "remedy": "", "dosage": "", "frequency": "", "timing": ""},
        {"layer": 2, "head": "Lens", "most_affected": "pinpoint cataracts",
         "remedy": "Clear Lens Eyedrops", "dosage": "", "frequency": "", "timing": ""},
    ]
    user = build_narrative_prompt({**_report(), "layers": layers}, "")["user"]
    assert "Layer 1 (ONE layer; 1 remedy): Lens" in user
    assert user.count("  - remedy:") == 1
    assert "remedy: Clear Lens Eyedrops;" in user and "; dose: as directed" in user


def test_narrative_is_plain_text():
    assert "PLAIN TEXT ONLY: no markdown" in build_narrative_prompt(_report(), "")["system"]


# Glen, 2026-09-24: "When the tail includes more than just the head, add a narrative
# description of the tail integrating structure/function and how it relates to what we
# know about the client ... while avoiding claims but expressing how balancing these
# patterns is supportive. Consider highlighting key relevant pathways addressed by the
# remedy or remedies on the layer."

def _tail_report():
    return {**_report(), "layers": [
        {"layer": 1, "head": "Night", "most_affected": "Night",
         "remedy": "TMG Powder", "dosage": "", "frequency": "", "timing": ""},
        {"layer": 2, "head": "Stress",
         "most_affected": "Stress, Nerve Terrain, Auditory Processing",
         "remedy": "Stress Release", "dosage": "", "frequency": "", "timing": ""},
        {"layer": 2, "head": "Stress",
         "most_affected": "Stress, Nerve Terrain, Auditory Processing",
         "remedy": "Nous Energy", "dosage": "", "frequency": "", "timing": ""},
    ]}


def _catalog(monkeypatch, descs, essences=(), ingredients=None):
    monkeypatch.setattr(narrative_mod, "_catalog_description", lambda n: descs.get(n, ""))
    monkeypatch.setattr(narrative_mod, "_catalog_product",
                        lambda n: {"ingredients": (ingredients or {}).get(n, [])})
    monkeypatch.setattr(narrative_mod, "_is_essence", lambda n: n in essences)


def test_a_tail_beyond_the_head_is_listed_without_the_head(monkeypatch):
    _catalog(monkeypatch, {})
    user = build_narrative_prompt(_tail_report(), "")["user"]
    assert "  - TAIL BEYOND THE HEAD: Nerve Terrain; Auditory Processing" in user
    assert "(write 2 to 4 sentences on these tail areas in this layer's paragraph" in user
    # Printed once for the layer, not once per remedy row.
    assert user.count("  - TAIL BEYOND THE HEAD:") == 1


def test_a_tail_that_only_repeats_the_head_adds_nothing(monkeypatch):
    _catalog(monkeypatch, {})
    user = build_narrative_prompt(_report(), "")["user"]
    # Layer 1 "Night"/"Night" repeats the head; layer 2 "Acid"/"Liver" does not.
    assert user.count("  - TAIL BEYOND THE HEAD:") == 1
    assert "  - TAIL BEYOND THE HEAD: Liver" in user


def test_head_match_ignores_case_and_spacing(monkeypatch):
    _catalog(monkeypatch, {})
    r = {**_report(), "layers": [
        {"layer": 1, "head": "Thyroid ", "most_affected": " thyroid",
         "remedy": "Thyroid Support", "dosage": "", "frequency": "", "timing": ""}]}
    assert "TAIL BEYOND THE HEAD" not in build_narrative_prompt(r, "")["user"]


def test_remedy_pathways_come_only_from_ingredient_names(monkeypatch):
    _catalog(monkeypatch, {"Stress Release": "Heals the causes of glaucoma. Price: $69.97."},
             ingredients={"Stress Release": [{"name": "CBD", "dose": "10 mg"},
                                             {"name": "L-Theanine"}]})
    user = build_narrative_prompt(_tail_report(), "")["user"]
    assert "remedy: Stress Release; pathways source: ingredients: CBD, L-Theanine; dose" in user
    # The catalog description carries claims and prices: it never reaches the writer.
    assert "glaucoma" not in user and "Price:" not in user
    # No ingredients: the writer is told so, rather than left to invent one.
    assert "remedy: Nous Energy; pathways source: (none supplied; name no pathway)" in user
    # A layer whose tail only repeats its head carries no pathways source.
    assert "remedy: TMG Powder; dose:" in user


def test_an_ingredient_that_is_another_layers_remedy_is_left_out(monkeypatch):
    """Named in layer 2's paragraph, "TMG Powder" would start layer 1's portal card
    there (segment_narrative finds each layer by its remedy name)."""
    _catalog(monkeypatch, {}, ingredients={"Stress Release": [{"name": "tmg powder"},
                                                              {"name": "CBD"}]})
    user = build_narrative_prompt(_tail_report(), "")["user"]
    assert "remedy: Stress Release; pathways source: ingredients: CBD; dose" in user


def test_a_life_stress_layer_with_a_tail_still_gets_a_pathways_source(monkeypatch):
    _catalog(monkeypatch, {})
    r = {**_report(), "layers": [
        {"layer": 1, "head": "Life Stress", "most_affected": "Life Stress, Liver",
         "remedy": "Moonstone Gem Elixir", "dosage": "", "frequency": "", "timing": ""}]}
    user = build_narrative_prompt(r, "")["user"]
    assert "  - TAIL BEYOND THE HEAD: Liver" in user
    assert "pathways source: (none supplied; name no pathway)" in user


def test_an_essence_among_several_tail_items_is_not_dropped(monkeypatch):
    _catalog(monkeypatch, {}, essences={"Anger"})
    r = {**_report(), "layers": [
        {"layer": 1, "head": "Liver", "most_affected": "Liver, Anger",
         "remedy": "Liver Support", "dosage": "", "frequency": "", "timing": ""}]}
    assert "  - TAIL BEYOND THE HEAD: Anger" in build_narrative_prompt(r, "")["user"]


def test_an_essence_in_the_tail_stays_with_the_life_stress_rule(monkeypatch):
    _catalog(monkeypatch, {}, essences={"Sunflower Flower Essence"})
    r = {**_report(), "layers": [
        {"layer": 1, "head": "Grief", "most_affected": "Sunflower Flower Essence",
         "remedy": "Moonstone Gem Elixir", "dosage": "", "frequency": "", "timing": ""}]}
    user = build_narrative_prompt(r, "")["user"]
    assert "LIFE STRESS ASSOCIATED ESSENCE / PATTERN: Sunflower Flower Essence" in user
    assert "TAIL BEYOND THE HEAD" not in user


def test_tail_rule_forbids_claims_and_invention():
    s = build_narrative_prompt(_report(), "")["system"]
    assert "TAIL BEYOND THE HEAD:" in s
    for phrase in ("structure and function", "Cover every tail area listed",
                   "describe the body area or function it names, not a product",
                   "only where a CLIENT-STATED CONCERNS item plainly relates",
                   "ONLY from that remedy's 'pathways source'",
                   "balancing these patterns supports",
                   "Never say a remedy treats, heals, cures, fixes or prevents",
                   "never state or imply a diagnosis",
                   "never invent a symptom or condition",
                   "when it says none was supplied, name no pathway for it"):
        assert phrase in s, phrase


def test_an_ingredient_containing_another_layers_remedy_name_is_left_out(monkeypatch):
    """segment_narrative finds a layer by substring, so "TMG Powder Blend" named in
    layer 2 would start layer 1's card there. A head is not a cue here: "Night"
    must not strip "Nightshade-free base"."""
    _catalog(monkeypatch, {}, ingredients={"Stress Release": [
        {"name": "TMG Powder Blend"}, {"name": "Nightshade-free base"}, {"name": "CBD"}]})
    user = build_narrative_prompt(_tail_report(), "")["user"]
    assert "pathways source: ingredients: Nightshade-free base, CBD; dose" in user


def test_tail_items_compare_without_punctuation_dedupe_and_read_every_row(monkeypatch):
    _catalog(monkeypatch, {})
    r = {**_report(), "layers": [
        {"layer": 1, "head": "Liver.", "most_affected": "",
         "remedy": "Liver Support", "dosage": "", "frequency": "", "timing": ""},
        {"layer": 1, "head": "Liver.", "most_affected": "Liver, Kidney, kidney, Kidney ",
         "remedy": "Kidney Support", "dosage": "", "frequency": "", "timing": ""}]}
    assert "  - TAIL BEYOND THE HEAD: Kidney\n" in build_narrative_prompt(r, "")["user"]


def test_the_video_script_carries_no_tail_block(monkeypatch):
    _catalog(monkeypatch, {})
    user = build_video_script_prompt(_tail_report(), "")["user"]
    assert "TAIL BEYOND THE HEAD" not in user and "pathways source" not in user


def test_a_paragraph_never_names_another_layers_remedy():
    assert ("Never name another layer's remedy in this paragraph."
            in build_narrative_prompt(_report(), "")["system"])


def test_a_punctuation_only_tail_item_is_not_listed(monkeypatch):
    _catalog(monkeypatch, {})
    r = {**_report(), "layers": [
        {"layer": 1, "head": "Liver", "most_affected": "Liver, -, Kidney",
         "remedy": "Liver Support", "dosage": "", "frequency": "", "timing": ""}]}
    assert "  - TAIL BEYOND THE HEAD: Kidney\n" in build_narrative_prompt(r, "")["user"]


def test_an_unnamed_filemaker_placeholder_is_not_an_ingredient(monkeypatch):
    _catalog(monkeypatch, {}, ingredients={"Stress Release": [
        {"name": "(unnamed FMP ingredient 5461)"}, {"name": "CBD"}]})
    user = build_narrative_prompt(_tail_report(), "")["user"]
    assert "pathways source: ingredients: CBD; dose" in user
