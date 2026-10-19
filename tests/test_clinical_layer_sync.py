"""Update Clinical layers (Glen, 2026-10-18 and 19). See biofield_clinical_layer_sync."""
import sqlite3

from biofield_local_app import create_app
from dashboard import biofield_clinical_sections as cs
from dashboard.biofield_authoring import add_chain_row, create_test, init_auth_tables
from dashboard.biofield_clinical_checklist import set_display_label
from dashboard.biofield_clinical_proposals import save_pattern, save_selection

URL = "/author/{}/clinical-items/layers/update"


def _topic(cx, label):
    """A Clinical Summary topic: a condition that holds section data."""
    cs.upsert_item(cx, label, cs.NUTRIENTS, {"name": "Vitamin C"})


def _app(tmp_path, monkeypatch, conditions, setup):
    monkeypatch.delenv("CONSOLE_SECRET", raising=False)
    import dashboard
    monkeypatch.setattr(dashboard, "CONSOLE_SECRET", "", raising=False)
    db = str(tmp_path / "chat_log.db")
    with sqlite3.connect(db) as cx:
        init_auth_tables(cx)
        tid = create_test(cx, "Josie", "j@example.com", "2026-10-19")
        setup(cx, tid)
        cx.commit()
    app = create_app(db, fetch_profile=lambda email: {"conditions": conditions},
                     fetch_recent_comms=lambda email: {})
    return db, tid, app.test_client()


def _chain(db, tid):
    with sqlite3.connect(db) as cx:
        return cx.execute("SELECT layer,head,most_affected,remedy,confirmed,origin "
                          "FROM biofield_auth_chain WHERE test_id=? ORDER BY layer,id",
                          (int(str(tid).lstrip("a")),)).fetchall()


def _preview(client, tid):
    j = client.post(URL.format(tid), json={}).get_json()
    assert j["ok"] and j["proposed"]
    return j


def _apply(client, tid, j, chosen=None):
    chosen = [c["id"] for c in j["changes"] if c["preticked"]] if chosen is None else chosen
    return client.post(URL.format(tid), json={"apply": True, "digest": j["digest"],
                                              "chosen": chosen})


def _gut_by_hand(cx, tid, confirmed=1):
    """Josie's layers were typed in by hand: origin live, a head row with no remedy."""
    _topic(cx, "Gut")
    add_chain_row(cx, tid, 1, "Gut", "Gut", "", confirmed=confirmed)
    add_chain_row(cx, tid, 1, "Gut", "", "Microbiome", confirmed=confirmed)
    add_chain_row(cx, tid, 1, "Gut", "", "Old Remedy", confirmed=confirmed)
    save_selection(cx, tid, "Gut", ["Microbiome", "Transform"])


def test_a_hand_entered_layer_is_matched_by_its_topic_name(tmp_path, monkeypatch):
    """Glen, 2026-10-19: "yes" to both. No row of hers has a stress pattern."""
    db, tid, client = _app(tmp_path, monkeypatch, ["Gut"], _gut_by_hand)
    before = _chain(db, tid)
    j = _preview(client, tid)
    kinds = {(c["kind"], c.get("remedy")) for c in j["changes"]}
    assert kinds == {("add", "Transform"), ("remove", "Old Remedy")}
    assert not any(c["kind"] == "new" for c in j["changes"]), "Gut was added a second time"
    assert _chain(db, tid) == before, "the preview wrote"


def test_a_confirmed_layer_changes_only_by_tick(tmp_path, monkeypatch):
    db, tid, client = _app(tmp_path, monkeypatch, ["Gut"], _gut_by_hand)
    j = _preview(client, tid)
    assert not any(c["preticked"] for c in j["changes"])
    assert all(c["confirmed_layer"] for c in j["changes"])


def test_a_draft_layer_pre_ticks_additions_but_never_removals(tmp_path, monkeypatch):
    db, tid, client = _app(tmp_path, monkeypatch, ["Gut"],
                           lambda cx, tid: _gut_by_hand(cx, tid, confirmed=0))
    j = _preview(client, tid)
    pre = {c["kind"]: c["preticked"] for c in j["changes"]}
    assert pre == {"add": True, "remove": False}


def test_apply_writes_the_chosen_changes_and_a_second_run_proposes_none(tmp_path, monkeypatch):
    db, tid, client = _app(tmp_path, monkeypatch, ["Gut"], _gut_by_hand)
    j = _preview(client, tid)
    r = _apply(client, tid, j, [c["id"] for c in j["changes"]])
    assert r.status_code == 200 and r.get_json()["added"] == 1 \
        and r.get_json()["removed"] == 1
    rows = _chain(db, tid)
    assert {row[3] for row in rows} == {"", "Microbiome", "Transform"}
    assert all(row[1] == "Gut" and row[0] == 1 for row in rows)
    added = next(row for row in rows if row[3] == "Transform")
    assert added[4] == 0 and added[5] == "clinical"
    assert _preview(client, tid)["changes"] == []


