"""A layer made from a Clinical Summary condition is named by its stored pattern.

Glen, 2026-10-20: "When it adds the layers from the Clinical, it is failing to make the
Head & Tail show the term like Ovaries or Adrenal, and instead puts the full description
which should go into the Balances list." And: "all the full wording data should go into
the list of what balances on that layer."

Sharon Connour (a50) is the case: the Minimum program wrote "Adrenal Fatigue, Current,
2023" as Head and Tail, where her stored pattern is "Adrenal". Her card 6 Head, "Green
Jasper Gem Elixir", is Glen's own and must survive any correction. Stress 1914 linked
only to row 449 after he deleted it, so no layer balanced it.
"""
import json
import re
import sqlite3

import pytest

from biofield_local_app import create_app
from dashboard import biofield_clinical_head_term as ht
from dashboard.biofield_authoring import (add_chain_row, create_test, init_auth_tables,
                                          remove_remedy_preserving_layer)
from dashboard.biofield_clinical_checklist import balance_item, remember_stress_pattern
from dashboard.biofield_clinical_proposals import save_selection
from dashboard.biofield_stress import add_stress, init_stress_tables, list_stresses

ADRENAL = "Adrenal Fatigue, Current, 2023"
MIGRAINE = ("Migranes, Current, Past, 2001 once I started taking natural calm they "
            "slowed down/they come rarely now")
GLAUCOMA = "Suspect of Glaucoma/high pressures and thinning cornea, Current, 2020"


@pytest.fixture(autouse=True)
def _no_gate(monkeypatch):
    monkeypatch.delenv("CONSOLE_SECRET", raising=False)
    import dashboard as _d
    monkeypatch.setattr(_d, "CONSOLE_SECRET", "", raising=False)


_NO_SCAN = {"status": "none", "found": False, "findings": [], "days_ago": None,
            "fresh": False}


def _setup(tmp_path, conditions, patterns, selections):
    db = str(tmp_path / "chat_log.db")
    with sqlite3.connect(db) as cx:
        init_auth_tables(cx)
        init_stress_tables(cx)
        tid = create_test(cx, "Sharon", "s@example.com", "2026-10-20")
        for label, pattern in patterns.items():
            remember_stress_pattern(cx, label, pattern)
        for label, remedies in selections.items():
            save_selection(cx, tid, label, remedies)
        cx.commit()
    app = create_app(db, scan_lookup=lambda e: _NO_SCAN,
                     fetch_profile=lambda e: {"conditions": conditions},
                     fetch_recent_comms=lambda e: {},
                     interpret_complete=lambda s, u: json.dumps({"layers": []}))
    return db, tid, app.test_client()


def _rows(db, tid):
    with sqlite3.connect(db) as cx:
        return cx.execute("SELECT id,head,most_affected,remedy FROM biofield_auth_chain "
                          "WHERE test_id=? ORDER BY layer,id", (int(str(tid).lstrip("a")),)
                          ).fetchall()


def _balances(db, tid, rid):
    with sqlite3.connect(db) as cx:
        return [r[0] for r in cx.execute(
            "SELECT s.label FROM biofield_auth_layer_stress ls JOIN biofield_auth_stress s "
            "ON s.id=ls.stress_id WHERE ls.test_id=? AND ls.chain_rid=? ORDER BY s.id",
            (int(str(tid).lstrip("a")), rid))]


def _program(client, tid):
    j = client.post(f"/author/{tid}/program", json={"mode": "minimum", "apply": True}).get_json()
    assert j["ok"] and j["applied"], j
    return j


