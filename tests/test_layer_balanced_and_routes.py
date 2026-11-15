"""Live session part 2a: the layer_balanced column, the Balanced box (layer only),
the uncovered count, POST /author/<tid>/match, client-first vocabulary, and the
confirmed value a short recording saves. Glen's rulings of 14 November 2026."""
import json
import sqlite3

from biofield_local_app import create_app
from dashboard.biofield_authoring import (add_chain_row, authored_report, create_test,
                                          init_auth_tables, set_layer_balanced)
from dashboard.biofield_stress import init_stress_tables, list_stresses
from tests.test_biofield_local_app import _seed, _species_known, _no_console_gate  # noqa: F401


def _cx(tmp_path):
    cx = sqlite3.connect(str(tmp_path / "t.db"))
    init_auth_tables(cx)
    return cx


# ---- the column ----------------------------------------------------------------

def test_layer_balanced_column_defaults_to_zero(tmp_path):
    cx = _cx(tmp_path)
    cols = {r[1]: r for r in cx.execute("PRAGMA table_info(biofield_auth_chain)")}
    assert "layer_balanced" in cols
    assert cols["layer_balanced"][2].upper() == "INTEGER" and cols["layer_balanced"][4] == "0"
    tid = create_test(cx, "J", "j@x.com", "2026-11-14")
    add_chain_row(cx, tid, 1, "Liver", "Gut", "Sobopla")
    assert [l["layer_balanced"] for l in authored_report(cx, tid)["layers"]] == [0]


def test_migration_is_idempotent_on_an_older_table(tmp_path):
    cx = sqlite3.connect(str(tmp_path / "old.db"))
    # The table as it stood before this column.
    cx.execute("""CREATE TABLE biofield_auth_chain(
        id INTEGER PRIMARY KEY AUTOINCREMENT, test_id INTEGER, layer INTEGER,
        head TEXT, most_affected TEXT, remedy TEXT, dosage TEXT, frequency TEXT,
        timing TEXT, sort_seq INTEGER, created_at TEXT, updated_at TEXT)""")
    cx.execute("INSERT INTO biofield_auth_chain(test_id,layer,head,remedy,created_at) "
               "VALUES(1,1,'Liver','Sobopla','2026-01-01')")
    cx.commit()
    init_auth_tables(cx)
    init_auth_tables(cx)
    assert cx.execute("SELECT layer_balanced FROM biofield_auth_chain").fetchone() == (0,)


# ---- balanced is the layer's, never a stress's ----------------------------------

def _two_cards(cx):
    tid = create_test(cx, "J", "j@x.com", "2026-11-14")
    a1 = add_chain_row(cx, tid, 1, "Liver", "Gut", "Sobopla")
    a2 = add_chain_row(cx, tid, 1, "Liver", "Gut", "Sulfur Syntropy")
    b1 = add_chain_row(cx, tid, 2, "Kidney", "Bladder", "TMG")
    return tid, (a1, a2), (b1,)


def _stress_snapshot(cx, tid):
    t = int(str(tid).lstrip("a"))
    return {tbl: cx.execute(f"SELECT * FROM {tbl} WHERE test_id=? ORDER BY 1,2,3", (t,)).fetchall()
            for tbl in ("biofield_auth_stress", "biofield_auth_layer_stress",
                        "biofield_auth_remedy_coverage")}


def test_balanced_marks_every_row_of_one_card_and_no_other(tmp_path):
    cx = _cx(tmp_path)
    tid, card_a, card_b = _two_cards(cx)
    assert set_layer_balanced(cx, tid, list(card_a), True) == 2
    by = {l["rid"]: l["layer_balanced"] for l in authored_report(cx, tid)["layers"]}
    assert by == {card_a[0]: 1, card_a[1]: 1, card_b[0]: 0}
    set_layer_balanced(cx, tid, list(card_a), False)
    assert all(l["layer_balanced"] == 0 for l in authored_report(cx, tid)["layers"])


def test_balanced_ignores_a_row_of_another_test(tmp_path):
    cx = _cx(tmp_path)
    tid, card_a, _ = _two_cards(cx)
    other = create_test(cx, "K", "k@x.com", "2026-11-14")
    foreign = add_chain_row(cx, other, 1, "Lung", "", "TMG")
    assert set_layer_balanced(cx, tid, [foreign, "junk", None], True) == 0
    assert authored_report(cx, other)["layers"][0]["layer_balanced"] == 0


