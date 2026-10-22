"""A remedy belongs on its earliest layer. Glen, 2026-09-24, Alyssa Fukushima (a40):
layer 11 was balanced by Nous Energy, already on layer 8, and should fold into it."""
import sqlite3

from biofield_local_app import create_app
from dashboard.biofield_authoring import add_chain_row, create_test, init_auth_tables
from dashboard.biofield_chain_fold import duplicate_folds, merged_tail


def _app(tmp_path, monkeypatch):
    monkeypatch.delenv("CONSOLE_SECRET", raising=False)
    import dashboard
    monkeypatch.setattr(dashboard, "CONSOLE_SECRET", "", raising=False)
    db = str(tmp_path / "chat_log.db")
    with sqlite3.connect(db) as cx:
        init_auth_tables(cx)
        tid = create_test(cx, "Alyssa", "a@example.com", "2026-09-24")
    app = create_app(db, fetch_profile=lambda email: {}, fetch_recent_comms=lambda email: {})
    return db, tid, app.test_client()


def _alyssa(db, tid, neuro_confirmed=0):
    with sqlite3.connect(db) as cx:
        add_chain_row(cx, tid, 1, "Stress", "Stress", "Stress Release", confirmed=0,
                      origin="clinical")
        add_chain_row(cx, tid, 1, "Stress", "Stress", "Nous Energy", confirmed=0,
                      origin="clinical")
        add_chain_row(cx, tid, 2, "Neurological Optimization",
                      "Nerve Terrain, Audio Star, stress", "Nous Energy",
                      confirmed=neuro_confirmed, origin="scan")
        add_chain_row(cx, tid, 3, "Liver", "Liver Driver", "Liver Support", confirmed=0,
                      origin="scan")


def _chain(db, tid):
    with sqlite3.connect(db) as cx:
        return cx.execute("SELECT head, most_affected, remedy FROM biofield_auth_chain "
                          "WHERE test_id=? ORDER BY layer, id",
                          (int(str(tid).lstrip("a")),)).fetchall()


def _write(client, tid):
    """Any successful write to the intake. Saving an empty selection is harmless."""
    r = client.post(f"/author/{tid}/clinical-items/selection",
                    json={"label": "Nothing", "remedies": []})
    assert r.status_code == 200


def _g(n, head, tail, *remedies, confirmed=0):
    return {"layer": n, "head": head, "most_affected": tail,
            "rows": [{"id": n * 10 + i, "remedy": r, "confirmed": confirmed}
                     for i, r in enumerate(remedies)]}


def test_a_layer_whose_only_remedy_is_earlier_folds_whole():
    got = duplicate_folds([_g(1, "Stress", "Stress", "Stress Release", "Nous Energy"),
                           _g(2, "Neuro", "Nerve Terrain", "Nous Energy")])
    assert len(got) == 1
    f = got[0]
    assert (f["layer"], f["into"], f["whole"]) == (2, 1, True)
    assert f["remedies"] == ["Nous Energy"] and f["rids"] == [20]


def test_a_layer_with_its_own_remedies_loses_only_the_repeat():
    got = duplicate_folds([_g(1, "Stress", "Stress", "Nous Energy"),
                           _g(2, "Neuro", "Nerve Terrain", "nous energy", "Neuroprotect")])
    assert got[0]["whole"] is False and got[0]["rids"] == [20]


def test_the_tail_merge_keeps_order_and_drops_repeats():
    assert merged_tail("Stress", "Nerve Terrain, stress, Audio Star") == \
        "Stress, Nerve Terrain, Audio Star"


def test_a_proposed_repeat_folds_on_the_next_write(tmp_path, monkeypatch):
    db, tid, client = _app(tmp_path, monkeypatch)
    _alyssa(db, tid)
    _write(client, tid)
    rows = _chain(db, tid)
    assert [r[2] for r in rows] == ["Stress Release", "Nous Energy", "Liver Support"]
    assert rows[0][1] == "Stress, Nerve Terrain, Audio Star"
    assert rows[1][1] == "Stress, Nerve Terrain, Audio Star"


def test_a_confirmed_repeat_is_proposed_not_folded(tmp_path, monkeypatch):
    db, tid, client = _app(tmp_path, monkeypatch)
    _alyssa(db, tid, neuro_confirmed=1)
    _write(client, tid)
    assert len(_chain(db, tid)) == 4, "a confirmed layer was folded without approval"
    page = client.get(f"/author/{tid}").get_data(as_text=True)
    assert "id=foldnotice" in page and "Fold into layer 1" in page