def test_a_condition_with_a_pattern_names_the_layer_and_its_wording_balances_it(tmp_path):
    db, tid, client = _setup(tmp_path, [ADRENAL], {ADRENAL: "Adrenal"},
                             {ADRENAL: ["Endocrine Restore Sublingual Powder"]})
    _program(client, tid)
    (rid, head, tail, _remedy), = _rows(db, tid)
    assert (head, tail) == ("Adrenal", "Adrenal")
    assert _balances(db, tid, rid) == [ADRENAL]
    page = client.get(f"/author/{tid}").get_data(as_text=True)
    # The card's "balances:" chip carries the full wording.
    assert re.search(r"class=cchip>[^<]*" + re.escape(ADRENAL) + "</span>", page)
    assert ht.NO_PATTERN_FLAG not in page


def test_a_condition_with_no_pattern_keeps_todays_layer_and_its_card_is_flagged(tmp_path):
    db, tid, client = _setup(tmp_path, [ADRENAL], {}, {ADRENAL: ["Endocrine Restore"]})
    _program(client, tid)
    (rid, head, tail, _remedy), = _rows(db, tid)
    # Today's behaviour: the condition itself is the Head, and nothing is linked.
    assert head.lower().startswith("adrenal fatigue") and head != "Adrenal"
    assert _balances(db, tid, rid) == []
    page = client.get(f"/author/{tid}").get_data(as_text=True)
    assert ht.NO_PATTERN_FLAG in page


def test_two_conditions_with_one_pattern_share_one_card(tmp_path):
    """What happens, recorded rather than ruled on: two rows with the same Head are one
    card on the page, so both wordings sit in its Balances."""
    a, b = "Hot flashes, Current", "Low progesterone, Current"
    db, tid, client = _setup(tmp_path, [a, b], {a: "Hormone Balance", b: "Hormone Balance"},
                             {a: ["Remedy A"], b: ["Remedy B"]})
    _program(client, tid)
    rows = _rows(db, tid)
    assert {r[1] for r in rows} == {"Hormone Balance"} and len(rows) == 2
    with sqlite3.connect(db) as cx:
        data = list_stresses(cx, tid, [{"layer": 1, "head": r[1], "remedy": r[3], "rid": r[0]}
                                       for r in rows])
    (card,) = data["by_layer"]
    assert sorted(s["label"] for s in card["stresses"]) == [a, b]


def test_update_clinical_layers_puts_the_pattern_in_the_tail_and_the_wording_in_balances(
        tmp_path):
    db, tid, client = _setup(tmp_path, [ADRENAL], {ADRENAL: "Adrenal"},
                             {ADRENAL: ["Endocrine Restore"]})
    url = f"/author/{tid}/clinical-items/layers/update"
    j = client.post(url, json={}).get_json()
    chosen = [c["id"] for c in j["changes"] if c["preticked"]]
    assert client.post(url, json={"apply": True, "digest": j["digest"],
                                  "chosen": chosen}).status_code == 200
    (rid, head, tail, _remedy), = _rows(db, tid)
    assert (head, tail) == ("Adrenal", "Adrenal")
    assert _balances(db, tid, rid) == [ADRENAL]


def test_the_balance_button_links_the_wording_to_the_layer(tmp_path):
    db = str(tmp_path / "c.db")
    with sqlite3.connect(db) as cx:
        init_auth_tables(cx)
        tid = create_test(cx, "S", "s@x.com", "2026-10-20")
        balance_item(cx, tid, ADRENAL, 1, ["Endocrine Restore"], pattern="Adrenal")
        rows = cx.execute("SELECT id,head,most_affected FROM biofield_auth_chain").fetchall()
    assert {(r[1]) for r in rows} == {"Adrenal"}
    for rid, _h, _t in rows:
        assert _balances(db, tid, rid) == [ADRENAL]


