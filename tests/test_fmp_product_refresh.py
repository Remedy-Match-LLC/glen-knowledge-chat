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


def test_missing_table_and_client_tables_in_the_export_are_refused(db, tmp_path):
    d = _export(tmp_path / "new", NEW)
    (d / "products_items.csv").unlink()
    with pytest.raises(RefreshRefused, match="products_items"):
        refresh_products(d, db)
    d2 = _export(tmp_path / "new2", NEW)
    (d2 / "client_remedy.csv").write_text("id,remedy\n", encoding="utf-8")
    with pytest.raises(RefreshRefused, match="non-product"):
        refresh_products(d2, db)
    with sqlite3.connect(db) as cx:
        assert cx.execute("SELECT COUNT(*) FROM fmp_snap_client_remedy").fetchone() == (1,)


def test_duplicate_or_blank_ids_are_refused(db, tmp_path):
    with pytest.raises(RefreshRefused, match="duplicate"):
        refresh_products(_export(tmp_path / "a", NEW + [_product("10", "Twin")]), db)
    with pytest.raises(RefreshRefused, match="no id_pk"):
        refresh_products(_export(tmp_path / "b", NEW + [_product("", "Orphan")]), db)


def test_a_rename_whose_old_name_is_still_carried_is_not_moved(db, tmp_path):
    # another product now carries "Vitamin P Polyphenols", so the stored name still resolves
    new = NEW + [_product("2000", "Vitamin P Polyphenols")]
    out = refresh_products(_export(tmp_path / "new", new), db)
    assert not any(pk == "374" for pk, _, _ in out["renamed"])
    assert _chain(db)[38][0] == "Vitamin P Polyphenols"


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
