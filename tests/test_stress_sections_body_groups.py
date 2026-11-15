"""BODY's subheads, the causal chain's move to BODY, layer heads in SPIRIT, and greyed
stresses already on a layer (Glen, 15 November 2026).

"Move to body." Body's groups in order: the causal chain, "Infoceuticals", "Body
stresses", "Psyche". Body stresses and Psyche split by the scan section a bar sits on.
A layer's head ("Liver Detox") is a stress in SPIRIT. "Grayed out means they're already
balanced on a layer."
"""
import json
import sqlite3

import pytest

from biofield_local_app import create_app
from dashboard import e4l_scan_marks as marks_mod
from dashboard.biofield_authoring import add_chain_row, create_test, init_auth_tables
from dashboard.biofield_layer_match import (BODY_GROUPS, chain_stress_labels, layer_heads,
                                            on_layer_labels, sectioned_vocab,
                                            natural_sorted, spirit_stress_labels)
from dashboard.biofield_stress import add_stress, init_stress_tables
from tests.test_biofield_local_app import _species_known, _no_console_gate  # noqa: F401

NAMES = {"ED4": "Nerve", "ED11": "Liver"}


def _sid(cx, tid, label):
    return cx.execute("SELECT id FROM biofield_auth_stress WHERE test_id=? AND label=?",
                      (int(str(tid).lstrip("a")), label)).fetchone()[0]


def _seeded(tmp_path):
    cx = sqlite3.connect(str(tmp_path / "chat_log.db"))
    init_auth_tables(cx)
    init_stress_tables(cx)
    tid = create_test(cx, "Pam", "pam@example.com", "2026-11-15")
    add_stress(cx, tid, "Grief", source="voice")
    add_stress(cx, tid, "Pressure 2", source="manual")
    for label in ("Skin & Gut Integrity", "Shoulder, Arm & Emotional Integration",
                  "Rectum & Self-Worth"):
        add_stress(cx, tid, label, source="chain")
    add_stress(cx, tid, "Adrenal Fatigue", source="tag")
    rid = add_chain_row(cx, tid, 1, "Liver Detox",
                        "Skin & Gut Integrity, Shoulder, Arm & Emotional Integration", "")
    # A second layer whose head is already a chain stress, and one with no head.
    add_chain_row(cx, tid, 2, "Rectum & Self-Worth", "", "")
    add_chain_row(cx, tid, 3, "", "", "")
    for label in ("Skin & Gut Integrity", "Shoulder, Arm & Emotional Integration",
                  "Adrenal Fatigue"):
        cx.execute("INSERT INTO biofield_auth_layer_stress(test_id,stress_id,chain_rid) "
                   "VALUES(?,?,?)", (int(str(tid).lstrip("a")), _sid(cx, tid, label), rid))
    # A link to a layer row that no longer exists places nothing.
    cx.execute("INSERT INTO biofield_auth_layer_stress(test_id,stress_id,chain_rid) "
               "VALUES(?,?,?)", (int(str(tid).lstrip("a")), _sid(cx, tid, "Grief"), 99999))
    cx.commit()
    return cx, tid


# ---- the data ----------------------------------------------------------------------

def test_chain_rows_leave_spirit(tmp_path):
    cx, tid = _seeded(tmp_path)
    assert spirit_stress_labels(cx, tid) == ["Grief", "Pressure 2"]
    assert chain_stress_labels(cx, tid) == [
        "Skin & Gut Integrity", "Shoulder, Arm & Emotional Integration", "Rectum & Self-Worth"]


def test_chain_rows_are_bodys_first_group_then_the_subheads_in_order(tmp_path):
    cx, tid = _seeded(tmp_path)
    got = sectioned_vocab(cx, tid, body=["Lead", "ED11", "Humility", "ED4", "Zinc",
                                         "Warrior"],
                          psyche=["Humility", "Warrior"], code_names=NAMES)
    assert got["body_groups"] == [
        ["Causal chain", ["Rectum & Self-Worth", "Shoulder, Arm & Emotional Integration",
                          "Skin & Gut Integrity"]],
        ["Infoceuticals", ["ED4: Nerve", "ED11: Liver"]],
        ["Body stresses", ["Lead", "Zinc"]],
        ["Psyche", ["Humility", "Warrior"]]]
    assert [g for g, _ in got["body_groups"]] == list(BODY_GROUPS)
    assert got["body"] == [d for _g, items in got["body_groups"] for d in items]
    for chain in ("Skin & Gut Integrity", "Rectum & Self-Worth"):
        assert chain not in got["spirit"]


