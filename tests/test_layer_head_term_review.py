"""Head term, review rounds 1 and 2 (2026-10-20). See test_layer_head_is_a_body_term.

Each test here pins one finding, so a regression names the finding it reopens.
"""
import inspect
import json
import sqlite3

import pytest

import biofield_local_app
from biofield_local_app import create_app
from dashboard import biofield_clinical_head_term as ht
from dashboard.biofield_authoring import (add_chain_row, create_test, delete_chain_row,
                                          init_auth_tables)
from dashboard.biofield_clinical_checklist import remember_stress_pattern
from dashboard.biofield_clinical_proposals import save_pattern, save_selection
from dashboard.biofield_stress import (add_stress, init_stress_tables, list_stresses,
                                       move_links_off)

ADRENAL = "Adrenal Fatigue, Current, 2023"
GLAUCOMA = "Suspect of Glaucoma/high pressures and thinning cornea, Current, 2020"
_NO_SCAN = {"status": "none", "found": False, "findings": [], "days_ago": None,
            "fresh": False}


@pytest.fixture(autouse=True)
def _no_gate(monkeypatch):
    monkeypatch.delenv("CONSOLE_SECRET", raising=False)
    import dashboard as _d
    monkeypatch.setattr(_d, "CONSOLE_SECRET", "", raising=False)


def _db(tmp_path):
    db = str(tmp_path / "c.db")
    cx = sqlite3.connect(db)
    init_auth_tables(cx)
    init_stress_tables(cx)
    tid = create_test(cx, "S", "s@x.com", "2026-10-20")
    return db, cx, tid


def _n(tid):
    return int(str(tid).lstrip("a"))


def _link(cx, tid, sid, rid):
    cx.execute("INSERT INTO biofield_auth_layer_stress VALUES(?,?,?)", (_n(tid), sid, rid))
    cx.commit()


def _sid(cx, label):
    return cx.execute("SELECT id FROM biofield_auth_stress WHERE label=?", (label,)).fetchone()[0]


def _balances(cx, tid, rid):
    return [r[0] for r in cx.execute(
        "SELECT s.label FROM biofield_auth_layer_stress ls JOIN biofield_auth_stress s "
        "ON s.id=ls.stress_id WHERE ls.test_id=? AND ls.chain_rid=? ORDER BY s.id",
        (_n(tid), rid))]


def _row(cx, rid):
    return cx.execute("SELECT head, most_affected FROM biofield_auth_chain WHERE id=?",
                      (rid,)).fetchone()


def _client(db, conditions=()):
    return create_app(db, scan_lookup=lambda e: _NO_SCAN,
                      fetch_profile=lambda e: {"conditions": list(conditions)},
                      fetch_recent_comms=lambda e: {},
                      interpret_complete=lambda s, u: json.dumps({"layers": []})
                      ).test_client()


# 1. move_links_off works per card (round 2 High, round 1 F6) -----------------------

def test_a_batch_delete_keeps_each_cards_balances_on_its_own_card(tmp_path):
    db, cx, tid = _db(tmp_path)
    a = add_chain_row(cx, tid, 1, "Eye", "", "A")
    a2 = add_chain_row(cx, tid, 1, "Eye", "", "A2")
    b = add_chain_row(cx, tid, 2, "Lens", "", "B")
    b2 = add_chain_row(cx, tid, 2, "Lens", "", "B2")
    add_stress(cx, tid, GLAUCOMA, source="tag")
    add_stress(cx, tid, "Cataract", source="tag")
    _link(cx, tid, _sid(cx, GLAUCOMA), a)
    _link(cx, tid, _sid(cx, "Cataract"), b)
    move_links_off(cx, tid, [a, b])
    assert _balances(cx, tid, a2) == [GLAUCOMA]
    assert _balances(cx, tid, b2) == ["Cataract"]


