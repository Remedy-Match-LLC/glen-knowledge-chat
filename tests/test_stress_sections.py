"""The sectioned stress drop-down's data (Glen, 15 November 2026).

"divide the active list into 3 sections ... SPIRIT ... stresses added in the live
session ... MIND ... the Clinical stress patterns ... BODY ... the purple findings from
the E4L scan ... before the full list." Then: "each sub-list is alphabetical, but numbers
are in numerical sequence", and "For infoceuticals, use the full name starting with the
code, e.g. ED1: Source".
"""
import re
import sqlite3

import pytest

from biofield_local_app import create_app
from dashboard import e4l_scan_marks as marks_mod
from dashboard.biofield_authoring import create_test, init_auth_tables, resolve_stress_name
from dashboard.biofield_clinical_checklist import remember_stress_pattern
from dashboard.biofield_layer_match import (natural_key, natural_sorted, sectioned_vocab,
                                            spirit_stress_labels, stress_display)
from dashboard.biofield_stress import _norm, add_custom_vocab, add_stress, seed_from_scan
from dashboard.biofield_clinical_sections import infoceutical_code, is_infoceutical
from tests.test_biofield_local_app import _species_known, _no_console_gate  # noqa: F401

NAMES = {"ED1": "Source", "ED4": "Nerve", "ED11": "Liver"}


# ---- natural order ---------------------------------------------------------------------

def test_ed4_comes_before_ed11():
    assert natural_sorted(["ED11", "ED4"]) == ["ED4", "ED11"]
    assert natural_sorted(["ED11: Liver", "ED4: Nerve"]) == ["ED4: Nerve", "ED11: Liver"]


def test_numbers_compare_by_value_not_digit_by_digit():
    assert natural_sorted(["ED100", "ED10", "ED2"]) == ["ED2", "ED10", "ED100"]
    assert natural_sorted(["Layer 10", "Layer 2"]) == ["Layer 2", "Layer 10"]
    assert natural_sorted(["A10b", "A2b"]) == ["A2b", "A10b"]


def test_case_is_ignored_and_words_without_digits_are_alphabetical():
    assert natural_sorted(["liver", "Adrenal", "kidney", "Bladder"]) == [
        "Adrenal", "Bladder", "kidney", "liver"]
    assert natural_sorted(["ed11", "ED4", "Ed2"]) == ["Ed2", "ED4", "ed11"]


def test_ties_keep_their_order():
    assert natural_sorted(["Liver", "liver", "LIVER"]) == ["Liver", "liver", "LIVER"]
    assert natural_key("Liver") == natural_key("liver")


# ---- the infoceutical display form -----------------------------------------------------

def test_an_infoceutical_shows_as_code_colon_name():
    assert stress_display("ED1", NAMES) == "ED1: Source"
    assert stress_display("ED1 Source", NAMES) == "ED1: Source"
    assert stress_display("ED1: Source", NAMES) == "ED1: Source"
    assert stress_display("ed1 source", NAMES) == "ED1: source"
    # The words after the code are the item's own, so a variant stays itself.
    assert stress_display("ED10 Skin Epigenetics", NAMES) == "ED10: Skin Epigenetics"
    # No name known: the bare code, never a made-up name.
    assert stress_display("ES9", NAMES) == "ES9"
    # Not infoceuticals (Glen, 2026-10-17): MR, ER, and plain words.
    assert stress_display("MR10 CNS-Heart-Matrix", NAMES) == "MR10 CNS-Heart-Matrix"
    assert stress_display("Liver", NAMES) == "Liver"


def _seeded(tmp_path):
    cx = sqlite3.connect(str(tmp_path / "chat_log.db"))
    init_auth_tables(cx)
    tid = create_test(cx, "Pam", "pam@example.com", "2026-11-15")
    other = create_test(cx, "Ann", "ann@example.com", "2026-11-15")
    for term in ("Liver", "Kidney", "ED1 Source", "ED11 Liver", "ED4 Nerve", "Heart",
                 "Source"):
        add_custom_vocab(cx, term)
    add_stress(cx, tid, "Grief", source="voice")
    add_stress(cx, tid, "Pressure 10", source="manual")
    add_stress(cx, tid, "Pressure 2", source="voice")
    add_stress(cx, tid, "ED1", source="voice")
    add_stress(cx, tid, "Source", source="manual")
    add_stress(cx, tid, "Adrenal Fatigue", source="tag")
    add_stress(cx, tid, "Insomnia", source="clinical")
    add_stress(cx, other, "Not this test", source="voice")
    seed_from_scan(cx, tid, [{"code": "ET16", "name": "Glial Cell Terrain"}], {})
    cx.commit()
    return cx, tid


def test_spirit_is_this_tests_live_session_stresses_only(tmp_path):
    cx, tid = _seeded(tmp_path)
    assert spirit_stress_labels(cx, tid) == ["Grief", "Pressure 10", "Pressure 2", "ED1",
                                             "Source"]


