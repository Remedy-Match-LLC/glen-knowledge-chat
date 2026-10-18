"""Five rated sections in each Clinical Summary condition (Glen, 2026-10-17).

Spec: vault clinical/specs/2026-10-17-clinical-summary-five-sections.md.
"""
import json
import sqlite3

import pytest

from dashboard import biofield_clinical_sections as cs
from dashboard.biofield_clinical_checklist import (
    custom_remedies, forget_remedy, remember_remedies)
from dashboard.biofield_report_html import render_clinical_checklist


@pytest.fixture
def cx(tmp_path):
    con = sqlite3.connect(str(tmp_path / "chat_log.db"))
    yield con
    con.close()


def _item(name, o, r, u, **extra):
    return dict({"name": name, "overall": o, "research": r, "uniqueness": u,
                 "reason": "why", "source": "src", "botanical": False,
                 "lowers_bp": None}, **extra)


# --- the rating -----------------------------------------------------------

def test_ten_shows_as_x():
    assert cs.rating_code(10, 7, 4) == "X74"
    assert cs.rating_code(9, 10, 10) == "9XX"


def test_a_partial_rating_shows_blank_rather_than_a_guess():
    assert cs.rating_code(8, None, 3) == ""


def test_parse_rating_reads_x_and_refuses_anything_else():
    assert cs.parse_rating("x74") == (10, 7, 4)
    for bad in ("074", "X7", "X745", "a74", "", None):
        assert cs.parse_rating(bad) is None


def test_x74_sorts_above_974_and_research_then_uniqueness_break_ties():
    items = [_item("a", 9, 7, 4), _item("b", 10, 7, 4), _item("c", 9, 8, 1),
             _item("d", 9, 8, 2), _item("e", None, None, None)]
    assert [i["name"] for i in cs.sort_items(items)] == ["b", "d", "c", "a", "e"]


# --- shared catalog and per-client picks ----------------------------------

def test_new_items_start_unchecked(cx):
    cs.upsert_item(cx, "Gut", "foods_emphasize", _item("Bone broth", 8, 4, 6))
    cs.upsert_item(cx, "Gut", "nutrients", _item("L-glutamine", 9, 7, 8, dose="5 g daily"))
    sections = cs.sections_for(cx, "a49", "Gut")
    assert [s["title"] for s in sections] == [
        "Functional Formulations", "Infoceuticals", "Foods to emphasize",
        "Foods to avoid", "Nutrients", "Fullscript supplements"]
    items = [i for s in sections for i in s["items"]]
    assert items and not any(i["checked"] for i in items)


def test_a_tick_and_an_edited_score_belong_to_one_client_only(cx):
    cs.upsert_item(cx, "Gut", "nutrients", _item("Zinc carnosine", 7, 6, 5, dose="75 mg"))
    cs.upsert_item(cx, "Gut", "nutrients", _item("L-glutamine", 8, 7, 8, dose="5 g"))
    cs.save_pick(cx, "a49", "Gut", "nutrients", "Zinc carnosine",
                 checked=True, rating="X95", dose="37.5 mg")
    mine = cs.sections_for(cx, "a49", "Gut")[4]["items"]
    theirs = cs.sections_for(cx, "a50", "Gut")[4]["items"]
    assert [(i["name"], i["rating"], i["dose"], i["checked"]) for i in mine] == [
        ("Zinc carnosine", "X95", "37.5 mg", True), ("L-glutamine", "878", "5 g", False)]
    assert [(i["name"], i["rating"], i["checked"]) for i in theirs] == [
        ("L-glutamine", "878", False), ("Zinc carnosine", "765", False)]


def test_saving_one_field_keeps_the_others(cx):
    cs.upsert_item(cx, "Gut", "nutrients", _item("Zinc carnosine", 7, 6, 5, dose="75 mg"))
    cs.save_pick(cx, "a49", "Gut", "nutrients", "Zinc carnosine", checked=True)
    cs.save_pick(cx, "a49", "Gut", "nutrients", "Zinc carnosine", dose="50 mg")
    item = cs.sections_for(cx, "a49", "Gut")[4]["items"][0]
    assert item["checked"] and item["dose"] == "50 mg" and item["rating"] == "765"