def test_a_card_with_no_survivor_never_borrows_another_cards_rows(tmp_path):
    db, cx, tid = _db(tmp_path)
    a = add_chain_row(cx, tid, 1, "Eye", "", "A")
    b = add_chain_row(cx, tid, 2, "Lens", "", "B")
    b2 = add_chain_row(cx, tid, 2, "Lens", "", "B2")
    add_stress(cx, tid, GLAUCOMA, source="tag")
    _link(cx, tid, _sid(cx, GLAUCOMA), a)
    out = move_links_off(cx, tid, [a, b])
    assert _balances(cx, tid, b2) == []
    assert out["unbalanced"] == [_sid(cx, GLAUCOMA)]


# 2. Which stresses count as conditions (round 1 F1) --------------------------------

@pytest.mark.parametrize("source", ["voice", "comm", "manual", "chain", "scan"])
def test_a_pattern_never_rewrites_a_head_from_a_non_condition_stress(tmp_path, source):
    db, cx, tid = _db(tmp_path)
    remember_stress_pattern(cx, "Digestion, poor", "GI - Gastro-Intestinal tract")
    add_stress(cx, tid, "Digestion, poor", source=source)
    rid = add_chain_row(cx, tid, 1, "Digestion, poor", "Digestion, poor, Kidney", "X")
    ht.correct_chain(cx, tid, dry_run=False)
    assert _row(cx, rid) == ("Digestion, poor", "Digestion, poor, Kidney")


@pytest.mark.parametrize("source", ["tag", "paste", "clinical"])
def test_a_condition_stress_is_rewritten(tmp_path, source):
    db, cx, tid = _db(tmp_path)
    remember_stress_pattern(cx, "Digestion, poor", "GI - Gastro-Intestinal tract")
    add_stress(cx, tid, "Digestion, poor", source=source)
    rid = add_chain_row(cx, tid, 1, "Digestion, poor", "Digestion, poor, Kidney", "X")
    ht.correct_chain(cx, tid, dry_run=False)
    assert _row(cx, rid) == ("GI - Gastro-Intestinal tract",
                             "GI - Gastro-Intestinal tract, Kidney")


# 3. Exact full-wording match (round 2 Medium, round 1 F3) --------------------------

def test_a_head_glen_typed_without_the_punctuation_stays(tmp_path):
    db, cx, tid = _db(tmp_path)
    remember_stress_pattern(cx, ADRENAL, "Adrenal")
    save_selection(cx, tid, ADRENAL, ["x"])
    typed = add_chain_row(cx, tid, 1, "Adrenal fatigue current 2023",
                          "Nerve, adrenal fatigue current 2023", "X")
    cased = add_chain_row(cx, tid, 2, "  ADRENAL FATIGUE, CURRENT, 2023 ",
                          "Nerve,  adrenal fatigue, current, 2023", "Y")
    ht.correct_chain(cx, tid, dry_run=False)
    assert _row(cx, typed) == ("Adrenal fatigue current 2023",
                               "Nerve, adrenal fatigue current 2023")
    assert _row(cx, cased) == ("Adrenal", "Nerve, Adrenal")


def test_a_wording_inside_a_longer_item_is_not_a_match(tmp_path):
    """Round 1 M2: the match must end at a separator."""
    db, cx, tid = _db(tmp_path)
    remember_stress_pattern(cx, ADRENAL, "Adrenal")
    save_selection(cx, tid, ADRENAL, ["x"])
    rid = add_chain_row(cx, tid, 1, "Kidney", f"{ADRENAL} worse in winter", "X")
    ht.correct_chain(cx, tid, dry_run=False)
    assert _row(cx, rid) == ("Kidney", f"{ADRENAL} worse in winter")


# 4. Balance All puts the pattern in the Tail too (round 2 Medium) ------------------