def test_the_sections_in_display_form_deduped_and_in_natural_order(tmp_path):
    cx, tid = _seeded(tmp_path)
    got = sectioned_vocab(cx, tid, mind=["Stress 10", "Stress 2", "stress 2", "ED4"],
                          body=["ED11", "Lead", "ED4"], code_names=NAMES)
    # "ED1" and "Source" are one item in SPIRIT: "ED1: Source".
    assert got["spirit"] == ["ED1: Source", "Grief", "Pressure 2", "Pressure 10"]
    assert got["mind"] == ["ED4: Nerve", "Stress 2", "Stress 10"]
    assert got["body"] == ["ED4: Nerve", "ED11: Liver", "Lead"]
    assert got["unnamed"] == []


def test_the_full_list_is_complete_sorted_and_keeps_plain_names(tmp_path):
    cx, tid = _seeded(tmp_path)
    got = sectioned_vocab(cx, tid, code_names=NAMES)
    assert got["all"] == natural_sorted(got["all"])
    # Every client stress and every vocabulary term, once, in display form.
    for item in ("Grief", "Insomnia", "Adrenal Fatigue", "Glial Cell Terrain", "Kidney",
                 "ED1: Source", "ED4: Nerve", "ED11: Liver", "Liver", "Heart", "Source"):
        assert item in got["all"], item
    assert len({x.lower() for x in got["all"]}) == len(got["all"])
    assert got["all"].index("ED4: Nerve") < got["all"].index("ED11: Liver")
    assert "ED1 Source" not in got["all"] and "ED1" not in got["all"]


def test_body_lists_infoceuticals_first_then_other_purples(tmp_path):
    # Glen, 15 Nov 2026: "all purple, infoceutical list first, then other purples".
    cx, tid = _seeded(tmp_path)
    got = sectioned_vocab(cx, tid, body=["Lead", "ED11", "Aluminum", "MR10 CNS-Heart-Matrix",
                                         "ED4", "Zinc"], code_names=NAMES)
    assert got["body"] == ["ED4: Nerve", "ED11: Liver", "Aluminum", "Lead",
                           "MR10 CNS-Heart-Matrix", "Zinc"]


def test_an_empty_section_is_an_empty_list(tmp_path):
    cx = sqlite3.connect(str(tmp_path / "chat_log.db"))
    init_auth_tables(cx)
    tid = create_test(cx, "Pam", "pam@example.com", "2026-11-15")
    got = sectioned_vocab(cx, tid)
    assert (got["spirit"], got["mind"], got["body"]) == ([], [], [])


def test_a_code_with_no_name_is_shown_bare_and_listed(tmp_path):
    cx, tid = _seeded(tmp_path)
    got = sectioned_vocab(cx, tid, body=["ES9"], code_names=NAMES)
    assert "ES9" in got["body"] and got["unnamed"] == ["ES9"]


# ---- a picked "ED1: Source" is still that infoceutical ---------------------------------

def test_a_picked_display_form_matches_the_stored_stress(tmp_path):
    cx, tid = _seeded(tmp_path)
    # The dedupe and the Head-to-stress match both go through _norm.
    assert _norm("ED1: Source") == _norm("ED1 Source") == "ed1 source"
    assert _norm("Liver: right lobe") == "liver: right lobe"     # only an infoceutical code
    # Adding it again is the stress already there, not a second one.
    add_stress(cx, tid, "ED1 Source", source="voice")
    assert not add_stress(cx, tid, "ED1: Source", source="voice")
    # The stress-name resolver takes it to the stored spelling.
    cx.execute("CREATE TABLE IF NOT EXISTS fmp_snap_client_active_main_stress(main_stress TEXT)")
    cx.execute("INSERT INTO fmp_snap_client_active_main_stress VALUES ('ED1 Source')")
    assert resolve_stress_name(cx, "ED1: Source") == "ED1 Source"
    # And it reads as the infoceutical ED1 wherever a code is looked for.
    assert infoceutical_code("ED1: Source") == "ED1" and is_infoceutical("ED1: Source")


def test_a_picked_display_form_matches_by_voice_too(tmp_path):
    from dashboard.biofield_layer_match import match_term
    cx, tid = _seeded(tmp_path)
    got = match_term(cx, "head", "ED1: Source", test_id=tid)
    assert got["matched"] and _norm(got["value"]) == "ed1 source"


# ---- the BODY section: purple bars on the scan -----------------------------------------

def test_purple_finding_names():
    got = marks_mod.purple_finding_names(
        {"ED4", "MR9", "12. Choice Point", "8 - Humility", "Molybdenum", "ER99"},
        {"MR9": "Freedom"})
    assert sorted(got) == sorted(["ED4", "Freedom", "Choice Point", "Humility",
                                  "Molybdenum", "ER99"])