def test_approving_the_fold_applies_it(tmp_path, monkeypatch):
    db, tid, client = _app(tmp_path, monkeypatch)
    _alyssa(db, tid, neuro_confirmed=1)
    with sqlite3.connect(db) as cx:
        rid = cx.execute("SELECT id FROM biofield_auth_chain WHERE head LIKE 'Neuro%'"
                         ).fetchone()[0]
    j = client.post(f"/author/{tid}/fold-duplicate",
                    json={"layer": 2, "rids": [rid]}).get_json()
    assert j["ok"] and (j["folded"], j["into"]) == (2, 1)
    assert [r[2] for r in _chain(db, tid)] == ["Stress Release", "Nous Energy",
                                              "Liver Support"]


def test_a_stale_tab_cannot_fold_other_rows(tmp_path, monkeypatch):
    db, tid, client = _app(tmp_path, monkeypatch)
    _alyssa(db, tid, neuro_confirmed=1)
    r = client.post(f"/author/{tid}/fold-duplicate", json={"layer": 2, "rids": [999]})
    assert r.status_code == 409
    assert len(_chain(db, tid)) == 4


def test_a_remedy_named_two_ways_is_one_remedy(monkeypatch):
    """Glen, 2026-10-22: "When folding layers together, do not duplicate an identical
    remedy." a45 carried ES1 under two names, so it was never folded."""
    from dashboard import biofield_authoring as A
    monkeypatch.setattr(A, "_catalog_alias_map", lambda: {
        "es1 immune": "ES1 Lymph and Immune Energetic Star Infoceutical"})
    got = duplicate_folds([_g(1, "Immune", "Immune", "ES1 Lymph and Immune Energetic Star Infoceutical"),
                           _g(2, "Lymph", "Lymph", "ES1 Immune")])
    assert len(got) == 1 and got[0]["whole"] is True and got[0]["rids"] == [20]


def test_a_remedy_twice_on_one_layer_keeps_the_first_row():
    got = duplicate_folds([_g(1, "Stress", "Stress", "Nous Energy", "Stress Release", "nous energy")])
    assert len(got) == 1
    f = got[0]
    assert (f["layer"], f["into"], f["whole"]) == (1, 1, False)
    assert f["rids"] == [12]


def test_a_close_but_different_name_is_never_folded():
    got = duplicate_folds([_g(1, "Joint", "Joint", "MSM Powder"), _g(2, "Skin", "Skin", "MSM Lotion")])
    assert got == []


def test_a_same_layer_repeat_is_worded_as_a_repeat():
    from dashboard.biofield_report_html import _render_fold_notice
    html = _render_fold_notice([_g(1, "Stress", "Stress", "Nous Energy", "Nous Energy", confirmed=1)])
    assert "Layer 1 lists Nous Energy more than once. The extra rows go." in html
    assert "Remove the repeat</button>" in html and "Fold into layer 1" not in html



def test_a_repeat_of_a_confirmed_row_on_its_own_card_is_only_proposed():
    g = _g(1, "Stress", "Stress", "Nous Energy", "Nous Energy")
    g["rows"][0]["confirmed"] = 1
    [f] = duplicate_folds([g])
    assert f["confirmed"] is True and f["rids"] == [11]


def test_a_card_with_both_kinds_of_repeat_names_each():
    from dashboard.biofield_report_html import _render_fold_notice
    html = _render_fold_notice([_g(1, "A", "A", "Nous Energy"),
                                _g(2, "B", "B", "Nous Energy", "Fiber Cleanse", "Fiber Cleanse", "Fiber Cleanse")])
    assert "Layer 2 repeats Nous Energy from layer 1." in html
    assert "Layer 2 lists Fiber Cleanse more than once. The extra rows go." in html



def test_a_retired_name_redirect_is_never_a_fold(monkeypatch):
    from dashboard import biofield_authoring as a
    monkeypatch.setattr(a, "_superseded_name_map",
                        lambda: {a._norm_name("Sublingual B12 Powder"): "Vitamin B12 Sublingual Drops"})
    groups = [_g(1, "A", "A", "Sublingual B12 Powder"), _g(2, "B", "B", "Vitamin B12 Sublingual Drops")]
    assert duplicate_folds(groups) == []


def test_each_repeat_names_its_own_source_layer():
    from dashboard.biofield_report_html import _render_fold_notice
    html = _render_fold_notice([_g(1, "A", "A", "Nous Energy"), _g(2, "B", "B", "Fiber Cleanse"),
                                _g(3, "C", "C", "Nous Energy", "Fiber Cleanse", "Spike Shield")])
    assert "Layer 3 repeats Nous Energy from layer 1; Fiber Cleanse from layer 2." in html