def _a50_like(cx):
    """Sharon's chain in miniature, as the program and a consolidate left it."""
    init_auth_tables(cx)
    init_stress_tables(cx)
    tid = create_test(cx, "Sharon", "s@x.com", "2026-10-20")
    for label, pattern in ((ADRENAL, "Adrenal"), (MIGRAINE, "Migraine"),
                           (GLAUCOMA, "Eye Pressure Regulation")):
        remember_stress_pattern(cx, label, pattern)
        add_stress(cx, tid, label, source="tag")
        save_selection(cx, tid, label, ["x"])
    eye = add_chain_row(cx, tid, 5, "Eye and Vision Care",
                        f"Eyes Rejuvenator, {GLAUCOMA}, {GLAUCOMA}", "OcuFlow Bedtime",
                        origin="program")
    gj1 = add_chain_row(cx, tid, 8, "Green Jasper Gem Elixir", f"Nerve, {MIGRAINE}",
                        "Yarrow Blend", origin="program")
    gj2 = add_chain_row(cx, tid, 8, "Green Jasper Gem Elixir", f"Nerve, {MIGRAINE}",
                        "MigraFree", origin="program")
    adr = add_chain_row(cx, tid, 10, ADRENAL, ADRENAL, "Endocrine Restore", origin="program")
    sid = {lab: i for i, lab in cx.execute("SELECT id,label FROM biofield_auth_stress")}
    cx.execute("INSERT INTO biofield_auth_layer_stress VALUES(?,?,?)", (int(tid.lstrip("a")),
               sid[GLAUCOMA], 449))                      # row 449 is gone
    cx.execute("INSERT INTO biofield_auth_layer_stress VALUES(?,?,?)", (int(tid.lstrip("a")),
               sid[MIGRAINE], gj1))
    cx.commit()
    return tid, {"eye": eye, "gj1": gj1, "gj2": gj2, "adr": adr}, sid


def test_the_correction_keeps_a_head_glen_set_and_rewrites_only_wording(tmp_path):
    db = str(tmp_path / "c.db")
    with sqlite3.connect(db) as cx:
        tid, r, sid = _a50_like(cx)
        ht.correct_chain(cx, tid, dry_run=False)
        got = {i: (h, t) for i, h, t in cx.execute(
            "SELECT id,head,most_affected FROM biofield_auth_chain")}
    assert got[r["gj1"]] == ("Green Jasper Gem Elixir", "Nerve, Migraine")
    assert got[r["gj2"]] == ("Green Jasper Gem Elixir", "Nerve, Migraine")
    assert got[r["adr"]] == ("Adrenal", "Adrenal")
    assert got[r["eye"]] == ("Eye and Vision Care", "Eyes Rejuvenator, Eye Pressure Regulation")
    assert _balances(db, tid, r["gj2"]) == [MIGRAINE]
    assert _balances(db, tid, r["adr"]) == [ADRENAL]
    # The glaucoma wording now balances the Eye card, and the dead link is gone.
    assert _balances(db, tid, r["eye"]) == [GLAUCOMA]
    assert _balances(db, tid, 449) == []


def test_the_dry_run_writes_nothing(tmp_path):
    db = str(tmp_path / "c.db")
    with sqlite3.connect(db) as cx:
        tid, _r, _sid = _a50_like(cx)
        before = list(cx.iterdump())
    ro = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    plan = ht.correct_chain(ro, tid)            # dry_run is the default
    ro.close()
    assert plan["rows"] and plan["links"] and plan["dangling"]
    with sqlite3.connect(db) as cx:
        assert list(cx.iterdump()) == before


def test_a_deleted_rows_balances_move_to_the_rest_of_its_card(tmp_path):
    db = str(tmp_path / "c.db")
    with sqlite3.connect(db) as cx:
        init_auth_tables(cx)
        init_stress_tables(cx)
        tid = create_test(cx, "S", "s@x.com", "2026-10-20")
        a = add_chain_row(cx, tid, 5, "Eye and Vision Care", "", "Remedy A")
        b = add_chain_row(cx, tid, 5, "Eye and Vision Care", "", "Remedy B")
        add_stress(cx, tid, GLAUCOMA, source="tag")
        (sid,) = cx.execute("SELECT id FROM biofield_auth_stress").fetchone()
        cx.execute("INSERT INTO biofield_auth_layer_stress VALUES(?,?,?)",
                   (int(tid.lstrip("a")), sid, a))
        cx.commit()
        assert remove_remedy_preserving_layer(cx, tid, a) is True
        data = list_stresses(cx, tid, [{"layer": 1, "head": "Eye and Vision Care",
                                        "remedy": "Remedy B", "rid": b}])
    assert _balances(db, tid, b) == [GLAUCOMA]
    assert [s["label"] for s in data["by_layer"][0]["stresses"]] == [GLAUCOMA]
    assert [s["label"] for s in data["balanced"]] == [GLAUCOMA]