def test_an_empty_group_has_no_subhead(tmp_path):
    cx, tid = _seeded(tmp_path)
    got = sectioned_vocab(cx, tid, body=["Lead"], code_names=NAMES)
    assert [g for g, _ in got["body_groups"]] == ["Causal chain", "Body stresses"]


def test_unknown_sections_fall_back_to_body_stresses(tmp_path):
    # No psyche list (an older read, or a heading that could not be read).
    cx, tid = _seeded(tmp_path)
    got = sectioned_vocab(cx, tid, body=["Humility", "Lead"], code_names=NAMES)
    assert dict(got["body_groups"])["Body stresses"] == ["Humility", "Lead"]
    assert "Psyche" not in dict(got["body_groups"])


def test_an_infoceutical_on_a_psyche_page_stays_an_infoceutical(tmp_path):
    cx, tid = _seeded(tmp_path)
    got = sectioned_vocab(cx, tid, body=["ED4"], psyche=["ED4"], code_names=NAMES)
    assert dict(got["body_groups"])["Infoceuticals"] == ["ED4: Nerve"]


def test_a_chain_stress_that_is_also_purple_shows_once_in_the_chain_group(tmp_path):
    cx, tid = _seeded(tmp_path)
    got = sectioned_vocab(cx, tid, body=["Skin & Gut Integrity", "Lead"], code_names=NAMES)
    assert got["body"].count("Skin & Gut Integrity") == 1
    assert "Skin & Gut Integrity" in dict(got["body_groups"])["Causal chain"]


def test_layer_heads_join_spirit_unless_already_listed(tmp_path):
    cx, tid = _seeded(tmp_path)
    assert layer_heads(cx, tid) == ["Liver Detox", "Rectum & Self-Worth"]
    got = sectioned_vocab(cx, tid, mind=["Pineal 2"], body=["Lead"], code_names=NAMES)
    # "Rectum & Self-Worth" is a head, but it is already in the Causal chain.
    assert got["spirit"] == ["Grief", "Pressure 2", "Liver Detox"]
    # A head that is a Mind or Body item stays there and out of SPIRIT.
    cx.execute("UPDATE biofield_auth_chain SET head='lead' WHERE head='Liver Detox'")
    got = sectioned_vocab(cx, tid, mind=["Pineal 2"], body=["Lead"], code_names=NAMES)
    assert got["spirit"] == ["Grief", "Pressure 2"]
    cx.execute("UPDATE biofield_auth_chain SET head='Pineal 2' WHERE head='lead'")
    got = sectioned_vocab(cx, tid, mind=["Pineal 2"], body=["Lead"], code_names=NAMES)
    assert got["spirit"] == ["Grief", "Pressure 2"]


def test_a_head_that_repeats_a_spirit_stress_shows_once(tmp_path):
    cx, tid = _seeded(tmp_path)
    cx.execute("UPDATE biofield_auth_chain SET head='grief' WHERE head='Liver Detox'")
    got = sectioned_vocab(cx, tid, code_names=NAMES)
    assert [s.lower() for s in got["spirit"]].count("grief") == 1


def test_on_layer_is_the_linked_stresses_and_the_heads(tmp_path):
    cx, tid = _seeded(tmp_path)
    assert on_layer_labels(cx, tid) == [
        "Skin & Gut Integrity", "Shoulder, Arm & Emotional Integration", "Adrenal Fatigue",
        "Liver Detox", "Rectum & Self-Worth"]
    got = sectioned_vocab(cx, tid, code_names=NAMES)
    assert set(got["on_layer"]) == {
        "Adrenal Fatigue", "Liver Detox", "Rectum & Self-Worth", "Skin & Gut Integrity",
        "Shoulder, Arm & Emotional Integration"}
    # The orphan link to a deleted layer row does not grey Grief.
    assert "Grief" not in got["on_layer"]


