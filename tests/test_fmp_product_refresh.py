"""The product refresher: re-reads FileMaker's product tables and carries renames into
stored chain rows by id_pk (Glen 2026-10-02, "refresh the product list first")."""
import csv
import sqlite3

import pytest

from dashboard import biofield_fmp_snapshot as snap
from dashboard.biofield_authoring import remedy_dosing
from dashboard.biofield_fmp_snapshot import RefreshRefused, refresh_products, snapshot_csv_dir

COLS = list(snap.REQUIRED_PRODUCT_COLS)


def _product(pk, name, dosage="1 capsule", freq="daily", timing="with food", dpb="30"):
    return {"id_pk": pk, "product_name": name, "active": "Yes", "type": "Functional Formulation",
            "dosage": dosage, "dosage_freq": freq, "dosage_timing": timing,
            "doses_per_bottle": dpb}


def _export(d, products):
    d.mkdir(parents=True, exist_ok=True)
    with (d / "products.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLS)
        w.writeheader()
        w.writerows(products)
    for t in ("products_items", "products_phases", "products_systems"):
        (d / f"{t}.csv").write_text("id_pk,name\n1,x\n", encoding="utf-8")
    return d


OLD = [_product("374", "Vitamin P Polyphenols*", dpb="30"),
       _product("10", "Sterol Max"),
       _product("11", "Perlandra Essence", dosage="3 drops")]
NEW = [_product("374", "Vascular Integrity: Vitamin P Plus TECA", dpb="60"),
       _product("10", "Sterol Max"),
       _product("11", "Perlandra Rose Essence", dosage="3 drops"),
       _product("1199", "GERD Guard Powder")]


@pytest.fixture
def db(tmp_path):
    path = str(tmp_path / "chat_log.db")
    snapshot_csv_dir(_export(tmp_path / "old", OLD), path)
    with sqlite3.connect(path) as cx:
        cx.execute("CREATE TABLE fmp_snap_client_remedy (id TEXT, remedy TEXT)")
        cx.execute("INSERT INTO fmp_snap_client_remedy VALUES ('1','Vitamin P Polyphenols*')")
        cx.execute("CREATE TABLE biofield_auth_chain (id INTEGER PRIMARY KEY, test_id INT, "
                   "remedy TEXT, dosage TEXT, frequency TEXT, timing TEXT, bottles INT)")
        cx.executemany("INSERT INTO biofield_auth_chain VALUES (?,?,?,?,?,?,?)", [
            (38, 9, "Vitamin P Polyphenols", "1 capsule", "twice a day", "with food", 2),
            (39, 9, "Perlandra Essence in Terrain Restore", "3 drops", "daily", "", 1),
            (40, 9, "Sterol Max", "2 capsules", "daily", "", 1)])
        cx.execute("CREATE TABLE biofield_auth_remedy_coverage (id INTEGER PRIMARY KEY, "
                   "test_id INT, remedy TEXT, code TEXT, UNIQUE(test_id, remedy, code))")
        cx.executemany("INSERT INTO biofield_auth_remedy_coverage VALUES (?,?,?,?)", [
            (1, 9, "vitamin p polyphenols", "ED1"),
            (2, 9, "vitamin p polyphenols", "ED2"),
            (3, 9, "vascular integrity: vitamin p plus teca", "ED2")])
    return path


def _chain(path):
    with sqlite3.connect(path) as cx:
        return {r[0]: r[1:] for r in cx.execute(
            "SELECT id, remedy, dosage, frequency, timing, bottles FROM biofield_auth_chain")}


def _tables(path):
    with sqlite3.connect(path) as cx:
        return {r[0] for r in cx.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def test_vitamin_p_row_becomes_vascular_integrity_and_keeps_its_dose_and_bottles(db, tmp_path):
    out = refresh_products(_export(tmp_path / "new", NEW), db)
    row = _chain(db)[38]
    assert row == ("Vascular Integrity: Vitamin P Plus TECA", "1 capsule", "twice a day",
                   "with food", 2)
    assert ("374", "Vitamin P Polyphenols", "Vascular Integrity: Vitamin P Plus TECA") in out["renamed"]
    # the renamed row still resolves to a FileMaker product for dosing
    with sqlite3.connect(db) as cx:
        assert remedy_dosing(cx, row[0])["dosage"] == "1 capsule"
        assert tuple(cx.execute("SELECT doses_per_bottle FROM fmp_snap_products "
                                "WHERE id_pk='374'").fetchone()) == ("60",)
    with sqlite3.connect(db) as cx:
        # coverage keeps its lowercase form, and a row already held under the new name
        # is not duplicated
        assert cx.execute("SELECT remedy, code FROM biofield_auth_remedy_coverage "
                          "ORDER BY code").fetchall() == [
            ("vascular integrity: vitamin p plus teca", "ED1"),
            ("vascular integrity: vitamin p plus teca", "ED2")]


def test_terrain_restore_suffix_survives_a_rename(db, tmp_path):
    refresh_products(_export(tmp_path / "new", NEW), db)
    assert _chain(db)[39][0] == "Perlandra Rose Essence in Terrain Restore"
    assert _chain(db)[40][0] == "Sterol Max"


def test_new_products_reach_the_picker_and_client_tables_are_untouched(db, tmp_path):
    out = refresh_products(_export(tmp_path / "new", NEW), db)
    assert out["added"] == ["GERD Guard Powder"]
    with sqlite3.connect(db) as cx:
        assert cx.execute("SELECT COUNT(*) FROM fmp_snap_products").fetchone() == (4,)
        assert cx.execute("SELECT remedy FROM fmp_snap_client_remedy").fetchall() == [
            ("Vitamin P Polyphenols*",)]
    assert not any(t.startswith(snap.STAGE_PREFIX) for t in _tables(db))
    assert "fmp_prev_products" in _tables(db)


def test_dry_run_changes_nothing(db, tmp_path):
    before = _chain(db)
    out = refresh_products(_export(tmp_path / "new", NEW), db, apply=False)
    assert out["renamed"] and out["rewritten"] == []
    assert _chain(db) == before
    with sqlite3.connect(db) as cx:
        assert cx.execute("SELECT COUNT(*) FROM fmp_snap_products").fetchone() == (3,)
    assert not any(t.startswith(snap.STAGE_PREFIX) for t in _tables(db))


def test_a_shrunken_export_is_refused_and_the_copy_kept(db, tmp_path):
    with pytest.raises(RefreshRefused, match="fell from"):
        refresh_products(_export(tmp_path / "new", NEW[:1]), db)
    with sqlite3.connect(db) as cx:
        assert cx.execute("SELECT COUNT(*) FROM fmp_snap_products").fetchone() == (3,)
    assert _chain(db)[38][0] == "Vitamin P Polyphenols"


def test_a_missing_table_is_refused_and_a_client_csv_beside_it_is_never_loaded(db, tmp_path):
    d = _export(tmp_path / "new", NEW)
    (d / "products_items.csv").unlink()
    with pytest.raises(RefreshRefused, match="products_items"):
        refresh_products(d, db)
    d2 = _export(tmp_path / "new2", NEW)
    (d2 / "client_remedy.csv").write_text("id,remedy\n", encoding="utf-8")
    (d2 / "renamed-rows.csv").write_text("table,rowid,old,new\n", encoding="utf-8")
    refresh_products(d2, db)
    with sqlite3.connect(db) as cx:
        assert cx.execute("SELECT COUNT(*) FROM fmp_snap_client_remedy").fetchone() == (1,)
    assert "fmp_snap_renamed_rows" not in _tables(db)


def test_a_shrunken_side_table_is_refused(db, tmp_path):
    d = _export(tmp_path / "old2", OLD)
    (d / "products_items.csv").write_text("id_pk,name\n" + "".join(
        f"{i},x\n" for i in range(1, 21)), encoding="utf-8")
    refresh_products(d, db)
    d2 = _export(tmp_path / "new", NEW)          # products_items back to one row
    with pytest.raises(RefreshRefused, match="products_items fell"):
        refresh_products(d2, db)


def test_a_product_gone_from_filemaker_needs_confirmation(db, tmp_path):
    keep = [r for r in NEW if r["id_pk"] != "10"] + [_product(str(i), f"Extra {i}")
                                                      for i in range(50, 60)]
    d = _export(tmp_path / "new", keep)
    with pytest.raises(RefreshRefused, match="no longer in FileMaker"):
        refresh_products(d, db)
    out = refresh_products(d, db, allow_removed=True)
    assert out["removed"] == ["Sterol Max"]


def test_products_sharing_a_name_renamed_apart_are_refused(tmp_path):
    path = str(tmp_path / "db")
    snapshot_csv_dir(_export(tmp_path / "old", [_product("1", "Twin"), _product("2", "Twin")]), path)
    with pytest.raises(RefreshRefused, match="renamed apart"):
        refresh_products(_export(tmp_path / "new", [_product("1", "Twin A"),
                                                    _product("2", "Twin B")]), path)


def test_a_non_duplicate_integrity_error_stops_the_run_and_deletes_nothing(db, tmp_path):
    with sqlite3.connect(db) as cx:
        cx.execute("CREATE UNIQUE INDEX one_remedy_per_test ON biofield_auth_chain(test_id, remedy)")
        cx.execute("INSERT INTO biofield_auth_chain VALUES (41, 9, 'Vascular Integrity: Vitamin P "
                   "Plus TECA', '2 capsules', 'daily', '', 1)")
    with pytest.raises(sqlite3.IntegrityError):
        refresh_products(_export(tmp_path / "new", NEW), db)
    assert _chain(db)[38][0] == "Vitamin P Polyphenols"
    assert 41 in _chain(db)


def test_duplicate_or_blank_ids_are_refused(db, tmp_path):
    with pytest.raises(RefreshRefused, match="duplicate"):
        refresh_products(_export(tmp_path / "a", NEW + [_product("10", "Twin")]), db)
    with pytest.raises(RefreshRefused, match="no id_pk"):
        refresh_products(_export(tmp_path / "b", NEW + [_product("", "Orphan")]), db)


def test_an_old_name_taken_by_another_product_is_refused(db, tmp_path):
    # id 374 is renamed and id 2000 takes its old name: the stored row would silently
    # start meaning a different product, so the whole refresh stops
    new = NEW + [_product("2000", "Vitamin P Polyphenols")]
    with pytest.raises(RefreshRefused, match="carried by another product"):
        refresh_products(_export(tmp_path / "new", new), db)
    assert _chain(db)[38][0] == "Vitamin P Polyphenols"


def test_a_shared_name_kept_by_its_other_holder_is_left_alone(tmp_path):
    path = str(tmp_path / "db")
    snapshot_csv_dir(_export(tmp_path / "old", [_product("1", "Healing Glaucoma eBook"),
                                                _product("2", "Healing Glaucoma eBook")]), path)
    out = refresh_products(_export(tmp_path / "new", [_product("1", "Healing Glaucoma eBook 2"),
                                                      _product("2", "Healing Glaucoma eBook")]), path)
    assert out["renamed"] == []


def test_a_failure_mid_swap_leaves_the_old_copy(db, tmp_path, monkeypatch):
    def boom(cx, renames):
        raise RuntimeError("disk full")
    monkeypatch.setattr(snap, "_rewrite_stored_names", boom)
    with pytest.raises(RuntimeError):
        refresh_products(_export(tmp_path / "new", NEW), db)
    with sqlite3.connect(db) as cx:
        assert cx.execute("SELECT product_name FROM fmp_snap_products WHERE id_pk='374'"
                          ).fetchone() == ("Vitamin P Polyphenols*",)
    assert _chain(db)[38][0] == "Vitamin P Polyphenols"
    assert not any(t.startswith(snap.STAGE_PREFIX) for t in _tables(db))


def _add_lists_and_checklist(path):
    from dashboard.biofield_clinical_checklist import ensure_catalog_schema
    with sqlite3.connect(path) as cx:
        cx.execute("CREATE TABLE biofield_auth_remedy_set (test_id INTEGER PRIMARY KEY, "
                   "remedies_json TEXT, updated_at TEXT)")
        cx.execute("INSERT INTO biofield_auth_remedy_set VALUES (9, ?, '')", (
            '["vitamin p polyphenols", "sterol max", "vascular integrity: vitamin p plus teca"]',))
        ensure_catalog_schema(cx)
        cx.execute("INSERT INTO biofield_clinical_catalog(item_key,label,remedy_key,remedy) "
                   "VALUES ('wet amd','Wet AMD','vitamin p polyphenols','Vitamin P Polyphenols')")


def test_saved_remedy_sets_and_the_checklist_follow_the_rename(db, tmp_path):
    import json
    _add_lists_and_checklist(db)
    refresh_products(_export(tmp_path / "new", NEW), db)
    with sqlite3.connect(db) as cx:
        names = json.loads(cx.execute("SELECT remedies_json FROM biofield_auth_remedy_set"
                                      ).fetchone()[0])
        assert names == ["vascular integrity: vitamin p plus teca", "sterol max"]
        assert cx.execute("SELECT remedy_key, remedy FROM biofield_clinical_catalog").fetchall() == [
            ("vascular integrity vitamin p plus teca", "Vascular Integrity: Vitamin P Plus TECA")]


def test_a_checklist_holding_both_names_differently_shown_is_refused(db, tmp_path):
    _add_lists_and_checklist(db)
    with sqlite3.connect(db) as cx:
        cx.execute("INSERT INTO biofield_clinical_catalog(item_key,label,remedy_key,remedy,hidden) "
                   "VALUES ('wet amd','Wet AMD','vascular integrity vitamin p plus teca',"
                   "'Vascular Integrity: Vitamin P Plus TECA',1)")
    with pytest.raises(RefreshRefused, match="Glen decides"):
        refresh_products(_export(tmp_path / "new", NEW), db)
    assert _chain(db)[38][0] == "Vitamin P Polyphenols"


def test_an_export_that_lost_a_column_is_refused(db, tmp_path):
    d = _export(tmp_path / "new", NEW)
    (d / "products_phases.csv").write_text("id_pk\n1\n", encoding="utf-8")
    with pytest.raises(RefreshRefused, match="lacks columns"):
        refresh_products(d, db)


def test_a_rename_onto_a_name_another_product_carries_is_refused(db, tmp_path):
    # 374 renamed onto Sterol Max's name: its rows would merge with Sterol Max's
    new = [_product("374", "Sterol Max")] + NEW[1:]
    with pytest.raises(RefreshRefused, match="also carries"):
        refresh_products(_export(tmp_path / "a", new), db)
    # two products renamed onto one new name
    new = NEW[:1] + [NEW[1], _product("11", "Vascular Integrity: Vitamin P Plus TECA"), NEW[3]]
    with pytest.raises(RefreshRefused, match="also carries"):
        refresh_products(_export(tmp_path / "b", new), db)
    assert _chain(db)[38][0] == "Vitamin P Polyphenols"


def test_saved_patterns_follow_the_rename(db, tmp_path):
    import json
    with sqlite3.connect(db) as cx:
        cx.execute("CREATE TABLE biofield_remedy_pattern (pattern_key TEXT PRIMARY KEY, "
                   "tokens_json TEXT, remedies_json TEXT, label TEXT, updated_at TEXT)")
        cx.execute("INSERT INTO biofield_remedy_pattern VALUES ('k','[]',?, '', '')",
                   ('["vitamin p polyphenols", "bad"]',))
        cx.execute("CREATE TABLE biofield_auth_remedy_set (test_id INTEGER PRIMARY KEY, "
                   "remedies_json TEXT, updated_at TEXT)")
        cx.execute("INSERT INTO biofield_auth_remedy_set VALUES (1, 'not json', '')")
        cx.execute("INSERT INTO biofield_auth_remedy_set VALUES (2, '{\"a\": 1}', '')")
    refresh_products(_export(tmp_path / "new", NEW), db)
    with sqlite3.connect(db) as cx:
        assert json.loads(cx.execute("SELECT remedies_json FROM biofield_remedy_pattern"
                                     ).fetchone()[0]) == ["vascular integrity: vitamin p plus teca", "bad"]
        assert [r[0] for r in cx.execute("SELECT remedies_json FROM biofield_auth_remedy_set "
                                         "ORDER BY test_id")] == ["not json", '{"a": 1}']


def test_an_export_without_dosing_columns_is_refused_on_a_first_load(tmp_path):
    d = tmp_path / "x"
    d.mkdir()
    (d / "products.csv").write_text("id_pk,product_name\n1,A\n", encoding="utf-8")
    for t in ("products_items", "products_phases", "products_systems"):
        (d / f"{t}.csv").write_text("id_pk,name\n1,x\n", encoding="utf-8")
    with pytest.raises(RefreshRefused, match="lacks columns"):
        refresh_products(d, str(tmp_path / "db"))


def test_duplicate_item_ids_are_refused(db, tmp_path):
    d = _export(tmp_path / "new", NEW)
    (d / "products_items.csv").write_text("id_pk,name\n1,x\n1,y\n", encoding="utf-8")
    with pytest.raises(RefreshRefused, match="products_items has 1 duplicate"):
        refresh_products(d, db)


def test_a_client_csv_beside_the_export_leaves_no_table_behind(db, tmp_path):
    d = _export(tmp_path / "new", NEW)
    (d / "client_remedy.csv").write_text("id,remedy\n1,x\n", encoding="utf-8")
    refresh_products(d, db)
    assert not [t for t in _tables(db) if t.startswith("fmp_stage") or "client_remedy" in t
                and t != "fmp_snap_client_remedy"]


def test_a_rename_to_a_blank_name_moves_nothing(db, tmp_path):
    new = [_product("374", "")] + NEW[1:]
    out = refresh_products(_export(tmp_path / "new", new), db)
    assert not any(pk == "374" for pk, _, _ in out["renamed"])
    assert _chain(db)[38][0] == "Vitamin P Polyphenols"
