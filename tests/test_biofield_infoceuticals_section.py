"""The Infoceuticals section and its scan marks (Glen, 2026-10-17).

"Infoceuticals need a separate section from functional formulations ... Indicate
which infoceuticals are among the 5 recommended, and also which are purple."
And: "MR's are not infoceuticals."
"""
import json
import os
import sqlite3

import pytest

from dashboard import biofield_clinical_sections as cs
from dashboard import e4l_scan_marks as marks_mod
from dashboard.biofield_report_html import render_clinical_checklist

ED5 = "ED5 Circulation Energetic Driver Infoceutical"
EI10 = "EI10 Circulation/Pericardium Meridian Energetic Integrator Infoceutical"


@pytest.fixture
def cx(tmp_path):
    con = sqlite3.connect(str(tmp_path / "chat_log.db"))
    yield con
    con.close()


def _item(name, o=8, r=1, u=6):
    return {"name": name, "overall": o, "research": r, "uniqueness": u,
            "reason": "why", "source": "src", "botanical": False, "lowers_bp": None}


def _by_key(sections):
    return {s["key"]: s for s in sections}


def test_which_names_are_infoceuticals():
    assert cs.is_infoceutical(ED5) and cs.is_infoceutical("ET16 Glial Cell")
    assert cs.is_infoceutical("MB8 Love") and cs.is_infoceutical(
        "ESR Emotional Stress Release Infoceutical")
    for name in ("MR3 Restore Matrix", "MR3 Restore Matrix Infoceutical", "MR10", "ER20 Something", "Fibrosolve",
                 "Iron Out", "Educated Choice"):
        assert not cs.is_infoceutical(name), name
    assert cs.infoceutical_code(EI10) == "EI10"
    assert cs.infoceutical_code("ESR Emotional Stress Release Infoceutical") == "ESR"


def test_the_row_remedies_split_into_two_sections(cx):
    secs = _by_key(cs.sections_for(cx, "a49", "Blood", ["Fibrosolve", ED5], [ED5]))
    assert [i["name"] for i in secs["functional_formulations"]["items"]] == ["Fibrosolve"]
    info = secs["infoceuticals"]["items"]
    assert [(i["name"], i["checked"], i["code"]) for i in info] == [(ED5, True, "ED5")]


def test_an_infoceutical_loaded_under_formulations_moves_and_keeps_its_rating(cx):
    cs.upsert_item(cx, "Blood", cs.FORMULATIONS, _item(ED5, 7, 1, 6))
    cs.save_pick(cx, "a49", "Blood", cs.FORMULATIONS, ED5, note="older note")
    secs = _by_key(cs.sections_for(cx, "a49", "Blood"))
    assert secs["functional_formulations"]["items"] == []
    entry = secs["infoceuticals"]["items"][0]
    assert entry["rating"] == "716" and entry["note"] == "older note"


def test_marks_recommended_and_purple(cx):
    cs.upsert_item(cx, "Blood", cs.INFOCEUTICALS, _item(EI10))
    marks = {"recommended": {"EI10"}, "purple": {"EI10", "ED5"}, "purple_error": ""}
    info = _by_key(cs.sections_for(cx, "a49", "Blood", [ED5], [], marks=marks))["infoceuticals"]
    got = {i["code"]: (i["recommended"], i["purple"]) for i in info["items"]}
    assert got == {"EI10": (True, True), "ED5": (False, True)}


def test_no_scan_means_no_marks_and_an_unread_pdf_is_not_none_purple(cx):
    info = _by_key(cs.sections_for(cx, "a49", "Blood", [ED5], []))["infoceuticals"]
    assert "recommended" not in info["items"][0] and "purple" not in info["items"][0]
    marks = {"recommended": set(), "purple": None, "purple_error": "no PDF"}
    info = _by_key(cs.sections_for(cx, "a49", "Blood", [ED5], [], marks=marks))["infoceuticals"]
    assert info["items"][0]["purple"] is None and info["purple_error"] == "no PDF"


