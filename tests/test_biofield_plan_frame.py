"""The Plan report's report-level lines and ingredient caution table (Glen, 2026-10-18).

Clinical writes plan-report/frame-lines.json. Only entries approved with a literal true
are kept. Placement, Glen's "yes" in the platform tab: the vertigo safety line (only
with a Vertigo tick), dose totals and thyroid spacing print once at the top; the
caution intro heads the caution table; the infoceutical header heads Remedies. Each
caution prints once, only when one of the items clinical lists under it is ticked, and
names those items as they print.
"""
import json
import sqlite3

import pytest

from dashboard import biofield_clinical_sections as cs
from dashboard import biofield_plan_report as pr
from dashboard.biofield_clinical_proposals import save_selection

TOP = {"vertigo_safety": "Vertigo needs care.", "dose_totals": "Count every source.",
       "thyroid_spacing": "Space thyroid medicine.",
       "infoceutical_header": "Infoceuticals are E4L matches.",
       "ingredient_caution_intro": "Some products share a caution."}


@pytest.fixture
def cx(tmp_path):
    con = sqlite3.connect(str(tmp_path / "chat_log.db"))
    yield con
    con.close()


def _item(name, overall=7, **extra):
    return dict({"name": name, "overall": overall, "research": 5, "uniqueness": 5,
                 "reason": "INTERNAL", "source": "src", "botanical": False,
                 "lowers_bp": None, "explanation": "", "explanation_approved": False}, **extra)


def _tick(cx, label, section, name, overall=7):
    cs.upsert_item(cx, label, section, _item(name, overall), origin_test="a49")
    cs.save_pick(cx, "a49", label, section, name, checked=True)


def _remedy(cx, label, section, name):
    cs.upsert_item(cx, label, section, _item(name), origin_test="a49")
    save_selection(cx, "a49", label, [name])


def _caution(text, items, approved=True):
    return {"text": text, "approved": approved, "items": items}


def _folder(tmp_path, lines=None, cautions=None, merges=()):
    folder = tmp_path / "plan-report"
    folder.mkdir(exist_ok=True)
    (folder / "merge-map.json").write_text(json.dumps({"merges": list(merges)}))
    (folder / "score-lines.json").write_text(json.dumps({"lines": {}}))
    (folder / "frame-lines.json").write_text(json.dumps({
        "lines": {k: {"text": v, "approved": True} for k, v in TOP.items()}
        if lines is None else lines,
        "ingredient_cautions": cautions or {}}))
    return str(folder)


def _page(cx, test_id="a49"):
    return pr.build(cx, test_id, "Josie", "18 October 2026")[0]


BERBERINE = {"Berberine": _caution("It lowers blood sugar.", [
    {"section": cs.FORMULATIONS, "name": "Glucose Tolerance"},
    {"section": cs.BOTANICALS, "name": "Berberine HCl"}])}


def test_approved_frame_lines_load_and_others_are_counted(cx, tmp_path):
    lines = {"dose_totals": {"text": "Count every source.", "approved": True},
             "thyroid_spacing": {"text": "Space it.", "approved": "yes"},
             "vertigo_safety": {"text": "Care.", "approved": False},
             "infoceutical_header": {"text": "", "approved": True},
             "new_line": {"text": "Not placed yet.", "approved": True}}
    report = pr.load_content(cx, _folder(tmp_path, lines))
    assert pr.stored_frame(cx)[0] == {"dose_totals": "Count every source."}
    assert (report["frame_lines_applied"], report["frame_lines_waiting"],
            report["frame_lines_unknown"]) == (1, 3, ["new_line"])