def test_balance_all_with_two_members_keeps_the_pattern_in_the_tail(tmp_path, monkeypatch):
    import dashboard.layer_grouping as lg
    db, cx, tid = _db(tmp_path)
    remember_stress_pattern(cx, ADRENAL, "Adrenal")
    add_stress(cx, tid, ADRENAL, source="tag")
    add_stress(cx, tid, "Other stress", source="tag")
    cx.close()
    monkeypatch.setattr(lg, "group_findings", lambda f: [{
        "why": "test", "members": sorted(f, key=lambda m: m["name"] != ADRENAL)}])
    j = _client(db).post(f"/author/{tid}/balance-all", json={"apply": True}).get_json()
    assert j["ok"] and j["layers_added"] == 1, j
    with sqlite3.connect(db) as cx:
        (rid, head, tail), = cx.execute("SELECT id,head,most_affected FROM biofield_auth_chain")
        assert (head, tail) == ("Adrenal", "Adrenal, Other stress")
        assert _balances(cx, tid, rid) == [ADRENAL]


# 5. The flag counts a Tail item too (round 2 Medium) -------------------------------

def test_a_tail_item_with_no_pattern_flags_the_card(tmp_path):
    db, cx, tid = _db(tmp_path)
    add_chain_row(cx, tid, 1, "Kidney Driver", f"Kidney Driver, {ADRENAL}", "X")
    cx.close()
    page = _client(db, [ADRENAL]).get(f"/author/{tid}").get_data(as_text=True)
    assert ht.NO_PATTERN_FLAG in page


def test_no_flag_when_the_tail_condition_has_a_pattern(tmp_path):
    db, cx, tid = _db(tmp_path)
    remember_stress_pattern(cx, ADRENAL, "Adrenal")
    add_chain_row(cx, tid, 1, "Kidney Driver", f"Kidney Driver, {ADRENAL}", "X")
    cx.close()
    page = _client(db, [ADRENAL]).get(f"/author/{tid}").get_data(as_text=True)
    assert ht.NO_PATTERN_FLAG not in page


# 6. Atomic batches (round 2 Medium) ------------------------------------------------

def _post_swallowing(client, url, body):
    try:
        return client.post(url, json=body)
    except RuntimeError:
        return None


def test_a_program_that_fails_part_way_writes_nothing(tmp_path, monkeypatch):
    db, cx, tid = _db(tmp_path)
    for label, pattern in ((ADRENAL, "Adrenal"), (GLAUCOMA, "Eye Pressure Regulation")):
        remember_stress_pattern(cx, label, pattern)
        save_selection(cx, tid, label, [label[:5] + " Remedy"])
    cx.commit()
    cx.close()
    calls = []

    def dosing(cx, name):
        calls.append(name)
        if len(calls) == 2:
            raise RuntimeError("dosing failed on the second layer")
        return {}
    monkeypatch.setattr(biofield_local_app, "remedy_dosing", dosing)
    r = _post_swallowing(_client(db), f"/author/{tid}/program",
                         {"mode": "full", "apply": True})
    assert len(calls) == 2, (calls, r and r.get_data(as_text=True)[:500])
    assert r is None or r.status_code == 500
    with sqlite3.connect(db) as cx:
        assert cx.execute("SELECT COUNT(*) FROM biofield_auth_chain").fetchone()[0] == 0
        assert cx.execute("SELECT COUNT(*) FROM biofield_auth_layer_stress").fetchone()[0] == 0


def test_balance_all_that_fails_part_way_writes_nothing(tmp_path, monkeypatch):
    import dashboard.layer_grouping as lg
    db, cx, tid = _db(tmp_path)
    remember_stress_pattern(cx, ADRENAL, "Adrenal")
    add_stress(cx, tid, ADRENAL, source="tag")
    add_stress(cx, tid, "Other stress", source="tag")
    cx.close()
    monkeypatch.setattr(lg, "group_findings",
                        lambda f: [{"why": "", "members": [m]} for m in
                                   sorted(f, key=lambda m: m["name"] != ADRENAL)])
    real, calls = ht.link_wording, []

    def link(*a, **k):
        calls.append(1)
        if len(calls) == 2:
            raise RuntimeError("link failed on the second layer")
        return real(*a, **k)
    monkeypatch.setattr(ht, "link_wording", link)
    _post_swallowing(_client(db), f"/author/{tid}/balance-all", {"apply": True})
    assert len(calls) == 2
    with sqlite3.connect(db) as cx:
        assert cx.execute("SELECT COUNT(*) FROM biofield_auth_chain").fetchone()[0] == 0
        assert cx.execute("SELECT COUNT(*) FROM biofield_auth_layer_stress").fetchone()[0] == 0