def test_a_bad_rating_is_refused(cx):
    with pytest.raises(ValueError):
        cs.save_pick(cx, "a49", "Gut", "nutrients", "Zinc", rating="0X1")


def test_formulation_ticks_stay_in_the_row_remedy_store(cx):
    with pytest.raises(ValueError):
        cs.save_pick(cx, "a49", "Gut", cs.FORMULATIONS, "GI Repair", checked=True)


def test_formulations_keep_the_row_list_and_sort_rated_first(cx):
    cs.upsert_item(cx, "Gut", cs.FORMULATIONS, _item("GI Repair", 9, 3, 7))
    cs.upsert_item(cx, "Gut", cs.FORMULATIONS, _item("Terrain Restore", 10, 2, 5))
    sec = cs.sections_for(cx, "a49", "Gut",
                          remedies=["Old One", "GI Repair", "Terrain Restore"],
                          chosen_remedies=["GI Repair"])[0]
    assert [(i["name"], i["rating"], i["checked"]) for i in sec["items"]] == [
        ("Terrain Restore", "X25", False), ("GI Repair", "937", True), ("Old One", "", False)]


def test_checked_items_lists_only_ticked_non_formulation_items(cx):
    cs.upsert_item(cx, "Gut", "foods_avoid", _item("Gluten", 9, 6, 5))
    cs.upsert_item(cx, "Gut", "nutrients", _item("Zinc carnosine", 7, 6, 5, dose="75 mg"))
    cs.save_pick(cx, "a49", "Gut", "foods_avoid", "Gluten", checked=True)
    cs.save_pick(cx, "a49", "Gut", "nutrients", "Zinc carnosine", dose="50 mg")
    assert cs.checked_items(cx, "a49") == [
        {"item_key": "gut", "section": "foods_avoid", "name": "Gluten", "brand": "",
         "dose": ""}]


def test_the_fullscript_link_is_the_store_front_door(monkeypatch):
    monkeypatch.delenv("FULLSCRIPT_DISPENSARY_SLUG", raising=False)
    assert cs.fullscript_store_url() == \
        "https://us.fullscript.com/welcome/remedymatch/store-start"


# --- the page -------------------------------------------------------------

def _rendered(cx, **pick):
    cs.upsert_item(cx, "Gut", cs.FORMULATIONS, _item("GI Repair", 9, 3, 7))
    cs.upsert_item(cx, "Gut", "nutrients", _item("L-glutamine", 10, 7, 8, dose="5 g daily",
                                                 lowers_bp=True))
    cs.upsert_item(cx, "Gut", cs.FULLSCRIPT, _item("GI Revive", 8, 5, 5, brand="Designs for Health"))
    if pick:
        cs.save_pick(cx, "a49", "Gut", "nutrients", "L-glutamine", **pick)
    remedies = ["GI Repair"]
    item = {"label": "Gut", "common_remedies": remedies, "selection_saved": True,
            "selected_remedies": ["GI Repair"],
            "sections": cs.sections_for(cx, "a49", "Gut", remedies, ["GI Repair"])}
    return render_clinical_checklist([item])


def test_page_shows_five_titles_ratings_dose_and_flags(cx):
    html = _rendered(cx)
    for title in ("Functional Formulations", "Infoceuticals", "Foods to emphasize", "Foods to avoid",
                  "Nutrients", "Fullscript supplements"):
        assert f"<div class=clinical-section-title>{title}</div>" in html
    assert 'value="X78"' in html and 'value="937"' in html
    assert 'class=clinical-section-dose value="5 g daily"' in html
    assert "lowers BP" in html


def test_formulation_ticks_still_drive_the_row_remedy_handler(cx):
    html = _rendered(cx)
    assert ('class=clinical-remedy-choice type=checkbox value="GI Repair" checked '
            'onchange=selectClinicalRemedy(this)') in html
    # The new sections must never be swept into the row's remedy list.
    assert 'clinical-remedy-choice type=checkbox value="L-glutamine"' not in html


def test_nutrient_tick_renders_after_a_reload(cx):
    html = _rendered(cx, checked=True)
    assert ('data-section="nutrients" data-name="L-glutamine" checked '
            'onchange=saveClinicalSectionItem') in html


