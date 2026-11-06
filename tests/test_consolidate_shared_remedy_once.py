"""Consolidating two layer cards that share a remedy keeps one row for it.

Glen, 2026-11-05 (primary tab): "When I consolidate 2 layers in Biofield Intake and
they contain a common remedy, don't duplicate it." Consolidation moved every source
row onto the target card, so a remedy on both cards showed twice. The merged card now
keeps the target's row, carrying the stresses of both cards. Remedies match by the
catalog identity the layer fold uses (`biofield_chain_fold._remedy_key`, Glen
2026-10-22: "do not duplicate an identical remedy").
"""
import sqlite3

import pytest

from dashboard import biofield_stress as st
from dashboard.biofield_authoring import (add_chain_row, create_test, init_auth_tables,
                                          ordered_chain)


@pytest.fixture(autouse=True)
def _no_gate(monkeypatch):
    monkeypatch.delenv("CONSOLE_SECRET", raising=False)
    import dashboard
    monkeypatch.setattr(dashboard, "CONSOLE_SECRET", "", raising=False)


def _row(cx, tid, layer, head, remedy):
    add_chain_row(cx, tid, layer, head, "", remedy)
    return cx.execute("SELECT max(id) FROM biofield_auth_chain").fetchone()[0]


def _stress(cx, tid, label, rids):
    st.add_stress(cx, tid, label, source="scan", balance="required")
    sid = cx.execute("SELECT max(id) FROM biofield_auth_stress").fetchone()[0]
    st.cover_stress(cx, tid, sid, rids)
    return sid


def _two_cards(db, b_name="Eye Support"):
    with sqlite3.connect(db) as cx:
        init_auth_tables(cx)
        st.init_stress_tables(cx)
        tid = create_test(cx, "Fixture", "fixture@example.com", "2026-11-05")
        a_eye = _row(cx, tid, 3, "Dry eye", "Eye Support")
        a_omega = _row(cx, tid, 3, "Dry eye", "Omega")
        b_eye = _row(cx, tid, 5, "Fatigue", b_name)
        b_adrenal = _row(cx, tid, 5, "Fatigue", "Adrenal")
        cx.execute("UPDATE biofield_auth_chain SET dosage='2 caps', timing='morning' "
                   "WHERE id=?", (b_eye,))
        cx.commit()
        s_a = _stress(cx, tid, "Tear film", [a_eye])
        s_b = _stress(cx, tid, "Low energy", [b_eye])
        s_adr = _stress(cx, tid, "Adrenal stress", [b_adrenal])
    return tid, dict(a_eye=a_eye, a_omega=a_omega, b_eye=b_eye, b_adrenal=b_adrenal,
                     s_a=s_a, s_b=s_b, s_adr=s_adr)


def _consolidate(db, tid, ids):
    with sqlite3.connect(db) as cx:
        st.consolidate_layer_balances(cx, tid, 5, 3,
                                      source_rids=[ids["b_eye"], ids["b_adrenal"]],
                                      target_rids=[ids["a_eye"], ids["a_omega"]])


def _merged(db, tid):
    from dashboard.biofield_report_html import group_layers
    with sqlite3.connect(db) as cx:
        return group_layers(ordered_chain(cx, tid))


def _links(db, rid):
    with sqlite3.connect(db) as cx:
        return {r[0] for r in cx.execute(
            "SELECT stress_id FROM biofield_auth_layer_stress WHERE chain_rid=?", (rid,))}


@pytest.mark.parametrize("b_name", ["Eye Support", "  eye support "])
def test_a_shared_remedy_appears_once_on_the_merged_card(tmp_path, b_name):
    db = str(tmp_path / "c.db")
    tid, ids = _two_cards(db, b_name)
    _consolidate(db, tid, ids)
    groups = _merged(db, tid)
    assert len(groups) == 1
    names = [r["remedy"].strip().lower() for r in groups[0]["rows"]]
    assert names.count("eye support") == 1, "REGRESSION: the shared remedy is duplicated"
    assert sorted(names) == ["adrenal", "eye support", "omega"]


def test_the_kept_row_is_the_targets_and_carries_both_cards_stresses(tmp_path):
    db = str(tmp_path / "c.db")
    tid, ids = _two_cards(db)
    _consolidate(db, tid, ids)
    kept = [r for r in _merged(db, tid)[0]["rows"] if r["remedy"] == "Eye Support"][0]
    assert kept["id"] == ids["a_eye"]
    assert {ids["s_a"], ids["s_b"]} <= _links(db, ids["a_eye"])
    assert _links(db, ids["b_eye"]) == set(), "a link to a deleted row is invisible"


def _field(db, rid, col):
    with sqlite3.connect(db) as cx:
        return cx.execute(f"SELECT {col} FROM biofield_auth_chain WHERE id=?", (rid,)).fetchone()[0]


def _set(db, rid, **kw):
    with sqlite3.connect(db) as cx:
        cx.execute("UPDATE biofield_auth_chain SET %s WHERE id=?" % ",".join(f"{k}=?" for k in kw),
                   (*kw.values(), rid))
        cx.commit()


def test_a_kept_row_with_no_instructions_takes_the_duplicates(tmp_path):
    db = str(tmp_path / "c.db")
    tid, ids = _two_cards(db)
    _consolidate(db, tid, ids)
    assert (_field(db, ids["a_eye"], "dosage"), _field(db, ids["a_eye"], "timing")) == \
        ("2 caps", "morning")