def _page(cx, marks):
    remedies = ["Fibrosolve", ED5, EI10]
    item = {"label": "Blood", "common_remedies": remedies, "selection_saved": True,
            "selected_remedies": [ED5],
            "sections": cs.sections_for(cx, "a49", "Blood", remedies, [ED5], marks=marks)}
    return render_clinical_checklist([item])


def test_page_shows_the_section_tags_and_ticks_as_remedies(cx):
    html = _page(cx, {"recommended": {"EI10"}, "purple": {"ED5"}, "purple_error": ""})
    assert "<div class=clinical-section-title>Infoceuticals</div>" in html
    info = html[html.index('data-section="infoceuticals"'):]
    assert info.count(">recommended</span>") == 1 and info.count(">purple</span>") == 1
    assert (f'class=clinical-remedy-choice type=checkbox value="{ED5}" checked '
            'onchange=selectClinicalRemedy(this)') in html
    assert "could not be read" not in html


def test_page_says_when_purple_could_not_be_read(cx):
    html = _page(cx, {"recommended": {"EI10"}, "purple": None, "purple_error": "gone"})
    assert "Purple could not be read from the scan PDF" in html
    assert ">purple</span>" not in html


def test_infoceutical_ticks_are_refused_outside_the_remedy_store(cx):
    with pytest.raises(ValueError):
        cs.save_pick(cx, "a49", "Blood", cs.INFOCEUTICALS, ED5, checked=True)


def test_all_box_on_infoceuticals_is_its_own(cx):
    chosen = cs.select_all(cx, "a49", "Blood", cs.INFOCEUTICALS, True, [ED5, EI10],
                           ["Fibrosolve"])
    assert chosen == ["Fibrosolve", ED5, EI10]
    secs = _by_key(cs.sections_for(cx, "a49", "Blood", ["Fibrosolve", ED5, EI10], chosen))
    assert secs["infoceuticals"]["all_on"] and not secs["functional_formulations"]["all_on"]
    # Unticking Fibrosolve by hand leaves the Infoceuticals box alone.
    cs.remedies_changed(cx, "a49", "Blood", chosen, [ED5, EI10])
    secs = _by_key(cs.sections_for(cx, "a49", "Blood", ["Fibrosolve", ED5, EI10], [ED5, EI10]))
    assert secs["infoceuticals"]["all_on"]
    # Unticking an infoceutical by hand unticks its box.
    cs.remedies_changed(cx, "a49", "Blood", [ED5, EI10], [EI10])
    secs = _by_key(cs.sections_for(cx, "a49", "Blood", ["Fibrosolve", ED5, EI10], [EI10]))
    assert not secs["infoceuticals"]["all_on"]
    # Ticking it again by hand does not bring the box back: it is a hand tick now.
    cs.remedies_changed(cx, "a49", "Blood", [EI10], [EI10, ED5])
    secs = _by_key(cs.sections_for(cx, "a49", "Blood", ["Fibrosolve", ED5, EI10], [EI10, ED5]))
    assert not secs["infoceuticals"]["all_on"]
    assert cs.select_all(cx, "a49", "Blood", cs.INFOCEUTICALS, False, [],
                         [EI10, ED5]) == [ED5]


def test_loader_reads_the_infoceuticals_key(tmp_path):
    from scripts.load_clinical_sections import load
    folder = tmp_path / "content"
    folder.mkdir()
    (folder / "blood.json").write_text(json.dumps({
        "condition_key": "blood", "label": "Blood", "sections": {
            "functional_formulations": [_item("Fibrosolve")],
            "infoceuticals": [dict(_item(EI10), scan_recommended=True)]}}))
    con = sqlite3.connect(str(tmp_path / "chat_log.db"))
    report = load(con, str(folder), "a49")
    assert report["added"] == {"functional_formulations": 1, "infoceuticals": 1}
    assert f"Blood: {EI10}" in report["formulations_new_to_condition"]
    info = _by_key(cs.sections_for(con, "a49", "Blood"))["infoceuticals"]["items"]
    assert [i["name"] for i in info] == [EI10]