def test_fullscript_items_link_only_to_the_front_door(cx, monkeypatch):
    monkeypatch.delenv("FULLSCRIPT_DISPENSARY_SLUG", raising=False)
    html = _rendered(cx)
    assert ('<a href="https://us.fullscript.com/welcome/remedymatch/store-start" '
            'target=_blank rel=noopener>GI Revive</a>') in html
    assert "Designs for Health" in html
    assert "/catalog/" not in html and "/products/" not in html


def test_a_row_without_sections_renders_as_before():
    html = render_clinical_checklist([{"label": "Gut", "common_remedies": ["GI Repair"]}])
    assert "<div class=clinical-section-title>" not in html
    assert 'value="GI Repair"' in html


# --- the route ------------------------------------------------------------

def test_pick_route_saves_and_refuses_bad_input(tmp_path, monkeypatch):
    # The console-key gate is tested in test_biofield_local_app; clear it here.
    monkeypatch.delenv("CONSOLE_SECRET", raising=False)
    import dashboard as _d
    monkeypatch.setattr(_d, "CONSOLE_SECRET", "", raising=False)
    from biofield_local_app import create_app
    db = str(tmp_path / "chat_log.db")
    with sqlite3.connect(db) as con:
        cs.upsert_item(con, "Gut", "nutrients", _item("Zinc", 7, 6, 5, dose="75 mg"))
    client = create_app(db).test_client()
    url = "/author/a49/clinical-sections/pick"
    ok = client.post(url, json={"label": "Gut", "section": "nutrients", "name": "Zinc",
                                "checked": True, "rating": "X11"})
    assert ok.status_code == 200 and ok.get_json()["ok"]
    assert client.post(url, json={"label": "Gut", "section": "nutrients", "name": "Zinc",
                                  "rating": "abc"}).status_code == 400
    assert client.post(url, json={"label": "Gut", "section": "bogus", "name": "Zinc",
                                  "checked": True}).status_code == 400
    assert client.post(url, json={"label": "Gut", "section": "nutrients", "name": "Zinc",
                                  "checked": "yes"}).status_code == 400
    with sqlite3.connect(db) as con:
        item = cs.sections_for(con, "a49", "Gut")[4]["items"][0]
    assert item["checked"] and item["rating"] == "X11"


# --- the loader -----------------------------------------------------------

def _content(tmp_path):
    folder = tmp_path / "content"
    folder.mkdir()
    (folder / "gut.json").write_text(json.dumps({
        "condition_key": "gut", "label": "Gut", "sections": {
            "functional_formulations": [_item("GI Repair", 9, 3, 7),
                                        _item("Glen Deleted This", 8, 2, 2)],
            "foods_emphasize": [_item("Bone broth", 8, 4, 6, josie_note="ok for her")],
            "foods_avoid": [_item("Gluten", 9, 6, 5)],
            "nutrients": [_item("L-glutamine", 9, 7, 8, dose="5 g")],
            "fullscript_wanted": [{"product_type": "glutamine powder"}]}}))
    (folder / "fullscript-matches.json").write_text(json.dumps({"glutamine powder": []}))
    return str(folder)


def test_loader_dry_run_keeps_nothing(tmp_path):
    from scripts.load_clinical_sections import load
    db = str(tmp_path / "chat_log.db")
    folder = _content(tmp_path)
    con = sqlite3.connect(db)
    report = load(con, folder, client_test="a49")
    con.rollback()
    assert report["files"] == 1 and report["skipped_files"] == ["fullscript-matches.json"]
    assert report["added"] == {"functional_formulations": 2, "foods_emphasize": 1,
                               "foods_avoid": 1, "nutrients": 1}
    assert report["fullscript_types_not_loaded"] == 1 and report["notes"] == 1
    assert con.execute("SELECT COUNT(*) FROM biofield_clinical_section_item").fetchone() == (0,)
    assert con.execute("SELECT COUNT(*) FROM biofield_clinical_section_pick").fetchone() == (0,)
    assert custom_remedies(con, "Gut") == []