def test_balanced_leaves_every_stress_untouched(tmp_path):
    """Ruling 3, "layer only": stresses stay marked by remedy coverage."""
    cx = _cx(tmp_path)
    init_stress_tables(cx)
    tid, card_a, card_b = _two_cards(cx)
    t = int(str(tid).lstrip("a"))
    cx.executemany("INSERT INTO biofield_auth_stress(test_id,code,label,source,balance) "
                   "VALUES(?,?,?,?,?)", [(t, "S1", "Liver", "scan", "required"),
                                         (t, "S2", "Mold", "scan", "required"),
                                         (t, "S3", "Grief", "voice", "optional")])
    s2 = cx.execute("SELECT id FROM biofield_auth_stress WHERE code='S2'").fetchone()[0]
    cx.execute("INSERT INTO biofield_auth_layer_stress VALUES(?,?,?)", (t, s2, card_b[0]))
    cx.execute("INSERT INTO biofield_auth_remedy_coverage(test_id,remedy,code) "
               "VALUES(?,?,?)", (t, "tmg", "S2"))
    cx.commit()
    rows = [{"layer": l["layer"], "head": l["head"], "remedy": l["remedy"], "rid": l["rid"]}
            for l in authored_report(cx, tid)["layers"]]
    before = _stress_snapshot(cx, tid)
    flags_before = {i["code"]: i["balanced"] for i in
                    list_stresses(cx, tid, rows)["active"] + list_stresses(cx, tid, rows)["balanced"]}
    set_layer_balanced(cx, tid, list(card_a) + list(card_b), True)
    assert _stress_snapshot(cx, tid) == before
    flags_after = {i["code"]: i["balanced"] for i in
                   list_stresses(cx, tid, rows)["active"] + list_stresses(cx, tid, rows)["balanced"]}
    assert flags_after == flags_before
    assert flags_after["S3"] is False          # Grief stays unbalanced


# ---- the routes ------------------------------------------------------------------

def _app(db, transcript=""):
    client = create_app(db, interpret_complete=lambda s, u: json.dumps({"layers": []})).test_client()
    tid = client.post("/author/new").headers["Location"].rstrip("/").rsplit("/", 1)[-1]
    return client, tid


def _db(tmp_path):
    db = str(tmp_path / "chat_log.db")
    _seed(db)
    with sqlite3.connect(db) as cx:
        cx.execute("CREATE TABLE fmp_snap_products(id_pk TEXT,product_name TEXT,dosage TEXT,"
                   "dosage_freq TEXT,dosage_timing TEXT)")
        cx.execute("INSERT INTO fmp_snap_products VALUES('1','Sulfur Syntropy','1 capsule',"
                   "'twice a day','with food, or as guided.')")
        cx.commit()
    return db


def _rows(db, tid):
    with sqlite3.connect(db) as cx:
        return authored_report(cx, tid)["layers"]


def test_match_route_answers_and_writes_nothing(tmp_path):
    db = _db(tmp_path)
    client, tid = _app(db)
    j = client.post(f"/author/{tid}/match",
                    json={"kind": "remedy", "text": "sulfur centropy 2 drops daily"}).get_json()
    assert j["ok"] and j["value"] == "Sulfur Syntropy" and j["matched"] is True
    assert j["dosing"] == {"dosage": "2 drops", "frequency": "daily",
                           "timing": "with food, or as guided."}
    j = client.post(f"/author/{tid}/match", json={"kind": "head", "text": "zzqx florp"}).get_json()
    assert j["matched"] is False and j["value"] == "Zzqx Florp"
    assert _rows(db, tid) == []
    r = client.post(f"/author/{tid}/match", json={"kind": "nope", "text": "x"})
    assert r.status_code == 400 and r.get_json()["ok"] is False


def test_new_routes_sit_behind_the_console_gate(tmp_path, monkeypatch):
    db = _db(tmp_path)
    import dashboard as _d
    monkeypatch.setenv("CONSOLE_SECRET", "s3cret-test")
    monkeypatch.setattr(_d, "CONSOLE_SECRET", "s3cret-test", raising=False)
    client = create_app(db).test_client()
    for url, body in (("/author/a1/match", {"kind": "head", "text": "x"}),
                      ("/author/a1/layer-balanced", {"rids": [1], "balanced": True})):
        assert client.post(url, json=body).status_code == 401, url


def test_layer_balanced_route_and_a_new_remedy_on_a_balanced_card(tmp_path):
    db = _db(tmp_path)
    client, tid = _app(db)
    a = client.post(f"/author/{tid}/row", json={"layer": 1, "head": "Liver", "remedy": "TMG"}).get_json()["rid"]
    b = client.post(f"/author/{tid}/row", json={"layer": 2, "head": "Kidney", "remedy": "TMG"}).get_json()["rid"]
    j = client.post(f"/author/{tid}/layer-balanced", json={"rids": [a], "balanced": True}).get_json()
    assert j == {"ok": True, "updated": 1, "balanced": True}
    # A remedy added to the ticked card joins it ticked; the other card is untouched.
    c = client.post(f"/author/{tid}/row", json={"anchor_rid": a, "head": "Liver",
                                                 "remedy": "Sobopla"}).get_json()["rid"]
    by = {l["rid"]: l["layer_balanced"] for l in _rows(db, tid)}
    assert by == {a: 1, c: 1, b: 0}
    h = client.get(f"/author/{tid}").get_data(as_text=True)
    assert "id=g0_balanced checked" in h and "id=g1_balanced " in h
    assert "id=g1_balanced checked" not in h