# --- reading the scan -----------------------------------------------------

def _e4l(tmp_path, pdf_path):
    path = str(tmp_path / "e4l.db")
    with sqlite3.connect(path) as e:
        e.execute("CREATE TABLE e4l_scans (scan_id, client_id, scan_date, pdf_filename, "
                  "raw_pdf_path, notes)")
        e.execute("CREATE TABLE e4l_scan_results (id, scan_id, item_code, priority_rank, "
                  "protocol_days, score, section_context, notes)")
        e.execute("INSERT INTO e4l_scans VALUES (7,1,'2026-10-11','x.pdf',?,'')", (pdf_path,))
        for rank, (code, ctx) in enumerate([("ED12", "Infoceuticals"), ("EI10", "Infoceuticals"),
                                            ("ER20", "miHealth Functions")], 1):
            e.execute("INSERT INTO e4l_scan_results VALUES (?,7,?,?,15,'',?,'')",
                      (rank, code, rank, ctx))
    return path


def test_marks_come_from_the_scan_and_the_pdf_is_read_once(cx, tmp_path, monkeypatch):
    reads = []
    monkeypatch.setattr(marks_mod, "bar_colours",
                        lambda p: reads.append(p) or {"ED12": {"purple"}, "ED1": {"green"},
                                                      "ES12": {"green", "purple"}})
    e4l = _e4l(tmp_path, "/scans/x.pdf")
    first = marks_mod.infoceutical_marks(cx, e4l, 7)
    again = marks_mod.infoceutical_marks(cx, e4l, 7)
    assert first == again == {"scan_id": "7", "recommended": {"ED12", "EI10"},
                              "purple": {"ED12", "ES12"}, "purple_error": ""}
    assert reads == ["/scans/x.pdf"]


def test_a_failed_read_says_why_and_is_tried_again(cx, tmp_path, monkeypatch):
    def boom(path):
        raise FileNotFoundError("no such file")
    monkeypatch.setattr(marks_mod, "bar_colours", boom)
    e4l = _e4l(tmp_path, "/scans/x.pdf")
    got = marks_mod.infoceutical_marks(cx, e4l, 7)
    assert got["purple"] is None and "no such file" in got["purple_error"]
    assert got["recommended"] == {"ED12", "EI10"}
    monkeypatch.setattr(marks_mod, "bar_colours", lambda p: {"ED12": {"purple"}})
    assert marks_mod.infoceutical_marks(cx, e4l, 7)["purple"] == {"ED12"}


def test_no_scan_id_gives_no_marks(cx, tmp_path):
    assert marks_mod.infoceutical_marks(cx, _e4l(tmp_path, ""), None) is None


JOSIE = os.path.expanduser("~/e4l-scans/client_16136_scan_1066168_2026-10-11.pdf")


@pytest.mark.skipif(not os.path.exists(JOSIE), reason="Josie's scan PDF is only on Glen's Mac")
def test_real_scan_purple_matches_what_glen_sees():
    # Checked by eye on pages 1, 7, 10, 13, 19 and 26 of her 11 October scan.
    purple = {c for c, cols in marks_mod.bar_colours(JOSIE).items() if "purple" in cols}
    assert purple == {"ED12", "ED14", "EI10", "ES12", "ET7", "ET11", "ET16", "MB8",
                      "MR3", "MR10"}


# --- found in review --------------------------------------------------------

def test_all_route_saves_infoceutical_ticks_as_row_remedies(tmp_path, monkeypatch):
    monkeypatch.delenv("CONSOLE_SECRET", raising=False)
    import dashboard as _d
    monkeypatch.setattr(_d, "CONSOLE_SECRET", "", raising=False)
    from biofield_local_app import create_app
    from dashboard.biofield_clinical_proposals import selections
    db = str(tmp_path / "chat_log.db")
    client = create_app(db).test_client()
    ok = client.post("/author/a49/clinical-sections/all", json={
        "label": "Blood", "section": cs.INFOCEUTICALS, "on": True,
        "names": [ED5, EI10], "remedies": ["Fibrosolve"]})
    assert ok.status_code == 200
    with sqlite3.connect(db) as con:
        assert selections(con, "a49")["blood"] == ["Fibrosolve", ED5, EI10]