# 7. A corrected Tail keeps Glen's separators and repeats (round 1 F4) --------------

def test_the_tail_correction_replaces_only_the_matched_item(tmp_path):
    db, cx, tid = _db(tmp_path)
    remember_stress_pattern(cx, ADRENAL, "Adrenal")
    save_selection(cx, tid, ADRENAL, ["x"])
    rid = add_chain_row(cx, tid, 1, "Kidney", f"Nerve; Liver\nNERVE, {ADRENAL}", "X")
    ht.correct_chain(cx, tid, dry_run=False)
    assert _row(cx, rid) == ("Kidney", "Nerve; Liver\nNERVE, Adrenal")


# 8. A second correction changes nothing (round 1 F5) -------------------------------

def test_a_second_correction_changes_nothing_when_a_pattern_is_a_wording(tmp_path):
    db, cx, tid = _db(tmp_path)
    remember_stress_pattern(cx, "Overweight, Current", "Excess belly fat")
    remember_stress_pattern(cx, "Excess belly fat", "Fat Metabolism")
    save_selection(cx, tid, "Overweight, Current", ["x"])
    save_selection(cx, tid, "Excess belly fat", ["y"])
    rid = add_chain_row(cx, tid, 1, "Overweight, Current", "Overweight, Current", "X")
    ht.correct_chain(cx, tid, dry_run=False)
    assert _row(cx, rid) == ("Fat Metabolism", "Fat Metabolism")
    before = list(cx.iterdump())
    again = ht.correct_chain(cx, tid, dry_run=False)
    assert not again["rows"] and not again["links"]
    assert list(cx.iterdump()) == before


def test_a_condition_named_by_its_own_wording_is_not_a_rewrite(tmp_path):
    """Round 1 M20: the self-named skip."""
    db, cx, tid = _db(tmp_path)
    remember_stress_pattern(cx, "Adrenal", "Adrenal")
    save_selection(cx, tid, "Adrenal", ["x"])
    add_chain_row(cx, tid, 1, "Adrenal", "Adrenal", "X")
    plan = ht.correct_chain(cx, tid)
    assert plan["rows"] == [] and plan["links"] == []


# 9. Update Clinical layers names a layer by the stored pattern (round 1 F7) --------

def _sync(db, tid, conditions):
    client = _client(db, conditions)
    url = f"/author/{tid}/clinical-items/layers/update"
    j = client.post(url, json={}).get_json()
    chosen = [c["id"] for c in j["changes"] if c["preticked"]]
    assert client.post(url, json={"apply": True, "digest": j["digest"],
                                  "chosen": chosen}).status_code == 200


def test_update_clinical_layers_prefers_the_pattern_typed_for_this_test(tmp_path):
    """Round 3 N1: the typed pattern wins, as it does on the + button."""
    db, cx, tid = _db(tmp_path)
    remember_stress_pattern(cx, ADRENAL, "Adrenal")
    save_selection(cx, tid, ADRENAL, ["Endocrine Restore"])
    save_pattern(cx, tid, ADRENAL, "Typed for this test")
    cx.commit()
    cx.close()
    _sync(db, tid, [ADRENAL])
    with sqlite3.connect(db) as cx:
        assert {r[0] for r in cx.execute("SELECT head FROM biofield_auth_chain")} == \
            {"Typed for this test"}


def test_update_clinical_layers_falls_back_to_the_stored_pattern(tmp_path):
    db, cx, tid = _db(tmp_path)
    remember_stress_pattern(cx, ADRENAL, "Adrenal")
    save_selection(cx, tid, ADRENAL, ["Endocrine Restore"])
    cx.commit()
    cx.close()
    _sync(db, tid, [ADRENAL])
    with sqlite3.connect(db) as cx:
        assert {r[0] for r in cx.execute("SELECT head FROM biofield_auth_chain")} == {"Adrenal"}


