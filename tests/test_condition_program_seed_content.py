import json, os
SEED = os.path.join(os.path.dirname(__file__), "..", "data", "condition_programs_seed.json")

def _seed():
    with open(SEED) as f: return json.load(f)

def _slugs(prog): return [i["slug"] for i in prog["items"]]

def test_dry_amd_has_crocin_ocuheal_ocuflow_and_no_lipids_in_base():
    p = _seed()["condition_programs"]["dry-amd"]
    s = _slugs(p)
    assert "macular-wellness-crocin" in s
    assert "ocuheal-eye-drops" in s and "ocuflow-bedtime" in s
    assert "lipid-zyme" not in s and "lipid-cleanse" not in s
    wh = [m for m in p["modifiers"] if m["when"] == "drusen"][0]
    assert {i["slug"] for i in wh["items"]} == {"lipid-cleanse", "lipid-zyme"}
    ar = [m for m in p["modifiers"] if m["when"] == "on_areds2"][0]
    assert ar["action"] == "remove" and ar["source"] == "client-reported"

def test_wet_amd_moves_angiogenx_and_scar_to_modifiers():
    p = _seed()["condition_programs"]["wet-amd"]
    s = _slugs(p)
    assert "angiogenx" not in s and "scar-solve" not in s
    assert "macular-wellness-crocin" in s and p["consult_recommended"] is True
    whens = {m["when"] for m in p["modifiers"]}
    assert {"drusen", "on_areds2", "leakage", "scar"} <= whens

def test_dr_proliferative_modifier_and_ocuflow():
    p = _seed()["condition_programs"]["diabetic-retinopathy"]
    assert "ocuflow-bedtime" in _slugs(p)
    prolif = [m for m in p["modifiers"] if m["when"] == "proliferative"][0]
    assert prolif["source"] == "clinician-measured" and prolif["client_default"] is False
    assert p["consult_recommended"] is False  # Glen: DR stays one-click orderable

def test_ocuheal_in_every_program():
    progs = _seed()["condition_programs"]
    for key, p in progs.items():
        if key.startswith("symptom-"):
            continue
        slugs = set(_slugs(p))
        for it in p["items"]:
            slugs.update(a["slug"] for a in it.get("alts", []))
        assert "ocuheal-eye-drops" in slugs, f"{key} missing OcuHeal"

def test_name_typos_fixed():
    progs = _seed()["condition_programs"]
    names = [i["name"] for p in progs.values() for i in p["items"]]
    assert "Clear Lens Eyedrops" not in names
    assert "Lens-Zyme Brunescense Buster" not in names

def test_wet_amd_leakage_angiogenx_keeps_dose():
    p = _seed()["condition_programs"]["wet-amd"]
    leak = [m for m in p["modifiers"] if m["when"] == "leakage"][0]
    ax = [i for i in leak["items"] if i["slug"] == "angiogenx"][0]
    assert ax.get("dose") == "1 or more/day"

def test_wet_amd_leakage_scar_default_off_in_composer():
    p = _seed()["condition_programs"]["wet-amd"]
    for w in ("leakage", "scar"):
        m = [x for x in p["modifiers"] if x["when"] == w][0]
        assert m["source"] == "diagnosis-implied" and m["client_default"] is True
        assert m["composer_default"] is False, w


def test_macular_pucker_focuses_on_scar_reduction():
    p = _seed()["condition_programs"]["macular-pucker"]
    assert p["label"] == "Macular Pucker (Epiretinal Membrane)"
    assert p["consult_recommended"] is False
    assert _slugs(p) == [
        "scar-silk", "scar-solve", "scar-soft-drink", "ocuheal-eye-drops"
    ]
    assert p["items"][0]["alts"] == [
        {"slug": "clear-the-way", "name": "Clear the Way"}
    ]
    assert p["items"][2]["name"] == "Scar Soft (when available)"


def test_every_condition_includes_common_symptoms_for_matching_context():
    for key, program in _seed()["condition_programs"].items():
        symptoms = program.get("symptoms")
        assert isinstance(symptoms, list) and len(symptoms) >= 3, key
        assert all(isinstance(s, str) and s.strip() for s in symptoms), key


def test_systemic_symptoms_are_standalone_programs_with_remedy_items():
    progs = _seed()["condition_programs"]
    systemic = {k: v for k, v in progs.items() if k.startswith("symptom-")}
    assert len(systemic) == 10
    for key, program in systemic.items():
        assert len(program["items"]) >= 2, key
        assert all(item.get("slug") and item.get("name") for item in program["items"]), key


EARLY_PROGRAMS = ("glaucoma-early", "dry-amd-early", "diabetic-retinopathy-early")


def test_early_states_exist_for_the_four_silent_conditions():
    """The four programs that say "Often no symptoms early" now have a preventive
    state of their own. The two glaucomas share one: "glaucoma suspect" is a single
    clinical category, so splitting it by pressure would make two labels that differ
    only by a number the client does not have yet."""
    progs = _seed()["condition_programs"]
    for key in EARLY_PROGRAMS:
        assert key in progs, key
        assert progs[key]["label"].startswith("Early, Suspect or Pre-"), key
        assert progs[key]["label"].endswith(": Often no symptoms"), key


def test_early_states_carry_their_own_remedies_not_an_empty_list():
    """An early state with no items would render "No common remedies recorded yet",
    which is the defect this whole change removed."""
    progs = _seed()["condition_programs"]
    for key in EARLY_PROGRAMS:
        assert len(progs[key]["items"]) >= 3, key
        assert all(i.get("slug") and i.get("name") for i in progs[key]["items"]), key


def test_early_state_symptoms_are_unique_to_one_program():
    """A symptom listed under two programs takes its count from the first and its
    remedies from both, so the dropdown and the row disagree again. Early-state
    symptoms are screening findings, and each belongs to one condition."""
    progs = _seed()["condition_programs"]
    early = {s for key in EARLY_PROGRAMS for s in progs[key]["symptoms"]}
    others = [s for key, p in progs.items() if key not in EARLY_PROGRAMS
              for s in p.get("symptoms") or []]
    assert early & set(others) == set()
    counted = [s for key in EARLY_PROGRAMS for s in progs[key]["symptoms"]]
    assert len(counted) == len(set(counted))