def test_an_old_note_survives_a_new_infoceutical_pick_row(cx):
    cs.upsert_item(cx, "Blood", cs.FORMULATIONS, _item(ED5, 7, 1, 6))
    cs.save_pick(cx, "a49", "Blood", cs.FORMULATIONS, ED5, note="josie says", rating="X74")
    cs.select_all(cx, "a49", "Blood", cs.INFOCEUTICALS, True, [ED5], [])
    entry = _by_key(cs.sections_for(cx, "a49", "Blood", [], [ED5]))["infoceuticals"]["items"][0]
    assert (entry["note"], entry["rating"]) == ("josie says", "X74")
    cs.save_pick(cx, "a49", "Blood", cs.INFOCEUTICALS, ED5, rating="555")
    entry = _by_key(cs.sections_for(cx, "a49", "Blood", [], [ED5]))["infoceuticals"]["items"][0]
    assert (entry["note"], entry["rating"]) == ("josie says", "555")


def test_approve_reaches_an_infoceutical_loaded_under_formulations(cx):
    cs.upsert_item(cx, "Blood", cs.FORMULATIONS, _item(ED5), origin_test="a49")
    assert cs.approve_item(cx, "Blood", cs.INFOCEUTICALS, ED5)
    info = _by_key(cs.sections_for(cx, "a50", "Blood"))["infoceuticals"]["items"]
    assert [i["name"] for i in info] == [ED5]


def test_a_read_that_finds_no_bars_is_an_error_not_nothing_purple(cx, tmp_path, monkeypatch):
    monkeypatch.setattr(marks_mod, "bar_colours", lambda p: {})
    got = marks_mod.infoceutical_marks(cx, _e4l(tmp_path, "/scans/x.pdf"), 7)
    assert got["purple"] is None and "no coloured bars" in got["purple_error"]


def test_a_newer_reader_re_reads_a_scan(cx, tmp_path, monkeypatch):
    e4l = _e4l(tmp_path, "/scans/x.pdf")
    monkeypatch.setattr(marks_mod, "bar_colours", lambda p: {"ED12": {"green"}})
    assert marks_mod.infoceutical_marks(cx, e4l, 7)["purple"] == set()
    monkeypatch.setattr(marks_mod, "READER_VERSION", marks_mod.READER_VERSION + 1)
    monkeypatch.setattr(marks_mod, "bar_colours", lambda p: {"ED12": {"purple"}})
    assert marks_mod.infoceutical_marks(cx, e4l, 7)["purple"] == {"ED12"}


def test_an_unreadable_scan_database_says_so(cx, tmp_path):
    got = marks_mod.infoceutical_marks(cx, str(tmp_path / "missing.db"), 7)
    assert got["recommended"] is None and got["purple"] is None
    html = render_clinical_checklist([{
        "label": "Blood", "common_remedies": [ED5], "selection_saved": True,
        "selected_remedies": [],
        "sections": cs.sections_for(cx, "a49", "Blood", [ED5], [], marks=got)}])
    assert "Recommended and purple could not be read from the scan" in html
    assert ">recommended</span>" not in html


def test_bfa_and_the_plural_are_infoceuticals():
    assert cs.is_infoceutical("BFA Big Field Activator")
    assert cs.is_infoceutical("Five Infoceuticals kit")


def test_a_scan_database_without_its_tables_says_so(cx, tmp_path):
    broken = str(tmp_path / "broken.db")
    sqlite3.connect(broken).close()
    got = marks_mod.infoceutical_marks(cx, broken, 7)
    assert got["recommended"] is None and "could not be read" in got["purple_error"]