def test_loader_never_restores_a_remedy_glen_deleted(tmp_path):
    from scripts.load_clinical_sections import load
    db = str(tmp_path / "chat_log.db")
    con = sqlite3.connect(db)
    remember_remedies(con, "Gut", ["Glen Deleted This"])
    forget_remedy(con, "Gut", "Glen Deleted This")
    report = load(con, _content(tmp_path), client_test="a49")
    con.commit()
    assert report["remedies_kept_deleted"] == ["Gut: Glen Deleted This"]
    assert report["formulations_new_to_condition"] == ["Gut: GI Repair"]
    # The shared remedy list is never written; the deleted one stays deleted.
    assert custom_remedies(con, "Gut") == []
    names = [i["name"] for i in cs.sections_for(con, "a49", "Gut")[0]["items"]]
    assert names == ["GI Repair"]
    # The note is Josie's alone, and loading ticks nothing.
    assert con.execute("SELECT test_id,checked,note FROM biofield_clinical_section_pick"
                       ).fetchall() == [("a49", None, "ok for her")]
    # Not yet reviewed, so another client sees none of it.
    assert all(s["items"] == [] for s in cs.sections_for(con, "a50", "Gut"))


def test_loader_is_idempotent(tmp_path):
    from scripts.load_clinical_sections import load
    con = sqlite3.connect(str(tmp_path / "chat_log.db"))
    folder = _content(tmp_path)
    load(con, folder, "a49")
    con.commit()
    again = load(con, folder, "a49")
    assert again["added"] == {}
    assert sum(again["updated"].values()) == 5


# --- not reviewed (Glen, 2026-10-17: "all three") --------------------------

def test_an_unreviewed_item_shows_only_on_its_own_client_until_approved(cx):
    cs.upsert_item(cx, "Gut", "foods_avoid", _item("Gluten", 9, 6, 5), origin_test="a49")
    mine = cs.sections_for(cx, "a49", "Gut")[3]["items"]
    assert [(i["name"], i["reviewed"]) for i in mine] == [("Gluten", False)]
    assert cs.sections_for(cx, "a50", "Gut")[3]["items"] == []
    assert cs.approve_item(cx, "Gut", "foods_avoid", "Gluten")
    assert [(i["name"], i["reviewed"]) for i in cs.sections_for(cx, "a50", "Gut")[3]["items"]] \
        == [("Gluten", True)]


def test_reloading_content_never_unapproves_or_moves_an_item(cx):
    cs.upsert_item(cx, "Gut", "foods_avoid", _item("Gluten", 9, 6, 5), origin_test="a49")
    cs.approve_item(cx, "Gut", "foods_avoid", "Gluten")
    cs.upsert_item(cx, "Gut", "foods_avoid", _item("Gluten", 8, 6, 5), origin_test="a77")
    item = cs.sections_for(cx, "a50", "Gut")[3]["items"][0]
    assert item["reviewed"] and item["rating"] == "865"


def test_an_unreviewed_formulation_joins_only_its_own_clients_row(cx):
    cs.upsert_item(cx, "Gut", cs.FORMULATIONS, _item("GI Repair", 9, 3, 7), origin_test="a49")
    assert [i["name"] for i in cs.sections_for(cx, "a49", "Gut", ["Old One"])[0]["items"]] \
        == ["GI Repair", "Old One"]
    assert [i["name"] for i in cs.sections_for(cx, "a50", "Gut", ["Old One"])[0]["items"]] \
        == ["Old One"]


def test_a_formulation_glen_deletes_stays_gone(cx):
    cs.upsert_item(cx, "Gut", cs.FORMULATIONS, _item("GI Repair", 9, 3, 7), origin_test="a49")
    forget_remedy(cx, "Gut", "GI Repair")
    assert cs.sections_for(cx, "a49", "Gut")[0]["items"] == []