def test_the_plus_button_and_update_clinical_layers_agree(tmp_path):
    """Round 3 N1 probe: + names the layer "Kidney"; Update must not add "Adrenal"."""
    db, cx, tid = _db(tmp_path)
    remember_stress_pattern(cx, ADRENAL, "Adrenal")
    save_selection(cx, tid, ADRENAL, ["Endocrine Restore"])
    save_pattern(cx, tid, ADRENAL, "Kidney")
    cx.commit()
    cx.close()
    client = _client(db, [ADRENAL])
    j = client.post(f"/author/{tid}/clinical-items/balance", json={
        "label": ADRENAL, "layer": 1, "remedies": ["Endocrine Restore"],
        "pattern": "Kidney"}).get_json()
    assert j["ok"], j
    plan = client.post(f"/author/{tid}/clinical-items/layers/update", json={}).get_json()
    assert [c for c in plan["changes"] if c["kind"] == "new"] == []


# 3b. A pattern cycle is no pattern (round 3 N3) ------------------------------------

def test_a_pattern_cycle_is_treated_as_no_pattern_and_flagged(tmp_path):
    db, cx, tid = _db(tmp_path)
    remember_stress_pattern(cx, "Alpha cond", "Beta cond")
    remember_stress_pattern(cx, "Beta cond", "Alpha cond")
    add_stress(cx, tid, "Alpha cond", source="tag")
    add_stress(cx, tid, "Beta cond", source="tag")
    rid = add_chain_row(cx, tid, 1, "Alpha cond", "Alpha cond", "X")
    assert ht.pattern_cycles(cx, tid) == {"alpha cond", "beta cond"}
    plan = ht.correct_chain(cx, tid, dry_run=False)
    assert plan["rows"] == [] and _row(cx, rid) == ("Alpha cond", "Alpha cond")
    cx.close()
    page = _client(db, ["Alpha cond"]).get(f"/author/{tid}").get_data(as_text=True)
    assert ht.NO_PATTERN_FLAG in page


# 7b. A replaced item is stored without stray whitespace (round 3 N4) ---------------

def test_a_replaced_tail_never_starts_with_a_space(tmp_path):
    db, cx, tid = _db(tmp_path)
    remember_stress_pattern(cx, ADRENAL, "Adrenal")
    save_selection(cx, tid, ADRENAL, ["x"])
    first = add_chain_row(cx, tid, 1, "Kidney", f"{ADRENAL}, Adrenal", "X")
    spaced = add_chain_row(cx, tid, 2, "Liver", f"Nerve,   {ADRENAL}  ;Liver", "Y")
    ht.correct_chain(cx, tid, dry_run=False)
    assert _row(cx, first) == ("Kidney", "Adrenal")
    assert _row(cx, spaced) == ("Liver", "Nerve, Adrenal;Liver")


# 5. Round 3 mutation gaps -----------------------------------------------------------

def test_a_program_failing_after_a_transcript_layer_writes_nothing(tmp_path, monkeypatch):
    """Round 3 M9: the transcript's rows are in the same batch."""
    db, cx, tid = _db(tmp_path)
    remember_stress_pattern(cx, ADRENAL, "Adrenal")
    save_selection(cx, tid, ADRENAL, ["Adren Remedy"])
    cx.commit()
    cx.close()
    client = create_app(db, scan_lookup=lambda e: _NO_SCAN,
                        fetch_profile=lambda e: {}, fetch_recent_comms=lambda e: {},
                        interpret_complete=lambda s, u: json.dumps({"layers": [
                            {"layer": 1, "head": "Grief", "most_affected": "Grief",
                             "remedy": "Heart Health", "dosage": "1", "frequency": "1",
                             "timing": "am"}]})).test_client()
    client.post(f"/author/{tid}/session", json={"transcript": "she spoke of grief"})
    seen = []

    def dosing(cx, name):
        seen.append(name)
        if name.startswith("Adren"):
            raise RuntimeError("dosing failed on the Mind pick")
        return {}
    monkeypatch.setattr(biofield_local_app, "remedy_dosing", dosing)
    _post_swallowing(client, f"/author/{tid}/program", {"mode": "full", "apply": True})
    assert any(n.startswith("Adren") for n in seen)
    with sqlite3.connect(db) as cx:
        assert cx.execute("SELECT COUNT(*) FROM biofield_auth_chain").fetchone()[0] == 0


