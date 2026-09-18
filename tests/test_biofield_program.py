"""Glen's priority sequence for a biofield balancing program.

Glen, 2026-09-17: *"integrate all 3 stress sources into a biofield balancing program
of layers and remedies. Priority sequence: 1. Spirit (transcript based layers,
stresses, and remedies) 2. Mind (history-based symptoms and conditions) - if not
already addressed by relevant remedies from step 1. 3. Body (scan patterns) - if not
already addressed by relevant remedies from 1 & 2."*

Two buttons, his words: *"full (narrow, bigger program) - vs minimum (wide, minimal
program)"*. Full suppresses a later stress only when a placed remedy already covers
that exact stress. Minimum also suppresses it when its function is already addressed.
"""
from dashboard.biofield_program import build_program, FULL, MINIMUM


def _s(label, source, code=""):
    return {"code": code, "label": label, "source": source, "balance": "required"}


def _one_remedy_each(tokens):
    """A stand-in cover: one remedy per token, named after it."""
    return [{"remedy": "R-" + t, "covers": [t]} for t in tokens]


def _nothing(tokens):
    return []


def test_the_stages_run_spirit_then_mind_then_body():
    prog = build_program(
        stresses=[_s("ED6 Heart", "scan", code="ED6"),
                  _s("Fatigue", "tag"),
                  _s("Grief", "voice")],
        spirit_layers=[],
        cover=_one_remedy_each,
        mode=FULL)
    assert [st["stage"] for st in prog["stages"]] == ["spirit", "mind", "body"]
    assert [p["remedy"] for p in prog["stages"][0]["picks"]] == ["R-grief"]
    assert [p["remedy"] for p in prog["stages"][1]["picks"]] == ["R-fatigue"]
    assert [p["remedy"] for p in prog["stages"][2]["picks"]] == ["R-ED6"]


def test_a_transcript_remedy_suppresses_the_same_stress_later():
    """Step 2 skips what step 1's remedy already addresses."""
    prog = build_program(
        stresses=[_s("Grief", "tag")],
        spirit_layers=[{"remedy": "Heart Health", "covers": ["grief"]}],
        cover=_one_remedy_each,
        mode=FULL)
    mind = prog["stages"][1]
    assert mind["picks"] == []
    assert [d["label"] for d in mind["suppressed"]] == ["Grief"]
    assert mind["suppressed"][0]["by"] == "Heart Health"


def test_full_keeps_a_stress_whose_function_alone_was_addressed():
    prog = build_program(
        stresses=[_s("ED6 Heart", "scan", code="ED6")],
        spirit_layers=[{"remedy": "Circulation", "covers": ["ED5"]}],
        cover=_one_remedy_each,
        functions_of=lambda t: {"ED5": ("circulation",), "ED6": ("circulation", "signalling")}.get(t, ()),
        mode=FULL)
    assert [p["remedy"] for p in prog["stages"][2]["picks"]] == ["R-ED6"]


def test_minimum_suppresses_a_stress_whose_function_was_addressed():
    prog = build_program(
        stresses=[_s("ED6 Heart", "scan", code="ED6")],
        spirit_layers=[{"remedy": "Circulation", "covers": ["ED5"]}],
        cover=_one_remedy_each,
        functions_of=lambda t: {"ED5": ("circulation",), "ED6": ("circulation", "signalling")}.get(t, ()),
        mode=MINIMUM)
    body = prog["stages"][2]
    assert body["picks"] == []
    assert body["suppressed"][0]["by"] == "Circulation"
    assert body["suppressed"][0]["on"] == "circulation"


def test_minimum_is_always_a_subset_of_full():
    """The whole promise of the two buttons. Minimum never adds anything Full lacks."""
    stresses = [_s("Grief", "voice"), _s("Sorrow", "tag"),
                _s("ED6 Heart", "scan", code="ED6")]
    seed = [{"remedy": "Heart Health", "covers": ["grief"]}]
    fn = lambda t: {"grief": ("grieving", "feeling"), "sorrow": ("grieving",),
                    "ED6": ("circulation",)}.get(t, ())
    full = build_program(stresses=stresses, spirit_layers=seed,
                         cover=_one_remedy_each, functions_of=fn, mode=FULL)
    minimum = build_program(stresses=stresses, spirit_layers=seed,
                            cover=_one_remedy_each, functions_of=fn, mode=MINIMUM)
    assert set(minimum["remedies"]) <= set(full["remedies"])
    # Full keeps Sorrow, because no remedy covers that exact stress
    assert full["remedies"] == ["Heart Health", "R-sorrow", "R-ED6"]
    # Minimum drops it, because grieving is already addressed
    assert minimum["remedies"] == ["Heart Health", "R-ED6"]