def test_the_top_lines_print_once_in_order_and_vertigo_only_with_a_vertigo_tick(cx, tmp_path):
    pr.load_content(cx, _folder(tmp_path))
    _tick(cx, "Gut", "foods_emphasize", "Bone broth")
    page = _page(cx)
    assert "Vertigo needs care." not in page
    assert page.count("Count every source.") == page.count("Space thyroid medicine.") == 1
    assert page.index("Count every source.") < page.index("Space thyroid medicine.") \
        < page.index("Bone broth")
    _tick(cx, "Vertigo", cs.NUTRIENTS, "Magnesium")
    _tick(cx, "Vertigo", cs.NUTRIENTS, "Vitamin B12")
    page = _page(cx)
    assert page.count("Vertigo needs care.") == 1
    assert page.index("Vertigo needs care.") < page.index("Count every source.")


def test_the_vertigo_line_follows_a_vertigo_remedy_too(cx, tmp_path):
    pr.load_content(cx, _folder(tmp_path))
    _remedy(cx, "Vertigo", cs.FORMULATIONS, "Gingerol")
    assert "Vertigo needs care." in _page(cx)


def test_the_infoceutical_header_heads_remedies_only_with_an_infoceutical(cx, tmp_path):
    pr.load_content(cx, _folder(tmp_path))
    _remedy(cx, "Gut", cs.FORMULATIONS, "Digestzymes")
    assert "Infoceuticals are E4L matches." not in _page(cx)
    _remedy(cx, "Liver", cs.INFOCEUTICALS, "ED11 Liver Energetic Driver Infoceutical")
    page = _page(cx)
    remedies = page.index("<h2>Remedies</h2>")
    assert page.count("Infoceuticals are E4L matches.") == 1
    assert remedies < page.index("Infoceuticals are E4L matches.") < page.index("ED11 Liver")


def test_a_caution_prints_only_with_a_ticked_item_and_names_it(cx, tmp_path):
    pr.load_content(cx, _folder(tmp_path, cautions=BERBERINE))
    _tick(cx, "Gut", "foods_emphasize", "Bone broth")
    page = _page(cx)
    assert "It lowers blood sugar." not in page
    assert "Some products share a caution." not in page
    _remedy(cx, "Blood sugar", cs.FORMULATIONS, "Glucose Tolerance")
    page = _page(cx)
    assert page.count("It lowers blood sugar.") == 1
    block = page[page.index("plan-cautions"):page.index("<section")]
    assert "Some products share a caution." in block
    assert "Berberine" in block and "Glucose Tolerance" in block
    assert "Berberine HCl" not in block


def test_one_item_ticked_under_two_conditions_is_named_once(cx, tmp_path):
    pr.load_content(cx, _folder(tmp_path, cautions=BERBERINE))
    _tick(cx, "Gut", cs.BOTANICALS, "Berberine HCl")
    _tick(cx, "Blood sugar", cs.BOTANICALS, "Berberine HCl")
    _remedy(cx, "Blood sugar", cs.FORMULATIONS, "Glucose Tolerance")
    hits = pr.cautions_for(pr.plan(cx, "a49"), pr.stored_frame(cx)[1])
    assert hits == [("Berberine", "It lowers blood sugar.", ["Glucose Tolerance",
                                                              "Berberine HCl"])]


def test_the_section_must_match_so_a_food_never_borrows_a_botanicals_caution(cx, tmp_path):
    ginger = {"Ginger": _caution("Ginger thins blood.",
                                 [{"section": cs.BOTANICALS, "name": "Ginger"}])}
    pr.load_content(cx, _folder(tmp_path, cautions=ginger))
    _tick(cx, "Gut", "foods_emphasize", "Ginger")
    assert "Ginger thins blood." not in _page(cx)
    _tick(cx, "Gut", cs.BOTANICALS, "Ginger")
    assert "Ginger thins blood." in _page(cx)