def test_the_correction_links_the_wording_to_every_row_of_the_card(tmp_path):
    """Round 3 M20: one row holds the wording, the card has two."""
    db, cx, tid = _db(tmp_path)
    remember_stress_pattern(cx, GLAUCOMA, "Eye Pressure Regulation")
    save_selection(cx, tid, GLAUCOMA, ["x"])
    a = add_chain_row(cx, tid, 1, "Eye and Vision Care", f"Eyes, {GLAUCOMA}", "A")
    b = add_chain_row(cx, tid, 1, "Eye and Vision Care", "", "B")
    ht.correct_chain(cx, tid, dry_run=False)
    assert _balances(cx, tid, a) == [GLAUCOMA]
    assert _balances(cx, tid, b) == [GLAUCOMA]


# 10. Paths the round 1 mutations showed untested -----------------------------------

def test_update_clinical_layers_add_links_the_wording_to_the_card(tmp_path):
    """Round 1 M10."""
    db, cx, tid = _db(tmp_path)
    remember_stress_pattern(cx, ADRENAL, "Adrenal")
    a = add_chain_row(cx, tid, 1, "Adrenal", "Adrenal", "Old Remedy", confirmed=0)
    save_selection(cx, tid, ADRENAL, ["Old Remedy", "New Remedy"])
    cx.commit()
    cx.close()
    _sync(db, tid, [ADRENAL])
    with sqlite3.connect(db) as cx:
        rids = [r[0] for r in cx.execute("SELECT id FROM biofield_auth_chain ORDER BY id")]
        assert len(rids) == 2
        for rid in rids:
            assert _balances(cx, tid, rid) == [ADRENAL]


def test_update_clinical_layers_remove_moves_the_balances(tmp_path):
    """Round 1 M18: removing one remedy row keeps its Balances on the card."""
    db, cx, tid = _db(tmp_path)
    remember_stress_pattern(cx, ADRENAL, "Adrenal")
    keep = add_chain_row(cx, tid, 1, "Adrenal", "Adrenal", "Keep", confirmed=0)
    drop = add_chain_row(cx, tid, 1, "Adrenal", "", "Drop", confirmed=0)
    add_stress(cx, tid, ADRENAL, source="tag")
    _link(cx, tid, _sid(cx, ADRENAL), drop)
    save_selection(cx, tid, ADRENAL, ["Keep"])
    cx.commit()
    cx.close()
    client = _client(db, [ADRENAL])
    url = f"/author/{tid}/clinical-items/layers/update"
    j = client.post(url, json={}).get_json()
    chosen = [c["id"] for c in j["changes"] if c["kind"] == "remove"]
    assert chosen
    assert client.post(url, json={"apply": True, "digest": j["digest"],
                                  "chosen": chosen}).status_code == 200
    with sqlite3.connect(db) as cx:
        assert [r[0] for r in cx.execute("SELECT id FROM biofield_auth_chain")] == [keep]
        assert _balances(cx, tid, keep) == [ADRENAL]


def test_delete_chain_row_moves_the_balances(tmp_path):
    """Round 1 M19."""
    db, cx, tid = _db(tmp_path)
    a = add_chain_row(cx, tid, 1, "Eye", "", "A")
    b = add_chain_row(cx, tid, 1, "Eye", "", "B")
    add_stress(cx, tid, GLAUCOMA, source="tag")
    _link(cx, tid, _sid(cx, GLAUCOMA), a)
    delete_chain_row(cx, a)
    assert _balances(cx, tid, b) == [GLAUCOMA]


