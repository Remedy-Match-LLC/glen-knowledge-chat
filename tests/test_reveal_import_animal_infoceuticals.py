"""An animal's imported causal chain recommends infoceuticals, not FFs.

Glen, 2026-09-18: for an animal, "Import Reveal -> Causal Chain should pull in the
recommended Infoceuticals, rather than FF's", using "the names you are currently using:
they name functions", and "you don't need to change the layering calculations".

So the FF-only synthesis and layer ordering are untouched. For an animal, each layer's
remedy becomes the function it already names -- its primary pattern label, the E4L
infoceutical -- and FF alternatives are dropped. This matches the rule the publish gate
already enforces (analysis_autoconfirm.animal_formulation_reasons blocks an animal draft
that names a formulation), which the import was contradicting.
"""
from dashboard.biofield_reveal_import import synthesize_reveal_layers

# One synthesized layer as _run_synthesis returns it: an FF remedy plus the function
# labels the layer is built around.
RAW = [{
    "n": 1, "title": "Layer 1", "summary": "s",
    "remedy": {"name": "WholOmega"},                    # an FF
    "pattern_labels": ["Energetic Drivers", "Energetic Integrators"],
    "patterns": ["ED", "EI"],
    "alternatives": [{"name": "Nrf2 Activator"}],       # an FF alternative
}]


def _runner(email, scan_id, e4l_db, catalog, today):
    return {"scan_id": "s1", "scan_date": "2026-09-18"}, [dict(L) for L in RAW]


def _layers(is_animal):
    r = synthesize_reveal_layers("pet@x.com", today="2026-09-18",
                                 runner=_runner, is_animal=is_animal)
    return r["layers"]


def test_a_human_still_gets_the_ff():
    L = _layers(is_animal=False)[0]
    assert L["remedy_name"] == "WholOmega"
    assert L["alternatives"] == [{"name": "Nrf2 Activator"}]


def test_an_animal_gets_the_function_name_not_the_ff():
    L = _layers(is_animal=True)[0]
    assert L["remedy_name"] == "Energetic Drivers", "the animal layer still names an FF"
    assert "WholOmega" not in L["remedy_name"]


def test_an_animal_drops_the_ff_alternatives():
    """Otherwise an FF sneaks back in as an alternative, which the publish gate rejects."""
    assert _layers(is_animal=True)[0]["alternatives"] == []


def test_the_layering_is_identical_for_both():
    """Glen: do not change the layering. n, title, codes and the labels must match."""
    h, a = _layers(False)[0], _layers(True)[0]
    for k in ("n", "title", "summary", "codes", "most_affected"):
        assert h[k] == a[k], f"{k} changed between human and animal"


def test_an_animal_with_no_labels_falls_back_to_the_synth_name():
    """A layer with no function labels must not import a blank remedy."""
    def runner(email, scan_id, e4l_db, catalog, today):
        return ({"scan_id": "s1", "scan_date": "2026-09-18"},
                [{"n": 1, "title": "L", "remedy": {"name": "Fallback"},
                  "pattern_labels": [], "patterns": [], "alternatives": []}])
    r = synthesize_reveal_layers("pet@x.com", today="2026-09-18", runner=runner,
                                 is_animal=True)
    assert r["layers"][0]["remedy_name"] == "Fallback"


# The route wiring used to be checked here by grepping biofield_local_app.py for the
# strings "client_species" and "is_animal=_is_animal". Both were present the whole
# time the route read species from a table that does not exist on Glen's Mac, so
# every animal imported as a person and this test stayed green (Sasha Takahashi,
# 2026-09-21). The wiring is now proven by driving the real route:
# tests/test_biofield_animal_infoceuticals.py::test_import_reveal_reads_species_from_e4l_db