def test_a_botanical_merged_into_nutrients_keeps_its_caution(cx, tmp_path):
    merge = {"group": "nutrients_botanicals", "display_name": "Curcumin",
             "members": ["Turmeric extract", "Curcumin"], "approved": True}
    curcumin = {"Turmeric": _caution("It may thin blood.",
                                     [{"section": cs.BOTANICALS, "name": "Turmeric extract"}])}
    pr.load_content(cx, _folder(tmp_path, cautions=curcumin, merges=[merge]))
    _tick(cx, "Gut", cs.BOTANICALS, "Turmeric extract")
    assert pr.cautions_for(pr.plan(cx, "a49"), pr.stored_frame(cx)[1]) == [
        ("Turmeric", "It may thin blood.", ["Curcumin"])]
    assert "<p class=plan-caution-items>Curcumin</p>" in _page(cx)


def test_unapproved_or_itemless_cautions_never_print_and_are_counted(cx, tmp_path):
    cautions = {"Iodine": _caution("Add up the iodine.", [], True),
                "Biotin": _caution("Biotin skews tests.",
                                   [{"section": cs.NUTRIENTS, "name": "Biotin"}], "yes"),
                "NAC": _caution("NAC note.", [{"section": cs.NUTRIENTS, "name": "No Such"}])}
    report = pr.load_content(cx, _folder(tmp_path, cautions=cautions))
    _tick(cx, "Gut", cs.NUTRIENTS, "Biotin")
    page = _page(cx)
    assert "Add up the iodine." not in page and "Biotin skews tests." not in page
    assert (report["cautions_applied"], report["cautions_waiting"]) == (2, 1)
    assert report["cautions_without_items"] == ["Iodine"]
    assert report["caution_items_not_in_catalog"] == ["NAC: nutrients: No Such"]


def test_a_reload_without_the_file_withdraws_every_frame_line_and_caution(cx, tmp_path):
    folder = _folder(tmp_path, cautions=BERBERINE)
    pr.load_content(cx, folder)
    assert pr.stored_frame(cx)[0] and pr.stored_frame(cx)[1]
    (tmp_path / "plan-report" / "frame-lines.json").unlink()
    pr.load_content(cx, folder)
    assert pr.stored_frame(cx) == ({}, [])


def test_every_caution_and_frame_string_is_escaped(cx, tmp_path):
    bad = "<b>x</b>"
    groups = {cs.INFOCEUTICALS: [{"name": "ED1 " + bad, "kind": "Infoceutical", "overall": 7,
                                  "entries": [{"condition": "Vertigo", "name": "ED1 " + bad,
                                               "section": cs.INFOCEUTICALS, "overall": 7,
                                               "condition_key": "vertigo",
                                               "dose": "", "explanation": ""}]}]}
    frame = {k: v + " " + bad for k, v in TOP.items()}
    page = pr.render_plan_html(groups, frame=frame,
                               cautions=[("Name " + bad, "Text " + bad, ["Item " + bad])])
    # Five frame lines, the caution's name, text and item, and the line's name.
    assert "<b>" not in page and page.count("&lt;b&gt;") == 9


def test_two_lines_with_one_name_are_named_once_in_a_caution(cx, tmp_path):
    ginger = {"Ginger": _caution("Ginger thins blood.", [
        {"section": cs.BOTANICALS, "name": "Ginger"},
        {"section": "foods_emphasize", "name": "Ginger"}])}
    pr.load_content(cx, _folder(tmp_path, cautions=ginger))
    _tick(cx, "Gut", "foods_emphasize", "Ginger")
    _tick(cx, "Gut", cs.BOTANICALS, "Ginger")
    assert pr.cautions_for(pr.plan(cx, "a49"), pr.stored_frame(cx)[1]) == [
        ("Ginger", "Ginger thins blood.", ["Ginger"])]


def test_the_table_prints_without_its_intro_until_the_intro_is_approved(cx, tmp_path):
    lines = {"ingredient_caution_intro": {"text": "Some products share a caution.",
                                          "approved": False}}
    pr.load_content(cx, _folder(tmp_path, lines, cautions=BERBERINE))
    _remedy(cx, "Blood sugar", cs.FORMULATIONS, "Glucose Tolerance")
    page = _page(cx)
    assert "It lowers blood sugar." in page
    assert "Some products share a caution." not in page and "plan-caution-intro" not in page