def test_drop_fragments_is_off_by_default(tmp_path):
    """Round 1 M21. Deleting the fragment stresses is Glen's call."""
    assert inspect.signature(ht.correct_chain).parameters["drop_fragments"].default is False
    db, cx, tid = _db(tmp_path)
    remember_stress_pattern(cx, ADRENAL, "Adrenal")
    save_selection(cx, tid, ADRENAL, ["x"])
    add_chain_row(cx, tid, 1, ADRENAL, ADRENAL, "X")
    cx.execute("INSERT INTO biofield_auth_stress(test_id,code,label,source,balance,"
               "manual_balanced) VALUES(?,?,?,'chain','required',0)", (_n(tid), "current",
                                                                     "Current"))
    cx.commit()
    plan = ht.correct_chain(cx, tid, dry_run=False)
    assert [f["label"] for f in plan["fragments"]] == ["Current"]
    assert cx.execute("SELECT COUNT(*) FROM biofield_auth_stress WHERE label='Current'"
                      ).fetchone()[0] == 1


def test_a_directly_placed_stress_names_its_remedy_and_layer(tmp_path):
    """Round 1 M22: not "assigned layer"."""
    db, cx, tid = _db(tmp_path)
    rid = add_chain_row(cx, tid, 4, "Adrenal", "Adrenal", "Endocrine Restore")
    add_stress(cx, tid, ADRENAL, source="tag")
    _link(cx, tid, _sid(cx, ADRENAL), rid)
    data = list_stresses(cx, tid, [{"layer": 1, "head": "Adrenal",
                                    "remedy": "Endocrine Restore", "rid": rid}])
    (s,) = [s for s in data["balanced"] if s["label"] == ADRENAL]
    assert s["balanced_by"] == "Endocrine Restore" and s["balanced_layer"] == 4


def test_a_chain_fragment_is_not_a_condition_wording(tmp_path):
    """Round 1 M23: a "chain" stress is what an earlier split made."""
    from dashboard.biofield_stress import condition_wordings
    db, cx, tid = _db(tmp_path)
    cx.execute("INSERT INTO biofield_auth_stress(test_id,code,label,source,balance,"
               "manual_balanced) VALUES(?,?,?,'chain','required',0)",
               (_n(tid), "a, b", "Kidney, Liver"))
    add_stress(cx, tid, ADRENAL, source="tag")
    assert condition_wordings(cx, tid) == [ADRENAL]


def test_apply_to_chain_that_fails_part_way_writes_nothing(tmp_path, monkeypatch):
    from dashboard import biofield_stress as st
    db, cx, tid = _db(tmp_path)
    cx.execute("UPDATE biofield_auth_tests SET email=''")   # no species question
    remember_stress_pattern(cx, ADRENAL, "Adrenal")
    add_stress(cx, tid, ADRENAL, source="tag")
    cx.commit()
    cx.close()
    monkeypatch.setattr(st, "resolve_remedy_set", lambda *a, **k: {"picks": [
        {"remedy": "One", "covers": [ADRENAL]}, {"remedy": "Two", "covers": ["Kidney"]}]})
    calls = []

    def dosing(cx, name):
        calls.append(name)
        if len(calls) == 2:
            raise RuntimeError("dosing failed on the second layer")
        return {}
    monkeypatch.setattr(biofield_local_app, "remedy_dosing", dosing)
    _post_swallowing(_client(db), f"/author/{tid}/remedy-set/apply-to-chain",
                     {"remedies": ["One", "Two"]})
    assert len(calls) == 2
    with sqlite3.connect(db) as cx:
        assert cx.execute("SELECT COUNT(*) FROM biofield_auth_chain").fetchone()[0] == 0
        assert cx.execute("SELECT COUNT(*) FROM biofield_auth_layer_stress").fetchone()[0] == 0