def test_no_layers_means_nothing_on_a_layer(tmp_path):
    cx = sqlite3.connect(str(tmp_path / "chat_log.db"))
    init_auth_tables(cx)
    tid = create_test(cx, "Pam", "pam@example.com", "2026-11-15")
    got = sectioned_vocab(cx, tid)
    assert got["on_layer"] == [] and got["body_groups"] == [] and got["spirit"] == []


# ---- the scan's page sections ----------------------------------------------------

HDR = ["Practitioner: X", "Client: Y   Scan date: 2026-11-13"]


@pytest.mark.parametrize("line,want", [
    ("NUTRITION FIELDS - PROTEIN FIELDS", "NUTRITION FIELDS - PROTEIN FIELDS"),
    ("HERO’S JOURNEY", "HERO’S JOURNEY"),
    ("DIGESTION / METABOLISM - METABOLISM", "DIGESTION / METABOLISM - METABOLISM"),
    ("HOME, WORK &amp; PERSONAL CARE", "HOME, WORK & PERSONAL CARE"),
    ("easy. We mustn’t delude ourselves", None),     # a continuation page
    ("ED4: NERVE DRIVER Energetic Drivers support", None),
    ("ET0 - CNS", None),                                  # a bar label in capitals
    ("1", None),
])
def test_the_page_heading_is_the_capitals_line_after_the_header(line, want):
    assert marks_mod.page_heading(HDR + [line, "more"]) == want


def test_a_page_with_only_its_header_has_no_heading():
    assert marks_mod.page_heading(HDR) is None


def test_a_continuation_page_inherits_the_section_before_it():
    assert marks_mod.page_sections([None, "LIFE JOURNEY", None, "ARCHETYPES", None, None]) \
        == [None, "LIFE JOURNEY", "LIFE JOURNEY", "ARCHETYPES", "ARCHETYPES", "ARCHETYPES"]


@pytest.mark.parametrize("heading", [
    "MIND BODY - CORE EMOTIONS", "LIFE JOURNEY", "LIFE PURPOSE - UNDERSTAND YOUR WORLD",
    "HERO’S JOURNEY", "HERO'S JOURNEY", "ARCHETYPES", "BELIEFS",
    "EMOTIONAL ATTRACTION - POSITIVE", "TRANSFORMATIONS", "UNCONSCIOUS PATTERNS"])
def test_psyche_headings(heading):
    assert marks_mod.is_psyche_section(heading)


@pytest.mark.parametrize("heading", [
    "ENVIRONMENTAL", "AGRICULTURE - FUNGICIDES", "NUTRITION", "NUTRITION FIELDS - VITAMIN FIELDS",
    "DIGESTION / METABOLISM - METABOLISM", "SENSITIVITIES - OTHER", "ENERGY REJUVENATION - MRs",
    "BELIEFSX", "", None])
def test_body_headings(heading):
    assert not marks_mod.is_psyche_section(heading)


def test_any_psyche_section_makes_a_bar_psyche():
    assert marks_mod.is_psyche(["NUTRITION", "ARCHETYPES"])
    assert not marks_mod.is_psyche(["NUTRITION"])
    assert not marks_mod.is_psyche(None) and not marks_mod.is_psyche([])


# ---- the stored read ---------------------------------------------------------------

def _e4l(tmp_path, pdf="/scans/x.pdf"):
    path = str(tmp_path / "e4l.db")
    with sqlite3.connect(path) as e:
        e.execute("CREATE TABLE e4l_scans (scan_id, client_id, scan_date, pdf_filename, "
                  "raw_pdf_path, notes)")
        e.execute("CREATE TABLE e4l_scan_results (id, scan_id, item_code, priority_rank, "
                  "protocol_days, score, section_context, notes)")
        e.execute("CREATE TABLE e4l_items (code TEXT, name TEXT, full_name TEXT, category TEXT)")
        e.execute("INSERT INTO e4l_scans VALUES (7,1,'2026-11-14','x.pdf',?,'')", (pdf,))
        e.execute("INSERT INTO e4l_scan_results VALUES (1,7,'ED4',1,15,'','Infoceuticals','')")
        e.executemany("INSERT INTO e4l_items VALUES (?,?,?,?)",
                      [("ED4", "Nerve", "Nerve Driver", "ED"),
                       ("ED11", "Liver", "Liver Driver", "ED")])
    return path