def test_a_stress_whose_last_row_is_deleted_shows_as_unbalanced(tmp_path):
    db, tid, client = _setup(tmp_path, [], {}, {})
    with sqlite3.connect(db) as cx:
        rid = add_chain_row(cx, tid, 1, "Eye and Vision Care", "", "Remedy A")
        add_stress(cx, tid, GLAUCOMA, source="tag")
        (sid,) = cx.execute("SELECT id FROM biofield_auth_stress").fetchone()
        cx.execute("INSERT INTO biofield_auth_layer_stress VALUES(?,?,?)",
                   (int(tid.lstrip("a")), sid, rid))
        cx.commit()
    j = client.post(f"/author/{tid}/row/{rid}/delete",
                    json={"remove_layer": True, "layer_rids": [rid]}).get_json()
    assert j["ok"]
    with sqlite3.connect(db) as cx:
        assert cx.execute("SELECT COUNT(*) FROM biofield_auth_layer_stress").fetchone()[0] == 0
        data = list_stresses(cx, tid, [])
    assert [s["label"] for s in data["active"]] == [GLAUCOMA]
    assert [s["label"] for s in data["unassigned"]] == [GLAUCOMA]


def test_a_link_left_on_a_deleted_row_no_longer_reads_as_balanced(tmp_path):
    """a50's stress 1914, linked only to row 449 after it was deleted."""
    db = str(tmp_path / "c.db")
    with sqlite3.connect(db) as cx:
        init_auth_tables(cx)
        init_stress_tables(cx)
        tid = create_test(cx, "S", "s@x.com", "2026-10-20")
        add_stress(cx, tid, GLAUCOMA, source="tag")
        (sid,) = cx.execute("SELECT id FROM biofield_auth_stress").fetchone()
        cx.execute("INSERT INTO biofield_auth_layer_stress VALUES(?,?,?)",
                   (int(tid.lstrip("a")), sid, 449))
        cx.commit()
        data = list_stresses(cx, tid, [])
    assert [s["label"] for s in data["active"]] == [GLAUCOMA]


def test_a_folded_layers_balances_go_to_the_layer_it_folds_into(tmp_path):
    from dashboard.biofield_chain_fold import fold_layer
    db = str(tmp_path / "c.db")
    with sqlite3.connect(db) as cx:
        init_auth_tables(cx)
        init_stress_tables(cx)
        tid = create_test(cx, "S", "s@x.com", "2026-10-20")
        a = add_chain_row(cx, tid, 1, "Gut", "", "Microbiome", confirmed=0)
        b = add_chain_row(cx, tid, 2, "Bowel", "", "Microbiome", confirmed=0)
        add_stress(cx, tid, "Bloating, Current", source="tag")
        (sid,) = cx.execute("SELECT id FROM biofield_auth_stress").fetchone()
        cx.execute("INSERT INTO biofield_auth_layer_stress VALUES(?,?,?)",
                   (int(tid.lstrip("a")), sid, b))
        cx.commit()
        assert fold_layer(cx, tid, 2, [b])
    assert _balances(db, tid, a) == ["Bloating, Current"]
    assert _balances(db, tid, b) == []