def test_a_short_recording_saves_confirmed_by_whether_it_matched(tmp_path):
    """Ruling 7: matched goes in confirmed, unmatched goes in for review."""
    db = _db(tmp_path)
    client, tid = _app(db)
    ok = client.post(f"/author/{tid}/row", json={"layer": 1, "head": "Liver",
                                                  "remedy": "Sulfur Syntropy",
                                                  "confirmed": 1}).get_json()["rid"]
    bad = client.post(f"/author/{tid}/row", json={"layer": 2, "head": "Zzqx",
                                                   "remedy": "Florp", "confirmed": 0}).get_json()["rid"]
    typed = client.post(f"/author/{tid}/row", json={"layer": 3, "head": "Lung",
                                                     "remedy": "TMG"}).get_json()["rid"]
    by = {l["rid"]: l["confirmed"] for l in _rows(db, tid)}
    assert by == {ok: 1, bad: 0, typed: 1}
    # Head spoken into an existing card: the row save carries confirmed.
    client.post(f"/author/{tid}/row/{ok}", json={"head": "Zzqx Florp", "most_affected": "",
                                                 "confirmed": 0})
    assert {l["rid"]: l["confirmed"] for l in _rows(db, tid)}[ok] == 0
    # A typed save without confirmed leaves it as it was.
    client.post(f"/author/{tid}/row/{bad}", json={"head": "Liver"})
    assert {l["rid"]: l["confirmed"] for l in _rows(db, tid)}[bad] == 0


def test_vocab_lists_this_clients_stresses_first(tmp_path):
    """Ruling 6, "client first". Without test_id, /api/vocab answers as before."""
    db = _db(tmp_path)
    client, tid = _app(db)
    with sqlite3.connect(db) as cx:
        init_stress_tables(cx)
        # _seed's FileMaker table has no main_stress column, which stress_vocab reads.
        cx.execute("ALTER TABLE fmp_snap_client_active_main_stress ADD COLUMN main_stress TEXT")
        cx.execute("CREATE TABLE custom_stress_vocab(term TEXT)")
        cx.executemany("INSERT INTO custom_stress_vocab VALUES(?)",
                       [("Acid",), ("Bacteria",), ("Zinc Deficiency",)])
        t = int(tid.lstrip("a"))
        cx.executemany("INSERT INTO biofield_auth_stress(test_id,code,label,source,balance) "
                       "VALUES(?,?,?,?,?)", [(t, "S1", "Zinc Deficiency", "scan", "required"),
                                             (t, "S2", "Mold", "voice", "optional")])
        cx.commit()
    plain = client.get("/api/vocab?limit=50").get_json()
    assert plain == {"vocab": ["Acid", "Bacteria", "Zinc Deficiency"]}
    j = client.get(f"/api/vocab?limit=50&test_id={tid}").get_json()
    assert j["client"] == ["Zinc Deficiency", "Mold"]
    assert j["vocab"] == ["Zinc Deficiency", "Mold", "Acid", "Bacteria"]


def test_page_shows_the_uncovered_count(tmp_path):
    """Ruling 8: a count of detected stresses no layer covers yet. It never blocks."""
    db = _db(tmp_path)
    client, tid = _app(db)
    client.post(f"/author/{tid}/row", json={"layer": 1, "head": "Liver", "remedy": "TMG"})
    with sqlite3.connect(db) as cx:
        init_stress_tables(cx)
        t = int(tid.lstrip("a"))
        cx.executemany("INSERT INTO biofield_auth_stress(test_id,code,label,source,balance) "
                       "VALUES(?,?,?,?,?)", [(t, "S1", "Liver", "scan", "required"),
                                             (t, "S2", "Mold", "scan", "required"),
                                             (t, "S3", "Grief", "voice", "optional")])
        cx.commit()
    h = client.get(f"/author/{tid}").get_data(as_text=True)
    assert "<p id=uncoveredCount class=food data-n=2>2 detected stresses not covered by " \
           "any layer yet</p>" in h
    # Every one covered: the count says 0, and nothing is disabled.
    client.post(f"/author/{tid}/row", json={"layer": 2, "head": "Mold", "remedy": "TMG"})
    client.post(f"/author/{tid}/row", json={"layer": 3, "head": "Grief", "remedy": "TMG"})
    h = client.get(f"/author/{tid}").get_data(as_text=True)
    assert "data-n=0>0 detected stresses not covered" in h