def test_an_unticked_change_is_not_written(tmp_path, monkeypatch):
    db, tid, client = _app(tmp_path, monkeypatch, ["Gut"], _gut_by_hand)
    j = _preview(client, tid)
    add = next(c["id"] for c in j["changes"] if c["kind"] == "add")
    assert _apply(client, tid, j, [add]).status_code == 200
    assert "Old Remedy" in {row[3] for row in _chain(db, tid)}


def test_a_changed_chain_refuses_the_old_preview(tmp_path, monkeypatch):
    db, tid, client = _app(tmp_path, monkeypatch, ["Gut"], _gut_by_hand)
    j = _preview(client, tid)
    with sqlite3.connect(db) as cx:
        add_chain_row(cx, tid, 1, "Gut", "", "Typed Meanwhile")
    before = _chain(db, tid)
    r = _apply(client, tid, j, [c["id"] for c in j["changes"]])
    assert r.status_code == 409 and r.get_json()["stale"]
    assert _chain(db, tid) == before


def test_a_change_id_from_nowhere_is_refused(tmp_path, monkeypatch):
    db, tid, client = _app(tmp_path, monkeypatch, ["Gut"], _gut_by_hand)
    j = _preview(client, tid)
    before = _chain(db, tid)
    assert _apply(client, tid, j, ["remove:999"]).status_code == 409
    assert _chain(db, tid) == before


def test_a_ticked_topic_with_no_layer_gets_a_new_draft_layer(tmp_path, monkeypatch):
    def setup(cx, tid):
        _gut_by_hand(cx, tid)
        _topic(cx, "Vertigo")
        save_selection(cx, tid, "Vertigo", ["Vitamin B12"])
    db, tid, client = _app(tmp_path, monkeypatch, ["Gut", "Vertigo"], setup)
    j = _preview(client, tid)
    new = [c for c in j["changes"] if c["kind"] == "new"]
    assert [(c["layer"], c["remedies"], c["preticked"]) for c in new] == \
        [("Vertigo", ["Vitamin B12"], True)]
    assert _apply(client, tid, j, [new[0]["id"]]).status_code == 200
    rows = [r for r in _chain(db, tid) if r[1] == "Vertigo"]
    assert rows == [(2, "Vertigo", "Vertigo", "Vitamin B12", 0, "clinical")]


def test_a_new_layer_takes_the_topics_display_name(tmp_path, monkeypatch):
    def setup(cx, tid):
        _topic(cx, "Cataract and vision")
        set_display_label(cx, "Cataract and vision", "Crystalline Lens")
        save_selection(cx, tid, "Cataract and vision", ["Clear Lens Eye Drops"])
    db, tid, client = _app(tmp_path, monkeypatch, ["Cataract and vision"], setup)
    new = [c for c in _preview(client, tid)["changes"] if c["kind"] == "new"]
    assert [c["layer"] for c in new] == ["Crystalline Lens"]


def test_a_layer_named_by_the_old_label_matches_the_display_name(tmp_path, monkeypatch):
    """Josie's layer 1 is "Cataract and vision"; the topic now displays as Crystalline Lens."""
    def setup(cx, tid):
        _topic(cx, "Cataract and vision")
        set_display_label(cx, "Cataract and vision", "Crystalline Lens")
        add_chain_row(cx, tid, 1, "Cataract and vision", "Cataract and vision",
                      "Clear Lens Eye Drops")
        save_selection(cx, tid, "Cataract and vision", ["Clear Lens Eye Drops"])
    db, tid, client = _app(tmp_path, monkeypatch, ["Cataract and vision"], setup)
    assert _preview(client, tid)["changes"] == []


def test_a_row_with_a_pattern_matches_by_its_pattern(tmp_path, monkeypatch):
    def setup(cx, tid):
        add_chain_row(cx, tid, 1, "Adrenal Support", "Fatigue", "Adrenal Syntropy",
                      origin="clinical")
        save_selection(cx, tid, "Fatigue", ["Adrenal Syntropy", "Ashwagandha"])
        save_pattern(cx, tid, "Fatigue", "Adrenal Support")
    db, tid, client = _app(tmp_path, monkeypatch, ["Fatigue"], setup)
    j = _preview(client, tid)
    assert [(c["kind"], c["layer"], c.get("remedy")) for c in j["changes"]] == \
        [("add", "Adrenal Support", "Ashwagandha")]


def test_a_free_text_row_with_no_pattern_is_skipped(tmp_path, monkeypatch):
    def setup(cx, tid):
        save_selection(cx, tid, "My eyes hurt", ["Clear Lens Eye Drops"])
    db, tid, client = _app(tmp_path, monkeypatch, ["My eyes hurt"], setup)
    j = _preview(client, tid)
    assert j["changes"] == [] and j["no_pattern"] == ["My eyes hurt"]


def test_a_scan_layer_is_never_changed(tmp_path, monkeypatch):
    def setup(cx, tid):
        _topic(cx, "Gut")
        add_chain_row(cx, tid, 1, "Gut", "Gut", "Scan Remedy", origin="scan")
        save_selection(cx, tid, "Gut", ["Microbiome"])
    db, tid, client = _app(tmp_path, monkeypatch, ["Gut"], setup)
    j = _preview(client, tid)
    assert j["changes"] == [] and j["stale"] == []


