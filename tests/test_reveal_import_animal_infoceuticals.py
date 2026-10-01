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
    "pattern_labels": ["Heart – Lung Integrator", "Lymph Star"],   # E4L's own labels
    "patterns": ["EI2", "ES1"],
    "alternatives": [{"name": "Nrf2 Activator"}],       # an FF alternative
}]


def _runner(email, scan_id, e4l_db, catalog, today):
    return {"scan_id": "s1", "scan_date": "2026-09-18"}, [dict(L) for L in RAW]


# The remedy list, by code (Glen's names, 2026-10-01).
NAMES = {"EI2": "EI2 Heart/Lung Meridian Energetic Integrator Infoceutical",
         "ES1": "ES1 Immune Energetic Star Infoceutical"}


def _layers(is_animal):
    r = synthesize_reveal_layers("pet@x.com", today="2026-09-18", runner=_runner,
                                 is_animal=is_animal, infoceutical_names=NAMES)
    return r["layers"]


def test_a_human_still_gets_the_ff():
    L = _layers(is_animal=False)[0]
    assert L["remedy_name"] == "WholOmega"
    assert L["alternatives"] == [{"name": "Nrf2 Activator"}]


def test_an_animal_gets_the_remedy_list_name_for_the_code_not_the_ff():
    """Glen 2026-10-01: E4L's label ("Heart – Lung Integrator") is not the remedy name."""
    L = _layers(is_animal=True)[0]
    assert L["remedy_name"] == "EI2 Heart/Lung Meridian Energetic Integrator Infoceutical"
    assert "WholOmega" not in L["remedy_name"]


def test_an_animal_drops_the_ff_alternatives():
    """Otherwise an FF sneaks back in as an alternative, which the publish gate rejects."""
    assert _layers(is_animal=True)[0]["alternatives"] == []


def test_the_layering_is_identical_for_both():
    """Glen: do not change the layering. n, title, codes and the labels must match."""
    h, a = _layers(False)[0], _layers(True)[0]
    for k in ("n", "title", "summary", "codes", "most_affected"):
        assert h[k] == a[k], f"{k} changed between human and animal"


def _one(patterns, labels, names=NAMES):
    def runner(email, scan_id, e4l_db, catalog, today):
        return ({"scan_id": "s1", "scan_date": "2026-09-18"},
                [{"n": 1, "title": "L", "remedy": {"name": "WholOmega"},
                  "pattern_labels": labels, "patterns": patterns, "alternatives": []}])
    return synthesize_reveal_layers("pet@x.com", today="2026-09-18", runner=runner,
                                    is_animal=True, infoceutical_names=names)["layers"][0]


def test_a_rejuvenator_is_never_an_animal_remedy():
    """Glen 2026-10-01: "Scapula Rejuvenator ... is a setting on the miHealth". Skylar's
    layer led with an ER code; the next code the remedy list carries is the remedy."""
    L = _one(["ER17", "ES1"], ["Scapula Rejuvenator", "Lymph Star"])
    assert L["remedy_name"] == "ES1 Immune Energetic Star Infoceutical"
    assert "Scapula Rejuvenator" in L["most_affected"]          # kept as information


def test_a_layer_with_no_remedy_list_code_imports_blank_and_is_flagged():
    """Never an invented name, never the FF for an animal, and no internal note in
    most_affected: that field prints on the client's report (review round 1)."""
    for codes, labels in ((["ER17", "MR3"], ["Scapula Rejuvenator", "Calm Mind"]),
                          (["ENV-Glyphosate", "BFA-Grounding"], ["Glyphosate", "Grounding"]),
                          ([], [])):
        L = _one(codes, labels)
        assert L["remedy_name"] == "", codes
        assert L["no_remedy"] is True, codes
        assert L["most_affected"] == ", ".join(labels), codes
    assert _one(["ES1"], ["Lymph Star"])["no_remedy"] is False


def test_the_remedy_list_map_reads_filemaker_by_code(tmp_path):
    """The Intake's remedy list is the FileMaker product list (the picker and the dosing
    read it), so the code map comes from there. Names are Glen's, 2026-10-01."""
    import sqlite3
    from dashboard.biofield_authoring import infoceutical_names_by_code
    cx = sqlite3.connect(str(tmp_path / "c.db"))
    cx.execute("CREATE TABLE fmp_snap_products (id_pk INTEGER, product_name TEXT, active TEXT)")
    for i, (n, act) in enumerate([("MB8 Love Infoceutical", "Yes"),
                                  ("MB4 CCH Cerebral Cortex Hologram Infoceutical", "Yes"),
                                  ("EI2 Heart/Lung Meridian Energetic Integrator Infoceutical", "Yes"),
                                  ("ES1 Immune Energetic Star Infoceutical", "Yes"),
                                  ("ES10 Stress - Video Processing Energetic Star Infoceutical*", "Yes"),
                                  ("ET4 Retired Energetic Transformer Infoceutical", "No"),
                                  ("WholOmega", "Yes"), ("Scapula Rejuvenator", "Yes"), ("", "Yes")]):
        cx.execute("INSERT INTO fmp_snap_products VALUES (?, ?, ?)", (i, n, act))
    m = infoceutical_names_by_code(cx)
    assert m == {"MB8": "MB8 Love Infoceutical",
                 "MB4": "MB4 CCH Cerebral Cortex Hologram Infoceutical",
                 "EI2": "EI2 Heart/Lung Meridian Energetic Integrator Infoceutical",
                 "ES1": "ES1 Immune Energetic Star Infoceutical",
                 # discontinue-intent stays, as in the picker; the marker is stripped
                 "ES10": "ES10 Stress - Video Processing Energetic Star Infoceutical"}
    assert "ET4" not in m                                     # inactive in FileMaker
    assert _one(["MB8"], ["Love Hologram"], names=m)["remedy_name"] == "MB8 Love Infoceutical"


def test_the_web_catalog_fallback_carries_no_rejuvenator():
    from dashboard.animal_infoceuticals import infoceutical_by_code
    from dashboard.biofield_portal_publish import load_catalog
    m = infoceutical_by_code(load_catalog())
    assert not [c for c in m if c.startswith(("ER", "MR", "BFA", "ENV", "NUT"))]
    assert not [n for n in m.values() if "rejuvenator" in n.lower()]


# The route wiring used to be checked here by grepping biofield_local_app.py for the
# strings "client_species" and "is_animal=_is_animal". Both were present the whole
# time the route read species from a table that does not exist on Glen's Mac, so
# every animal imported as a person and this test stayed green (Sasha Takahashi,
# 2026-09-21). The wiring is now proven by driving the real route:
# tests/test_biofield_animal_infoceuticals.py::test_import_reveal_reads_species_from_e4l_db