def test_the_instructions_never_mix_two_cards(tmp_path):
    """Review round 1: a blank dose beside a set frequency read "2 caps, 3x daily"."""
    db = str(tmp_path / "c.db")
    tid, ids = _two_cards(db)
    _set(db, ids["a_eye"], frequency="3x daily")
    _consolidate(db, tid, ids)
    assert _field(db, ids["a_eye"], "dosage") in ("", None)
    assert _field(db, ids["a_eye"], "timing") in ("", None)
    assert _field(db, ids["a_eye"], "frequency") == "3x daily"


@pytest.mark.parametrize("kept,dup,want", [(None, 3, 3), (2, None, 2), (4, 3, 4), (None, None, None)])
def test_the_bill_keeps_the_larger_bottle_count(tmp_path, kept, dup, want):
    db = str(tmp_path / "c.db")
    tid, ids = _two_cards(db)
    _set(db, ids["a_eye"], bottles=kept)
    _set(db, ids["b_eye"], bottles=dup)
    _consolidate(db, tid, ids)
    assert _field(db, ids["a_eye"], "bottles") == want


def test_a_confirmed_duplicate_keeps_the_row_confirmed(tmp_path):
    db = str(tmp_path / "c.db")
    tid, ids = _two_cards(db)
    _set(db, ids["a_eye"], confirmed=0)
    _set(db, ids["b_eye"], confirmed=1)
    _consolidate(db, tid, ids)
    assert _field(db, ids["a_eye"], "confirmed") == 1


def test_the_duplicates_stress_codes_are_kept(tmp_path):
    import json
    db = str(tmp_path / "c.db")
    tid, ids = _two_cards(db)
    _set(db, ids["a_eye"], codes=json.dumps(["ED1"]))
    _set(db, ids["b_eye"], codes=json.dumps(["ED9", "ED1"]))
    _consolidate(db, tid, ids)
    assert json.loads(_field(db, ids["a_eye"], "codes")) == ["ED1", "ED9"]


def test_a_remedy_only_on_one_card_still_moves(tmp_path):
    db = str(tmp_path / "c.db")
    tid, ids = _two_cards(db)
    _consolidate(db, tid, ids)
    with sqlite3.connect(db) as cx:
        layer = cx.execute("SELECT layer FROM biofield_auth_chain WHERE id=?",
                           (ids["b_adrenal"],)).fetchone()[0]
    assert layer == 3
    assert ids["s_adr"] in _links(db, ids["a_eye"]) | _links(db, ids["b_adrenal"])


def test_cards_with_no_common_remedy_keep_every_row(tmp_path):
    db = str(tmp_path / "c.db")
    tid, ids = _two_cards(db, b_name="Liver Support")
    _consolidate(db, tid, ids)
    assert len(_merged(db, tid)[0]["rows"]) == 4


def test_two_rows_of_one_remedy_on_the_source_card_alone_both_move(tmp_path):
    """Review round 2: only a remedy on both cards folds."""
    db = str(tmp_path / "c.db")
    tid, ids = _two_cards(db, b_name="Liver Support")
    with sqlite3.connect(db) as cx:
        extra = _row(cx, tid, 5, "Fatigue", "Liver Support")
    with sqlite3.connect(db) as cx:
        st.consolidate_layer_balances(cx, tid, 5, 3,
                                      source_rids=[ids["b_eye"], ids["b_adrenal"], extra],
                                      target_rids=[ids["a_eye"], ids["a_omega"]])
    names = [r["remedy"] for r in _merged(db, tid)[0]["rows"]]
    assert names.count("Liver Support") == 2


def test_a_second_dose_on_the_source_card_survives_when_the_target_has_the_remedy(tmp_path):
    """Review round 3: morning and bedtime rows both folded into the one target row."""
    db = str(tmp_path / "c.db")
    tid, ids = _two_cards(db)
    with sqlite3.connect(db) as cx:
        bedtime = _row(cx, tid, 5, "Fatigue", "Eye Support")
    _set(db, bedtime, dosage="2 caps", timing="bedtime")
    with sqlite3.connect(db) as cx:
        st.consolidate_layer_balances(cx, tid, 5, 3,
                                      source_rids=[ids["b_eye"], ids["b_adrenal"], bedtime],
                                      target_rids=[ids["a_eye"], ids["a_omega"]])
    rows = [r for r in _merged(db, tid)[0]["rows"] if r["remedy"] == "Eye Support"]
    assert sorted(r["id"] for r in rows) == sorted([ids["a_eye"], bedtime])
    assert _field(db, bedtime, "timing") == "bedtime"


@pytest.mark.parametrize("blank", ["   ", "[]"])
def test_spaces_or_an_emptied_schedule_count_as_no_instructions(tmp_path, blank):
    db = str(tmp_path / "c.db")
    tid, ids = _two_cards(db)
    _set(db, ids["a_eye"], dosage="   ", schedule_slot=blank)
    _consolidate(db, tid, ids)
    assert _field(db, ids["a_eye"], "dosage") == "2 caps"


def test_codes_that_are_not_json_are_kept(tmp_path):
    import json
    db = str(tmp_path / "c.db")
    tid, ids = _two_cards(db)
    _set(db, ids["a_eye"], codes="ED1, ED2")
    _set(db, ids["b_eye"], codes=json.dumps(["ED9"]))
    _consolidate(db, tid, ids)
    assert json.loads(_field(db, ids["a_eye"], "codes")) == ["ED1", "ED2", "ED9"]