@pytest.mark.parametrize("bad", ["{not json", json.dumps({"ingredient_cautions": {
    "A": {"text": "A.", "approved": True, "items": []}, "B": "not an object"}})])
def test_a_bad_file_fails_the_load_and_leaves_the_stored_set_alone(cx, tmp_path, bad):
    folder = _folder(tmp_path, cautions=BERBERINE)
    pr.load_content(cx, folder)
    before = pr.stored_frame(cx), pr.stored_wording(cx)
    (tmp_path / "plan-report" / "frame-lines.json").write_text(bad)
    with pytest.raises(Exception):
        pr.load_content(cx, folder)
    assert (pr.stored_frame(cx), pr.stored_wording(cx)) == before


def test_a_remedy_matches_whichever_remedy_section_it_lands_in(cx, tmp_path):
    """Clinical lists an infoceutical; the row ticks it with no catalog entry, so its
    name puts it under Formulations. And the other way round."""
    cautions = {"A": _caution("Caution A.", [{"section": cs.INFOCEUTICALS, "name": "Calm Spray"}]),
                "B": _caution("Caution B.", [{"section": cs.FORMULATIONS,
                                             "name": "ED11 Liver Energetic Driver Infoceutical"}])}
    pr.load_content(cx, _folder(tmp_path, cautions=cautions))
    save_selection(cx, "a49", "Gut", ["Calm Spray"])
    _remedy(cx, "Liver", cs.INFOCEUTICALS, "ED11 Liver Energetic Driver Infoceutical")
    groups = pr.plan(cx, "a49")
    assert [l["name"] for l in groups[cs.FORMULATIONS]] == ["Calm Spray"]
    assert [n for n, _, _ in pr.cautions_for(groups, pr.stored_frame(cx)[1])] == ["A", "B"]


def test_a_food_and_a_remedy_of_one_name_stay_apart(cx, tmp_path):
    cautions = {"A": _caution("Caution A.", [{"section": cs.FORMULATIONS, "name": "Ginger"}])}
    pr.load_content(cx, _folder(tmp_path, cautions=cautions))
    _tick(cx, "Gut", "foods_emphasize", "Ginger")
    assert "Caution A." not in _page(cx)


def test_a_prl_caution_matches_and_is_not_called_missing(cx, tmp_path):
    from dashboard import biofield_prl_section as prl
    prl.ensure_schema(cx)
    cx.execute("INSERT INTO biofield_prl_rating (focus_area_id,name_key,name,overall) "
               "VALUES (1,'liver tonic','Liver Tonic',7)")
    cx.execute("INSERT INTO biofield_prl_pick (test_id,focus_area_id,name_key,checked) "
               "VALUES ('a49',1,'liver tonic',1)")
    cautions = {"A": _caution("Caution A.", [{"section": "prl", "name": "Liver Tonic"}])}
    report = pr.load_content(cx, _folder(tmp_path, cautions=cautions))
    assert report["caution_items_not_in_catalog"] == []
    assert "Caution A." in _page(cx)


def test_an_approval_set_back_withdraws_the_line_and_the_caution(cx, tmp_path):
    folder = _folder(tmp_path, cautions=BERBERINE)
    pr.load_content(cx, folder)
    _remedy(cx, "Blood sugar", cs.FORMULATIONS, "Glucose Tolerance")
    lines = {k: {"text": v, "approved": k != "dose_totals"} for k, v in TOP.items()}
    _folder(tmp_path, lines, cautions={"Berberine": dict(BERBERINE["Berberine"], approved=False)})
    pr.load_content(cx, folder)
    page = _page(cx)
    assert "Count every source." not in page and "It lowers blood sugar." not in page
    assert "Space thyroid medicine." in page