def test_approve_route_and_page_marker(cx, tmp_path, monkeypatch):
    monkeypatch.delenv("CONSOLE_SECRET", raising=False)
    import dashboard as _d
    monkeypatch.setattr(_d, "CONSOLE_SECRET", "", raising=False)
    from biofield_local_app import create_app
    db = str(tmp_path / "route.db")
    with sqlite3.connect(db) as con:
        cs.upsert_item(con, "Gut", "foods_avoid", _item("Gluten", 9, 6, 5), origin_test="a49")
        html = render_clinical_checklist([{"label": "Gut", "common_remedies": [],
                                           "sections": cs.sections_for(con, "a49", "Gut")}])
    assert "not reviewed</span>" in html and "onclick=approveClinicalSectionItem(this)" in html
    client = create_app(db).test_client()
    url = "/author/a49/clinical-sections/approve"
    assert client.post(url, json={"label": "Gut", "section": "foods_avoid",
                                  "name": "Nope"}).status_code == 404
    assert client.post(url, json={"label": "Gut", "section": "foods_avoid",
                                  "name": "Gluten"}).get_json()["ok"]
    with sqlite3.connect(db) as con:
        html = render_clinical_checklist([{"label": "Gut", "common_remedies": [],
                                           "sections": cs.sections_for(con, "a50", "Gut")}])
    assert "Gluten" in html and "not reviewed</span>" not in html


# --- avoids botanicals -----------------------------------------------------

def _botanicals(cx):
    for name, bot in (("Ginger root", True), ("Milk thistle seed", True),
                      ("Turmeric", True), ("Carrots", False)):
        cs.upsert_item(cx, "Gut", "foods_emphasize", _item(name, 8, 5, 5, botanical=bot))


def test_the_switch_hides_botanicals_except_named_ones_and_ticked_ones(cx):
    _botanicals(cx)
    cs.upsert_item(cx, "Gut", "nutrients", _item("Berberine", 9, 8, 6, botanical=True, dose="500 mg"))
    cs.save_pick(cx, "a49", "Gut", "nutrients", "Berberine", checked=True)
    cs.save_botanical_setting(cx, "a49", avoid=True, exceptions="ginger, milk thistle")
    sec = cs.sections_for(cx, "a49", "Gut")
    assert sorted(i["name"] for i in sec[2]["items"]) == ["Carrots", "Ginger root", "Milk thistle seed"]
    assert sec[2]["botanical_hidden"] == 1
    assert [i["name"] for i in sec[4]["items"]] == ["Berberine"]


def test_the_switch_belongs_to_one_client_and_is_off_by_default(cx):
    _botanicals(cx)
    cs.save_botanical_setting(cx, "a49", avoid=True, exceptions="")
    assert len(cs.sections_for(cx, "a50", "Gut")[2]["items"]) == 4
    assert cs.botanical_setting(cx, "a50") == {"avoid": False, "exceptions": ""}
    cs.save_botanical_setting(cx, "a49", exceptions="ginger")
    assert cs.botanical_setting(cx, "a49") == {"avoid": True, "exceptions": "ginger"}


def test_switch_renders_and_hidden_count_shows(cx):
    _botanicals(cx)
    cs.save_botanical_setting(cx, "a49", avoid=True, exceptions="ginger")
    setting = cs.botanical_setting(cx, "a49")
    html = render_clinical_checklist(
        [{"label": "Gut", "common_remedies": [],
          "sections": cs.sections_for(cx, "a49", "Gut", botanical=setting)}],
        botanical=setting)
    assert "id=clinicalAvoidBotanicals checked" in html
    assert 'id=clinicalBotanicalExceptions value="ginger"' in html
    assert "2 botanical items hidden for this client" in html


# --- food conflicts --------------------------------------------------------

def test_a_food_one_checked_row_emphasizes_and_another_avoids_is_flagged_on_both(cx):
    cs.upsert_item(cx, "Gut", "foods_emphasize", _item("Fermented foods", 8, 5, 5))
    cs.upsert_item(cx, "Infections", "foods_avoid", _item("Fermented Foods", 7, 4, 4))
    cs.upsert_item(cx, "Vertigo", "foods_avoid", _item("Fermented foods", 7, 4, 4))
    gut, inf, ver = (cs.sections_for(cx, "a49", n) for n in ("Gut", "Infections", "Vertigo"))
    count = cs.flag_food_conflicts([("Gut", True, gut), ("Infections", True, inf),
                                    ("Vertigo", False, ver)])
    assert count == 1
    assert gut[2]["items"][0]["conflicts"] == ["Infections"]
    assert inf[3]["items"][0]["conflicts"] == ["Gut"]
    assert ver[3]["items"][0]["conflicts"] == []
    html = render_clinical_checklist([{"label": "Gut", "common_remedies": [], "sections": gut}])
    assert "conflicts with Infections" in html