def _reading(colours, sections):
    found = marks_mod.BarColours(colours)
    found.sections = sections
    return found


def test_sections_are_stored_with_the_colours(tmp_path, monkeypatch):
    cx = sqlite3.connect(str(tmp_path / "chat_log.db"))
    monkeypatch.setattr(marks_mod, "bar_colours", lambda p: _reading(
        {"Lead": {"purple"}, "8 - Humility": {"purple"}, "ED4": {"purple"}},
        {"Lead": {"ENVIRONMENTAL"}, "8 - Humility": {"LIFE JOURNEY"}}))
    marks = marks_mod.infoceutical_marks(cx, _e4l(tmp_path), 7)
    assert marks["purple"] == {"Lead", "8 - Humility", "ED4"}
    assert "sections" not in marks          # infoceutical_marks' answer is unchanged
    assert marks_mod.bar_sections(cx, 7) == {"Lead": ["ENVIRONMENTAL"],
                                             "8 - Humility": ["LIFE JOURNEY"]}


def test_an_old_cache_table_gains_the_column_and_reads_as_unknown(tmp_path):
    cx = sqlite3.connect(str(tmp_path / "chat_log.db"))
    cx.execute("CREATE TABLE biofield_scan_bar_colour (scan_id TEXT NOT NULL, code TEXT "
               "NOT NULL, colours TEXT NOT NULL, PRIMARY KEY (scan_id, code))")
    cx.execute("INSERT INTO biofield_scan_bar_colour VALUES ('7','Lead','purple')")
    marks_mod.ensure_schema(cx)
    marks_mod.ensure_schema(cx)             # idempotent
    assert marks_mod.bar_sections(cx, 7) == {}
    cols = [r[1] for r in cx.execute("PRAGMA table_info(biofield_scan_bar_colour)")]
    assert cols.count("sections") == 1


def test_an_older_good_read_stands_when_the_re_read_fails(tmp_path, monkeypatch):
    monkeypatch.setattr(marks_mod, "_REREAD_FAILED", {})
    cx = sqlite3.connect(str(tmp_path / "chat_log.db"))
    e4l = _e4l(tmp_path)
    monkeypatch.setattr(marks_mod, "bar_colours", lambda p: {"Lead": {"purple"}})
    monkeypatch.setattr(marks_mod, "READER_VERSION", 2)
    assert marks_mod.infoceutical_marks(cx, e4l, 7)["purple"] == {"Lead"}
    monkeypatch.setattr(marks_mod, "READER_VERSION", 3)

    def boom(path):
        raise FileNotFoundError("the PDF moved")
    monkeypatch.setattr(marks_mod, "bar_colours", boom)
    got = marks_mod.infoceutical_marks(cx, e4l, 7)
    assert got["purple"] == {"Lead"} and got["purple_error"] == ""
    assert marks_mod.bar_sections(cx, 7) == {}


def test_a_plain_reading_without_sections_still_stores(tmp_path, monkeypatch):
    cx = sqlite3.connect(str(tmp_path / "chat_log.db"))
    monkeypatch.setattr(marks_mod, "bar_colours", lambda p: {"Lead": {"purple"}})
    assert marks_mod.infoceutical_marks(cx, _e4l(tmp_path), 7)["purple"] == {"Lead"}
    assert marks_mod.bar_sections(cx, 7) == {}


# ---- the route -----------------------------------------------------------------------