def test_balance_all_names_a_condition_layer_by_its_pattern(tmp_path):
    db, tid, client = _setup(tmp_path, [], {ADRENAL: "Adrenal"}, {})
    with sqlite3.connect(db) as cx:
        add_stress(cx, tid, ADRENAL, source="tag")
    j = client.post(f"/author/{tid}/balance-all", json={"apply": True}).get_json()
    assert j["ok"] and j["layers_added"] == 1, j
    (rid, head, tail, _remedy), = _rows(db, tid)
    assert (head, tail) == ("Adrenal", "Adrenal")
    assert _balances(db, tid, rid) == [ADRENAL]


def test_layer_fields_replaces_only_covers_with_a_stored_pattern():
    """The rule _append_layers and the program share. With no pattern it returns what
    those paths wrote before."""
    patterns = {"adrenal fatigue current 2023": "Adrenal"}
    assert ht.layer_fields(["Kidney Driver", ADRENAL], patterns) == \
        ("Kidney Driver", "Kidney Driver, Adrenal", [ADRENAL])
    assert ht.layer_fields([ADRENAL, "Lymph Star"], patterns) == \
        ("Adrenal", "Adrenal, Lymph Star", [ADRENAL])
    assert ht.layer_fields([ADRENAL, "Lymph Star"], {}) == \
        (ADRENAL, f"{ADRENAL}, Lymph Star", [])


def test_add_one_from_the_remedy_set_names_a_condition_layer_by_its_pattern(
        tmp_path, monkeypatch):
    from dashboard import biofield_stress as st
    db, tid, client = _setup(tmp_path, [], {ADRENAL: "Adrenal"}, {})
    with sqlite3.connect(db) as cx:      # no email: the species gate has nothing to ask
        cx.execute("UPDATE biofield_auth_tests SET email=''")
    monkeypatch.setattr(st, "resolve_remedy_set", lambda *a, **k: {
        "picks": [{"remedy": "Endocrine Restore", "covers": [ADRENAL]}]})
    j = client.post(f"/author/{tid}/remedy-set/add-one",
                    json={"remedy": "Endocrine Restore"}).get_json()
    assert j.get("added") == 1, j
    (rid, head, tail, _remedy), = _rows(db, tid)
    assert (head, tail) == ("Adrenal", "Adrenal")
    assert _balances(db, tid, rid) == [ADRENAL]


@pytest.mark.parametrize("with_pattern", [True, False])
def test_consolidate_moves_a_conditions_wording_as_one_stress(tmp_path, with_pattern):
    """Consolidate split a Tail on commas, so a50 grew stresses "Current", "2020" and
    "Past". A condition's full wording moves whole, pattern or not; short terms still
    split."""
    from dashboard.biofield_stress import consolidate_layer_balances
    db = str(tmp_path / "c.db")
    with sqlite3.connect(db) as cx:
        init_auth_tables(cx)
        init_stress_tables(cx)
        tid = create_test(cx, "S", "s@x.com", "2026-10-20")
        if with_pattern:
            remember_stress_pattern(cx, GLAUCOMA, "Eye Pressure Regulation")
        save_selection(cx, tid, GLAUCOMA, ["OcuFlow Bedtime"])
        add_chain_row(cx, tid, 1, "Eye and Vision Care", "Eyes Rejuvenator", "IOP Syntropy")
        add_chain_row(cx, tid, 2, GLAUCOMA, f"Lens Clarity, {GLAUCOMA}", "OcuFlow Bedtime")
        consolidate_layer_balances(cx, tid, 2, 1)
        labels = sorted(r[0] for r in cx.execute(
            "SELECT label FROM biofield_auth_stress WHERE source='chain'"))
        tails = {r[0] for r in cx.execute("SELECT most_affected FROM biofield_auth_chain")}
    assert labels == sorted([GLAUCOMA, "Lens Clarity"])
    assert "Current" not in labels and "2020" not in labels
    assert tails == {f"Eyes Rejuvenator, {GLAUCOMA}, Lens Clarity"}