def test_no_conflict_when_one_row_is_unchecked(cx):
    cs.upsert_item(cx, "Gut", "foods_emphasize", _item("Eggs", 8, 5, 5))
    cs.upsert_item(cx, "Infections", "foods_avoid", _item("Eggs", 7, 4, 4))
    gut, inf = cs.sections_for(cx, "a49", "Gut"), cs.sections_for(cx, "a49", "Infections")
    assert cs.flag_food_conflicts([("Gut", True, gut), ("Infections", False, inf)]) == 0


def test_conflicts_match_on_food_keys_when_names_are_phrases(cx):
    cs.upsert_item(cx, "Blood", "foods_emphasize",
                   _item("Black or green tea with meals", 7, 4, 4, food_keys=["black tea", "green tea"]))
    cs.upsert_item(cx, "Vertigo", "foods_avoid",
                   _item("Caffeine: coffee, black tea, cola", 8, 5, 4,
                         food_keys=["caffeine", "coffee", "Black tea", "cola"]))
    blood, ver = cs.sections_for(cx, "a49", "Blood"), cs.sections_for(cx, "a49", "Vertigo")
    assert cs.flag_food_conflicts([("Blood", True, blood), ("Vertigo", True, ver)]) == 1
    assert blood[2]["items"][0]["conflicts"] == ["Vertigo"]


# --- the All box ----------------------------------------------------------
# Glen, 2026-10-17: "Check the box and all the items in that group are added.
# Uncheck, and only the ones individually checked are added. Unchecking doesn't
# remove checks from those checked manually."

def _foods(cx):
    for name in ("Kale", "Beets", "Garlic"):
        cs.upsert_item(cx, "Gut", "foods_emphasize", _item(name, 8, 5, 5))


def _ticked(cx, section="foods_emphasize", test_id="a49"):
    sec = next(s for s in cs.sections_for(cx, test_id, "Gut") if s["key"] == section)
    return {i["name"] for i in sec["items"] if i["checked"]}, sec["all_on"]


def test_all_on_ticks_every_shown_item(cx):
    _foods(cx)
    cs.select_all(cx, "a49", "Gut", "foods_emphasize", True, ["Kale", "Beets", "Garlic"])
    assert _ticked(cx) == ({"Kale", "Beets", "Garlic"}, True)


def test_all_off_keeps_the_items_ticked_by_hand(cx):
    _foods(cx)
    cs.save_pick(cx, "a49", "Gut", "foods_emphasize", "Beets", checked=True)
    cs.select_all(cx, "a49", "Gut", "foods_emphasize", True, ["Kale", "Beets", "Garlic"])
    cs.select_all(cx, "a49", "Gut", "foods_emphasize", False, ["Kale", "Beets", "Garlic"])
    assert _ticked(cx) == ({"Beets"}, False)


def test_an_item_reticked_by_hand_after_all_survives_all_off(cx):
    _foods(cx)
    cs.select_all(cx, "a49", "Gut", "foods_emphasize", True, ["Kale", "Beets", "Garlic"])
    cs.save_pick(cx, "a49", "Gut", "foods_emphasize", "Kale", checked=False)
    assert _ticked(cx)[1] is False      # unticking an item unticks the All box
    cs.save_pick(cx, "a49", "Gut", "foods_emphasize", "Kale", checked=True)
    cs.select_all(cx, "a49", "Gut", "foods_emphasize", False, [])
    assert _ticked(cx) == ({"Kale"}, False)


def test_all_ticks_only_the_names_shown_and_only_catalog_items(cx):
    _foods(cx)
    cs.select_all(cx, "a49", "Gut", "foods_emphasize", True, ["Kale", "Not listed"])
    assert _ticked(cx)[0] == {"Kale"}