def test_a_renamed_vertigo_row_keeps_the_line_and_a_dismissed_one_drops_it(cx, tmp_path):
    pr.load_content(cx, _folder(tmp_path))
    _tick(cx, "Vertigo", cs.NUTRIENTS, "Magnesium")
    entry = pr.plan(cx, "a49")[cs.NUTRIENTS][0]["entries"][0]
    entry["condition"] = "Vertigo and dizziness"
    assert pr._has_condition({cs.NUTRIENTS: [{"entries": [entry]}]}, "vertigo")
    from dashboard.biofield_clinical_proposals import decide
    decide(cx, "a49", "Vertigo", "dismissed")
    assert "Vertigo needs care." not in _page(cx)


def test_an_extra_explanation_remedy_is_known_and_matches(cx, tmp_path):
    """Soothe Powder lives only under a condition's extra_explanations."""
    save_selection(cx, "a49", "Gut", ["Soothe Powder"])
    cs.set_extra_explanations(cx, "Gut", {"Soothe Powder": {
        "section": cs.FORMULATIONS, "explanation": "Soothes.", "explanation_approved": True}})
    cautions = {"Fibres": _caution("Space them.", [{"section": cs.FORMULATIONS,
                                                    "name": "Soothe Powder"}])}
    report = pr.load_content(cx, _folder(tmp_path, cautions=cautions))
    assert report["caution_items_not_in_catalog"] == []
    assert "Space them." in _page(cx)


def test_a_reordered_reload_keeps_each_item_under_its_own_caution(cx, tmp_path):
    first = {"Berberine": _caution("Berberine text.", [{"section": cs.FORMULATIONS,
                                                       "name": "Glucose Tolerance"}]),
             "Iodine": _caution("Iodine text.", [{"section": cs.NUTRIENTS, "name": "Kelp"}])}
    folder = _folder(tmp_path, cautions=first)
    pr.load_content(cx, folder)
    _folder(tmp_path, cautions={"Iodine": first["Iodine"], "Berberine": first["Berberine"]})
    pr.load_content(cx, folder)
    _remedy(cx, "Blood sugar", cs.FORMULATIONS, "Glucose Tolerance")
    assert pr.cautions_for(pr.plan(cx, "a49"), pr.stored_frame(cx)[1]) == [
        ("Berberine", "Berberine text.", ["Glucose Tolerance"])]


def test_the_oils_header_heads_remedies_once_only_with_an_oil_after_the_infoceutical_one(
        cx, tmp_path):
    lines = {k: {"text": v, "approved": True} for k, v in TOP.items()}
    lines["essential_oils_header"] = {"text": "Oils <need> care.", "approved": True}
    pr.load_content(cx, _folder(tmp_path, lines=lines))
    _remedy(cx, "Gut", cs.FORMULATIONS, "Digestzymes")
    assert "need" not in _page(cx)
    _tick(cx, "Vertigo", cs.ESSENTIAL_OILS, "Lavender")
    page = _page(cx)
    assert page.count("Oils &lt;need&gt; care.") == 1
    assert page.index("<h2>Remedies</h2>") < page.index("Oils &lt;need&gt;") \
        < page.index("Lavender")
    _remedy(cx, "Liver", cs.INFOCEUTICALS, "ED11 Liver Energetic Driver Infoceutical")
    page = _page(cx)
    assert page.index("Infoceuticals are E4L") < page.index("Oils &lt;need&gt;") \
        < page.index("Lavender")


def test_an_unapproved_oils_header_never_prints(cx, tmp_path):
    lines = {"essential_oils_header": {"text": "Oils need care.", "approved": False}}
    pr.load_content(cx, _folder(tmp_path, lines=lines))
    _tick(cx, "Vertigo", cs.ESSENTIAL_OILS, "Lavender")
    assert "Oils need care." not in _page(cx)