# ---- the route ---------------------------------------------------------------------------

def _e4l(tmp_path):
    # e4l.db is another application's database; these are the columns this code reads.
    path = str(tmp_path / "e4l.db")
    with sqlite3.connect(path) as e:
        e.execute("CREATE TABLE e4l_scans (scan_id, client_id, scan_date, pdf_filename, "
                  "raw_pdf_path, notes)")
        e.execute("CREATE TABLE e4l_scan_results (id, scan_id, item_code, priority_rank, "
                  "protocol_days, score, section_context, notes)")
        e.execute("CREATE TABLE e4l_items (code TEXT, name TEXT, full_name TEXT, category TEXT)")
        e.execute("INSERT INTO e4l_scans VALUES (7,1,'2026-11-14','x.pdf','/scans/x.pdf','')")
        e.execute("INSERT INTO e4l_scan_results VALUES (1,7,'ED4',1,15,'','Infoceuticals','')")
        e.executemany("INSERT INTO e4l_items VALUES (?,?,?,?)",
                      [("ED1", "Source", "Source Driver", "ED"),
                       ("ED4", "Nerve", "Nerve Driver", "ED"),
                       ("ED11", "Liver", "Liver Driver", "ED")])
    return path


@pytest.fixture
def app(tmp_path, monkeypatch, _species_known):
    monkeypatch.setattr(marks_mod, "bar_colours", lambda p: {
        "ED11": {"purple"}, "ED4": {"purple"}, "ET16": {"purple"}, "Lead": {"purple"},
        "ED1": {"green"}, "Silica": {"yellow"}})
    # The intake is read by its email here: this e4l.db has no clients to resolve.
    import dashboard.biofield_e4l as _be
    monkeypatch.setattr(_be, "resolve_intake_subject", lambda *a, **k: {
        "by": "none", "client": None, "scan_ids": None, "species": None, "ambiguous": False})
    db = str(tmp_path / "chat_log.db")
    cx, tid = _seeded(tmp_path)
    remember_stress_pattern(cx, "Fatigue", "Adrenal exhaustion")
    remember_stress_pattern(cx, "Sleep 10", "Pineal 10")
    remember_stress_pattern(cx, "Sleep 2", "Pineal 2")
    cx.commit()
    cx.close()
    client = create_app(
        db, e4l_db=_e4l(tmp_path),
        fetch_profile=lambda email: {"conditions": ["Fatigue", "Sleep 10", "Sleep 2"]},
        scan_lookup=lambda email, client_id=None: {"found": True, "scan_id": 7,
                                                   "scan_date": "2026-11-14"},
    ).test_client()
    return client, tid


def test_the_route_answers_each_section(app):
    client, tid = app
    j = client.get(f"/api/vocab?test_id={tid}&sections=1&limit=5000").get_json()
    assert j["spirit"] == ["ED1: Source", "Grief", "Pressure 2", "Pressure 10"]
    assert j["mind"] == ["Adrenal exhaustion", "Pineal 2", "Pineal 10"]
    # Every purple bar: infoceuticals as "CODE: name", ET16 by this test's scan stress
    # label when e4l_items has no row for it, and printed names as they are.
    assert j["body"] == ["ED4: Nerve", "ED11: Liver", "ET16: Glial Cell Terrain", "Lead"]
    assert j["all"] == sorted(j["all"], key=natural_key) and "Kidney" in j["all"]
    assert j["errors"] == {} and j["unnamed"] == []


def test_an_unreadable_scan_says_so_instead_of_none(app, monkeypatch):
    client, tid = app

    def boom(path):
        raise FileNotFoundError("no such file")
    monkeypatch.setattr(marks_mod, "bar_colours", boom)
    j = client.get(f"/api/vocab?test_id={tid}&sections=1").get_json()
    assert j["body"] == [] and "no such file" in j["errors"]["body"]


def test_without_sections_the_answer_is_exactly_as_before(app):
    client, tid = app
    j = client.get(f"/api/vocab?test_id={tid}&limit=5000").get_json()
    assert set(j) == {"vocab", "client"}
    assert j["client"][:2] == ["Grief", "Pressure 10"]     # client first, unsorted
    assert "ED1 Source" in j["vocab"] and "ED1: Source" not in j["vocab"]
    plain = client.get("/api/vocab?limit=5000").get_json()
    assert set(plain) == {"vocab"}


def test_add_spirit_reuses_the_stress_add_route_as_a_spirit_source(app):
    client, tid = app
    j = client.post(f"/author/{tid}/stress/add", json={"label": "Fear"}).get_json()
    assert j["ok"]
    got = client.get(f"/api/vocab?test_id={tid}&sections=1").get_json()
    assert "Fear" in got["spirit"]