@pytest.fixture
def app(tmp_path, monkeypatch, _species_known):
    monkeypatch.setattr(marks_mod, "bar_colours", lambda p: _reading(
        {"ED4": {"purple"}, "Lead": {"purple"}, "8 - Humility": {"purple"},
         "12. Choice Point": {"purple"}, "Silica": {"purple"}, "Zinc": {"green"}},
        {"ED4": {"ENERGY STRENGTH"}, "Lead": {"ENVIRONMENTAL"},
         "8 - Humility": {"LIFE JOURNEY"}, "12. Choice Point": {"HERO’S JOURNEY"}}))
    import dashboard.biofield_e4l as _be
    monkeypatch.setattr(_be, "resolve_intake_subject", lambda *a, **k: {
        "by": "none", "client": None, "scan_ids": None, "species": None, "ambiguous": False})
    db = str(tmp_path / "chat_log.db")
    cx, tid = _seeded(tmp_path)
    cx.close()
    client = create_app(
        db, e4l_db=_e4l(tmp_path),
        fetch_profile=lambda email: {"conditions": []},
        scan_lookup=lambda email, client_id=None: {"found": True, "scan_id": 7,
                                                   "scan_date": "2026-11-14"},
    ).test_client()
    return client, tid


def test_the_route_answers_body_groups_spirit_heads_and_on_layer(app):
    client, tid = app
    j = client.get(f"/api/vocab?test_id={tid}&sections=1&limit=5000").get_json()
    assert j["errors"] == {}
    assert j["body_groups"] == [
        ["Causal chain", ["Rectum & Self-Worth", "Shoulder, Arm & Emotional Integration",
                          "Skin & Gut Integrity"]],
        ["Infoceuticals", ["ED4: Nerve"]],
        # Silica has no section known: Body stresses.
        ["Body stresses", ["Lead", "Silica"]],
        ["Psyche", ["Choice Point", "Humility"]]]
    assert j["spirit"] == ["Grief", "Pressure 2", "Liver Detox"]
    assert "Liver Detox" in j["on_layer"] and "Skin & Gut Integrity" in j["on_layer"]
    assert "Grief" not in j["on_layer"]
    assert j["spirit_heads"] == ["Liver Detox"]
    json.dumps(j)


def test_a_scan_infoceutical_on_a_layer_greys_its_code_form(tmp_path):
    from dashboard.biofield_stress import seed_from_scan
    cx, tid = _seeded(tmp_path)
    seed_from_scan(cx, tid, [{"code": "ED4", "name": "Nerve Driver"},
                             {"code": "MR10", "name": "CNS-Heart-Matrix"}], {})
    rid = cx.execute("SELECT id FROM biofield_auth_chain WHERE head='Liver Detox'").fetchone()[0]
    t = int(str(tid).lstrip("a"))
    for (sid,) in cx.execute("SELECT id FROM biofield_auth_stress WHERE test_id=? AND "
                             "source='scan'", (t,)).fetchall():
        cx.execute("INSERT INTO biofield_auth_layer_stress VALUES(?,?,?)", (t, sid, rid))
    got = sectioned_vocab(cx, tid, body=["ED4", "ED11"], code_names=NAMES)
    assert "ED4: Nerve" in got["on_layer"] and "Nerve Driver" in got["on_layer"]
    assert "ED11: Liver" not in got["on_layer"]
    # MR is not an infoceutical: only its stored name is on the layer.
    assert "CNS-Heart-Matrix" in got["on_layer"] and "MR10" not in got["on_layer"]


# ---- found in review of #2292 ----------------------------------------------------------

def test_a_chain_name_folded_into_its_infoceutical_stays_in_the_causal_chain(tmp_path):
    cx, tid = _seeded(tmp_path)
    add_stress(cx, tid, "Source", source="chain")
    got = sectioned_vocab(cx, tid, body=["ED1", "ED4"],
                          code_names={"ED1": "Source", **NAMES})
    groups = dict(got["body_groups"])
    assert "ED1: Source" in groups["Causal chain"]
    assert groups["Infoceuticals"] == ["ED4: Nerve"]


def test_spirit_heads_are_the_heads_that_are_not_stored_stresses(tmp_path):
    cx, tid = _seeded(tmp_path)
    got = sectioned_vocab(cx, tid, code_names=NAMES)
    assert got["spirit_heads"] == ["Liver Detox"]
    add_stress(cx, tid, "Liver Detox", source="manual")
    got = sectioned_vocab(cx, tid, code_names=NAMES)
    assert got["spirit_heads"] == [] and "Liver Detox" in got["spirit"]