def test_all_belongs_to_one_section_and_one_client(cx):
    _foods(cx)
    cs.upsert_item(cx, "Gut", "foods_avoid", _item("Sugar", 8, 5, 5))
    cs.select_all(cx, "a49", "Gut", "foods_emphasize", True, ["Kale"])
    assert _ticked(cx, "foods_avoid") == (set(), False)
    assert _ticked(cx, test_id="a50") == (set(), False)


def test_all_on_formulations_adds_to_the_row_remedies_and_off_takes_back_only_those(cx):
    chosen = cs.select_all(cx, "a49", "Gut", cs.FORMULATIONS, True,
                           ["GI Repair", "Liver Lift"], ["GI Repair"])
    assert chosen == ["GI Repair", "Liver Lift"]
    assert cs.select_all(cx, "a49", "Gut", cs.FORMULATIONS, False,
                         ["GI Repair", "Liver Lift"], chosen) == ["GI Repair"]


def test_a_formulation_unticked_then_reticked_by_hand_survives_all_off(cx):
    chosen = cs.select_all(cx, "a49", "Gut", cs.FORMULATIONS, True,
                           ["GI Repair", "Liver Lift"], [])
    cs.remedies_changed(cx, "a49", "Gut", chosen, ["GI Repair"])
    sec = cs.sections_for(cx, "a49", "Gut", ["GI Repair", "Liver Lift"], ["GI Repair"])[0]
    assert sec["all_on"] is False
    cs.remedies_changed(cx, "a49", "Gut", ["GI Repair"], ["GI Repair", "Liver Lift"])
    assert cs.select_all(cx, "a49", "Gut", cs.FORMULATIONS, False, [],
                         ["GI Repair", "Liver Lift"]) == ["Liver Lift"]


def test_all_box_renders_with_its_state(cx):
    html = _rendered(cx)
    assert html.count("class=clinical-section-allbox") == 3   # three sections have items
    cs.select_all(cx, "a49", "Gut", "nutrients", True, ["L-glutamine"])
    html = _rendered(cx)
    assert ('class=clinical-section-allbox data-section="nutrients" checked '
            'onchange=clinicalSectionAll(this)') in html


def test_all_route_saves_formulations_as_the_row_remedies(tmp_path, monkeypatch):
    monkeypatch.delenv("CONSOLE_SECRET", raising=False)
    import dashboard as _d
    monkeypatch.setattr(_d, "CONSOLE_SECRET", "", raising=False)
    from biofield_local_app import create_app
    from dashboard.biofield_clinical_proposals import selections
    db = str(tmp_path / "chat_log.db")
    with sqlite3.connect(db) as con:
        _foods(con)
    client = create_app(db).test_client()
    url = "/author/a49/clinical-sections/all"
    ok = client.post(url, json={"label": "Gut", "section": cs.FORMULATIONS, "on": True,
                                "names": ["GI Repair", "Liver Lift"], "remedies": []})
    assert ok.status_code == 200 and ok.get_json()["ok"]
    assert client.post(url, json={"label": "Gut", "section": "foods_emphasize",
                                  "on": True, "names": ["Kale", "Beets", "Garlic"]}
                       ).status_code == 200
    assert client.post(url, json={"label": "Gut", "section": "bogus", "on": True,
                                  "names": []}).status_code == 400
    assert client.post(url, json={"label": "Gut", "section": "foods_emphasize",
                                  "on": "yes", "names": []}).status_code == 400
    # A remedy unticked by hand through the existing route unticks the All box.
    client.post("/author/a49/clinical-items/selection",
                json={"label": "Gut", "remedies": ["GI Repair"]})
    with sqlite3.connect(db) as con:
        assert selections(con, "a49")["gut"] == ["GI Repair"]
        assert cs.sections_for(con, "a49", "Gut")[0]["all_on"] is False
        assert _ticked(con) == ({"Kale", "Beets", "Garlic"}, True)


def test_all_box_reads_off_when_an_item_arrives_after_it_was_ticked(cx):
    _foods(cx)
    cs.select_all(cx, "a49", "Gut", "foods_emphasize", True, ["Kale", "Beets", "Garlic"])
    cs.upsert_item(cx, "Gut", "foods_emphasize", _item("Leeks", 8, 5, 5))
    assert _ticked(cx) == ({"Kale", "Beets", "Garlic"}, False)