def test_two_stresses_in_one_stage_are_the_covers_business():
    """Suppression runs BETWEEN stages. Inside one pass the set-cover decides, so
    two same-function findings in the same stage both reach it."""
    prog = build_program(
        stresses=[_s("ED5 Flow", "scan", code="ED5"),
                  _s("ED6 Heart", "scan", code="ED6")],
        spirit_layers=[], cover=_one_remedy_each,
        functions_of=lambda t: {"ED5": ("circulation",), "ED6": ("circulation", "signalling")}.get(t, ()),
        mode=MINIMUM)
    assert prog["stages"][2]["pending"] == ["ED5", "ED6"]


def test_a_stress_already_on_the_chain_is_not_a_finding():
    """source 'chain' came off the causal chain, so it is placed, not pending."""
    prog = build_program(
        stresses=[_s("Liver", "chain")],
        spirit_layers=[], cover=_one_remedy_each, mode=FULL)
    assert prog["remedies"] == []


def test_glens_own_manual_stresses_ride_with_spirit():
    """CORRECTED 2026-09-18. This test was named ..._ride_with_mind and asserted stage 1.

    Glen: "The manual biofield stress responses and balancing remedies in remote biofield
    analysis come from coherent communication between the spirit of the tester as
    surrogate and the spirit of the client, based on meaning associated with Gold."

    A manual biofield response is a surrogate reading, not the practitioner's opinion, so
    it is Spirit. Mind is symptoms and diagnoses -- Iridium, visual -- which is the
    Clinical Summary plus what is mined from tags and communications.
    """
    prog = build_program(
        stresses=[_s("Worthiness", "manual")],
        spirit_layers=[], cover=_one_remedy_each, mode=FULL)
    stages = {s["stage"]: s for s in prog["stages"]}
    assert [p["remedy"] for p in stages["spirit"]["picks"]] == ["R-worthiness"]
    assert not stages["mind"]["picks"], "a manual response is not a Mind finding"


def test_an_optional_stress_is_not_balanced():
    s = _s("Maybe", "tag")
    s["balance"] = "optional"
    prog = build_program(stresses=[s], spirit_layers=[],
                         cover=_one_remedy_each, mode=FULL)
    assert prog["remedies"] == []


def test_the_seed_layers_lead_the_program():
    prog = build_program(
        stresses=[_s("Fatigue", "tag")],
        spirit_layers=[{"remedy": "Heart Health", "covers": ["grief"]}],
        cover=_one_remedy_each, mode=FULL)
    assert prog["remedies"] == ["Heart Health", "R-fatigue"]


def test_a_remedy_picked_in_one_stage_suppresses_the_next():
    """Step 3 skips what step 2's remedy addresses, not only step 1's."""
    def cover_both(tokens):
        return [{"remedy": "Wide One", "covers": list(tokens) + ["ED6"]}]
    prog = build_program(
        stresses=[_s("Fatigue", "tag"), _s("ED6 Heart", "scan", code="ED6")],
        spirit_layers=[], cover=cover_both, mode=FULL)
    assert prog["stages"][2]["picks"] == []
    assert prog["stages"][2]["suppressed"][0]["by"] == "Wide One"


def test_any_shared_function_is_enough_to_suppress():
    """A tissue serves several functions. Sharing one of them is a match."""
    prog = build_program(
        stresses=[_s("ED6 Heart", "scan", code="ED6")],
        spirit_layers=[{"remedy": "Nerve Support", "covers": ["ED11"]}],
        cover=_one_remedy_each,
        functions_of=lambda t: {"ED11": ("signalling",),
                                "ED6": ("circulation", "signalling")}.get(t, ()),
        mode=MINIMUM)
    assert prog["stages"][2]["picks"] == []
    assert prog["stages"][2]["suppressed"][0]["on"] == "signalling"


# ── the cover call the route injects ──────────────────────────────────────────────
from dashboard.biofield_stress import cover_tokens


def test_cover_tokens_returns_the_tokens_it_covers_not_labels():
    """build_program matches on tokens, so the cover must report tokens. Reporting
    labels would silently suppress nothing."""
    coverage = {"neuro magnesium": {"ED1", "ES3"}, "heart health": {"ED6"}}
    picks = cover_tokens({"ED1", "ES3", "ED6"}, coverage)
    got = {p["remedy"]: set(p["covers"]) for p in picks}
    assert got == {"neuro magnesium": {"ED1", "ES3"}, "heart health": {"ED6"}}


def test_cover_tokens_reports_only_the_tokens_asked_for():
    coverage = {"neuro magnesium": {"ED1", "ES3", "MR2"}}
    picks = cover_tokens({"ED1"}, coverage)
    assert [set(p["covers"]) for p in picks] == [{"ED1"}]


def test_cover_tokens_on_nothing_is_nothing():
    assert cover_tokens(set(), {"x": {"ED1"}}) == []