def test_a_failed_re_read_is_not_repeated_on_every_call(tmp_path, monkeypatch):
    monkeypatch.setattr(marks_mod, "_REREAD_FAILED", {})
    cx = sqlite3.connect(str(tmp_path / "chat_log.db"))
    e4l = _e4l(tmp_path)
    monkeypatch.setattr(marks_mod, "bar_colours", lambda p: {"Lead": {"purple"}})
    monkeypatch.setattr(marks_mod, "READER_VERSION", 2)
    marks_mod.infoceutical_marks(cx, e4l, 7)
    monkeypatch.setattr(marks_mod, "READER_VERSION", 3)
    tries = []

    def boom(path):
        tries.append(path)
        raise FileNotFoundError("the PDF moved")
    monkeypatch.setattr(marks_mod, "bar_colours", boom)
    for _ in range(3):
        assert marks_mod.infoceutical_marks(cx, e4l, 7)["purple"] == {"Lead"}
    assert len(tries) == 1
    # After the interval it is tried again, and a good read clears the mark.
    monkeypatch.setattr(marks_mod, "REREAD_RETRY_SECONDS", 0)
    monkeypatch.setattr(marks_mod, "bar_colours", lambda p: _reading(
        {"Lead": {"purple"}}, {"Lead": {"ENVIRONMENTAL"}}))
    marks_mod.infoceutical_marks(cx, e4l, 7)
    assert marks_mod.bar_sections(cx, 7) == {"Lead": ["ENVIRONMENTAL"]}
    assert marks_mod._REREAD_FAILED == {}


def test_greyed_items_go_last_in_every_list_and_body_group(tmp_path):
    # Glen, 15 Nov 2026: "The grayed items can also move to the bottom of their section
    # list in alphabetical order."
    cx, tid = _seeded(tmp_path)
    t = int(str(tid).lstrip("a"))
    rid = cx.execute("SELECT id FROM biofield_auth_chain WHERE head='Liver Detox'").fetchone()[0]
    add_stress(cx, tid, "Apathy", source="voice")
    add_stress(cx, tid, "Anger", source="manual")
    from dashboard.biofield_stress import seed_from_scan
    seed_from_scan(cx, tid, [{"code": "ED11", "name": "Liver Driver"}], {})
    for label in ("Apathy", "Liver Driver"):
        cx.execute("INSERT INTO biofield_auth_layer_stress VALUES(?,?,?)",
                   (t, _sid(cx, tid, label), rid))
    cx.execute("UPDATE biofield_auth_chain SET head='Rectum & Self-Worth' WHERE id=?", (rid,))
    got = sectioned_vocab(cx, tid, mind=["Pineal 2", "Adrenal Fatigue"],
                          body=["ED11", "ED4", "ED20", "Lead"],
                          code_names={"ED20": "X", **NAMES})
    # SPIRIT: Apathy is on the layer, so it follows Anger, Grief and Pressure 2.
    assert got["spirit"] == ["Anger", "Grief", "Pressure 2", "Apathy"]
    assert got["mind"] == ["Pineal 2", "Adrenal Fatigue"]
    groups = dict(got["body_groups"])
    # Linked chain stresses last in the chain group; ED11 last in Infoceuticals, and the
    # rest keep the natural order (ED4 before ED20).
    assert groups["Causal chain"] == ["Rectum & Self-Worth", "Shoulder, Arm & Emotional "
                                      "Integration", "Skin & Gut Integrity"]
    assert groups["Infoceuticals"] == ["ED4: Nerve", "ED20: X", "ED11: Liver"]
    assert [g for g, _ in got["body_groups"]] == ["Causal chain", "Infoceuticals",
                                                  "Body stresses"]
    on = {d.lower() for d in got["on_layer"]}
    first_on = next(i for i, d in enumerate(got["all"]) if d.lower() in on)
    assert all(d.lower() in on for d in got["all"][first_on:])
    assert all(d.lower() not in on for d in got["all"][:first_on])
    assert got["all"][first_on:] == natural_sorted(got["all"][first_on:])