def test_a_remedy_already_on_another_layer_is_not_added_again(tmp_path, monkeypatch):
    def setup(cx, tid):
        _gut_by_hand(cx, tid)
        _topic(cx, "Vertigo")
        add_chain_row(cx, tid, 2, "Vertigo", "Vertigo", "B12")
        save_selection(cx, tid, "Vertigo", ["B12", "Microbiome"])
    db, tid, client = _app(tmp_path, monkeypatch, ["Gut", "Vertigo"], setup)
    j = _preview(client, tid)
    assert not any(c.get("remedy") == "Microbiome" and c["kind"] == "add"
                   for c in j["changes"])
    assert {"remedy": "Microbiome", "layer": "Gut", "for": "Vertigo"} in j["elsewhere"]


def test_an_unticked_topic_flags_its_layer_and_keeps_it(tmp_path, monkeypatch):
    def setup(cx, tid):
        _gut_by_hand(cx, tid)
        save_selection(cx, tid, "Gut", [])
    db, tid, client = _app(tmp_path, monkeypatch, ["Gut"], setup)
    before = _chain(db, tid)
    j = _preview(client, tid)
    assert j["changes"] == []
    assert [s["layer"] for s in j["stale"]] == ["Gut"]
    assert _chain(db, tid) == before


def test_a_clinical_layer_no_row_earns_is_flagged(tmp_path, monkeypatch):
    def setup(cx, tid):
        add_chain_row(cx, tid, 1, "Retired Pattern", "Gone", "X", origin="clinical")
    db, tid, client = _app(tmp_path, monkeypatch, [], setup)
    assert [s["layer"] for s in _preview(client, tid)["stale"]] == ["Retired Pattern"]


def test_removing_the_row_that_carries_the_tail_keeps_the_tail(tmp_path, monkeypatch):
    """Josie's layer 1 carries its tail on the Pyrite row itself."""
    def setup(cx, tid):
        _topic(cx, "Cataract and vision")
        add_chain_row(cx, tid, 1, "Cataract and vision", "Cataract and vision", "Pyrite")
        add_chain_row(cx, tid, 1, "Cataract and vision", "", "Clear Lens Eye Drops")
        save_selection(cx, tid, "Cataract and vision", ["Clear Lens Eye Drops"])
    db, tid, client = _app(tmp_path, monkeypatch, ["Cataract and vision"], setup)
    j = _preview(client, tid)
    assert _apply(client, tid, j, [c["id"] for c in j["changes"]]).status_code == 200
    rows = _chain(db, tid)
    assert ("Cataract and vision" in {r[2] for r in rows})
    assert {r[3] for r in rows} == {"", "Clear Lens Eye Drops"}


def test_the_page_has_the_button():
    from dashboard import biofield_report_html as page
    import inspect
    src = inspect.getsource(page)
    assert "onclick=clinicalLayersUpdate()>Update Clinical layers</button>" in src
    assert "async function clinicalLayersUpdateApply()" in src


def test_a_chain_remedy_covering_an_unsaved_row_is_not_offered_for_removal(tmp_path, monkeypatch):
    """The page shows a row ticked by the chain remedy that covers it when nothing
    is saved for the row. That remedy is ticked, so it must not read as unticked."""
    from dashboard.biofield_clinical_checklist import remember_remedies

    def setup(cx, tid):
        _topic(cx, "Gut")
        remember_remedies(cx, "Gut", ["Microbiome"])
        add_chain_row(cx, tid, 1, "Gut", "Gut", "Microbiome")
    db, tid, client = _app(tmp_path, monkeypatch, ["Gut"], setup)
    assert _preview(client, tid)["changes"] == []


def test_a_remedy_ticked_twice_lands_on_the_existing_layer_not_the_new_one(tmp_path, monkeypatch):
    """Josie: Fibrosolve is ticked under Blood and circulation, which has no layer, and
    under Cataract and vision, which is layer 1. A new layer goes last in the chain,
    so the remedy belongs on layer 1."""
    def setup(cx, tid):
        _topic(cx, "Blood and circulation")
        _topic(cx, "Cataract and vision")
        add_chain_row(cx, tid, 1, "Cataract and vision", "Cataract and vision", "Clear Lens")
        save_selection(cx, tid, "Blood and circulation", ["Fibrosolve", "B12"])
        save_selection(cx, tid, "Cataract and vision", ["Clear Lens", "Fibrosolve"])
    db, tid, client = _app(tmp_path, monkeypatch,
                           ["Blood and circulation", "Cataract and vision"], setup)
    j = _preview(client, tid)
    assert [(c["kind"], c["layer"], c.get("remedy") or c.get("remedies"))
            for c in j["changes"]] == [("add", "Cataract and vision", "Fibrosolve"),
                                       ("new", "Blood and circulation", ["B12"])]
