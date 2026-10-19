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
    # Layer 1 is confirmed, so its add is offered unticked and reserves nothing.
    assert [(c["kind"], c["layer"], c.get("remedy") or c.get("remedies"))
            for c in j["changes"]] == [("add", "Cataract and vision", "Fibrosolve"),
                                       ("new", "Blood and circulation", ["Fibrosolve", "B12"])]
    assert _apply(client, tid, j, [c["id"] for c in j["changes"]]).status_code == 200
    assert [r[1] for r in _chain(db, tid) if r[3] == "Fibrosolve"] == ["Cataract and vision"]


def test_a_draft_layer_reserves_its_remedy(tmp_path, monkeypatch):
    def setup(cx, tid):
        _topic(cx, "Blood and circulation")
        _topic(cx, "Cataract and vision")
        add_chain_row(cx, tid, 1, "Cataract and vision", "Cataract and vision", "Clear Lens",
                      confirmed=0)
        save_selection(cx, tid, "Blood and circulation", ["Fibrosolve", "B12"])
        save_selection(cx, tid, "Cataract and vision", ["Clear Lens", "Fibrosolve"])
    db, tid, client = _app(tmp_path, monkeypatch,
                           ["Blood and circulation", "Cataract and vision"], setup)
    j = _preview(client, tid)
    assert [c.get("remedies") for c in j["changes"] if c["kind"] == "new"] == [["B12"]]


def test_a_repeated_change_id_writes_once(tmp_path, monkeypatch):
    db, tid, client = _app(tmp_path, monkeypatch, ["Gut"], _gut_by_hand)
    j = _preview(client, tid)
    add = next(c["id"] for c in j["changes"] if c["kind"] == "add")
    assert _apply(client, tid, j, [add, add]).status_code == 200
    assert [r[3] for r in _chain(db, tid)].count("Transform") == 1


def test_apply_must_be_true_exactly(tmp_path, monkeypatch):
    db, tid, client = _app(tmp_path, monkeypatch, ["Gut"], _gut_by_hand)
    j = _preview(client, tid)
    before = _chain(db, tid)
    r = client.post(URL.format(tid), json={"apply": "false", "digest": j["digest"],
                                           "chosen": [c["id"] for c in j["changes"]]})
    assert r.get_json()["proposed"] and _chain(db, tid) == before


def test_a_tick_saved_after_the_checklist_was_read_refuses_the_apply(tmp_path, monkeypatch):
    """Review round 2: the checklist is read before the write lock. A tick saved in
    between must still change the digest, which is read under the lock."""
    from dashboard import biofield_clinical_layer_sync as sync
    db, tid, client = _app(tmp_path, monkeypatch, ["Gut"], _gut_by_hand)
    j = _preview(client, tid)
    items = [{"label": "Gut", "checked": True, "selection_saved": True,
              "selected_remedies": ["Microbiome", "Transform"]}]
    with sqlite3.connect(db) as cx:
        assert sync.plan(cx, tid, items)["digest"] == j["digest"]
        save_selection(cx, tid, "Gut", ["Microbiome"])
        cx.commit()
        try:
            sync.apply(cx, tid, items, j["digest"], [c["id"] for c in j["changes"]])
            raise AssertionError("applied over a tick saved after the preview")
        except sync.Stale:
            pass
    assert "Transform" not in {r[3] for r in _chain(db, tid)}


def test_an_unticked_add_on_a_confirmed_layer_does_not_starve_a_new_layer(tmp_path, monkeypatch):
    """Review round 2: X offered unticked on confirmed Gut must still reach the new
    Vertigo layer, which is pre-ticked. Ticking both lands X once, on Gut."""
    def setup(cx, tid):
        _gut_by_hand(cx, tid)
        save_selection(cx, tid, "Gut", ["Microbiome", "Old Remedy", "X"])
        _topic(cx, "Vertigo")
        save_selection(cx, tid, "Vertigo", ["X"])
    db, tid, client = _app(tmp_path, monkeypatch, ["Gut", "Vertigo"], setup)
    j = _preview(client, tid)
    new = next(c for c in j["changes"] if c["kind"] == "new")
    assert new["remedies"] == ["X"]
    assert _apply(client, tid, j).status_code == 200
    assert [(r[1], r[3]) for r in _chain(db, tid) if r[3] == "X"] == [("Vertigo", "X")]


def test_ticking_both_lands_the_remedy_once_on_the_earlier_layer(tmp_path, monkeypatch):
    def setup(cx, tid):
        _gut_by_hand(cx, tid)
        save_selection(cx, tid, "Gut", ["Microbiome", "Old Remedy", "X"])
        _topic(cx, "Vertigo")
        save_selection(cx, tid, "Vertigo", ["X"])
    db, tid, client = _app(tmp_path, monkeypatch, ["Gut", "Vertigo"], setup)
    j = _preview(client, tid)
    assert _apply(client, tid, j, [c["id"] for c in j["changes"]]).status_code == 200
    assert [(r[1], r[3]) for r in _chain(db, tid) if r[3] == "X"] == [("Gut", "X")]


def test_removing_a_row_keeps_a_tail_no_sibling_shares(tmp_path, monkeypatch):
    """Review round 2: two rows on one pattern carry different tails."""
    def setup(cx, tid):
        add_chain_row(cx, tid, 1, "Adrenal Support", "Fatigue", "Old", origin="clinical")
        add_chain_row(cx, tid, 1, "Adrenal Support", "Stress", "Keep", origin="clinical")
        save_selection(cx, tid, "Fatigue", ["Keep"])
        save_pattern(cx, tid, "Fatigue", "Adrenal Support")
    db, tid, client = _app(tmp_path, monkeypatch, ["Fatigue"], setup)
    j = _preview(client, tid)
    assert [c["kind"] for c in j["changes"]] == ["remove"]
    assert _apply(client, tid, j, [j["changes"][0]["id"]]).status_code == 200
    assert {r[2] for r in _chain(db, tid)} == {"Fatigue", "Stress"}


def test_a_new_layer_chosen_twice_is_made_once(tmp_path, monkeypatch):
    def setup(cx, tid):
        _topic(cx, "Vertigo")
        save_selection(cx, tid, "Vertigo", ["B12"])
    db, tid, client = _app(tmp_path, monkeypatch, ["Vertigo"], setup)
    j = _preview(client, tid)
    new = j["changes"][0]["id"]
    assert _apply(client, tid, j, [new, new]).status_code == 200
    assert len(_chain(db, tid)) == 1


def test_one_remedy_offered_on_two_confirmed_layers_lands_once(tmp_path, monkeypatch):
    def setup(cx, tid):
        _gut_by_hand(cx, tid)
        save_selection(cx, tid, "Gut", ["Microbiome", "Old Remedy", "X"])
        _topic(cx, "Vertigo")
        add_chain_row(cx, tid, 2, "Vertigo", "Vertigo", "B12")
        save_selection(cx, tid, "Vertigo", ["B12", "X"])
    db, tid, client = _app(tmp_path, monkeypatch, ["Gut", "Vertigo"], setup)
    j = _preview(client, tid)
    assert len([c for c in j["changes"] if c.get("remedy") == "X"]) == 2
    r = _apply(client, tid, j, [c["id"] for c in j["changes"]])
    # Counted at the write: the page folds a repeat away when it next reads the chain.
    assert r.status_code == 200 and r.get_json()["added"] == 1
    assert [(r[1], r[3]) for r in _chain(db, tid) if r[3] == "X"] == [("Gut", "X")]


def test_a_remedy_on_a_blank_head_row_is_not_added_again(tmp_path, monkeypatch):
    """Review round 1: the editor keeps a blank-head row as a layer of its own."""
    def setup(cx, tid):
        add_chain_row(cx, tid, 1, "", "", "B12")
        _topic(cx, "Vertigo")
        save_selection(cx, tid, "Vertigo", ["B12", "Ginkgo"])
    db, tid, client = _app(tmp_path, monkeypatch, ["Vertigo"], setup)
    j = _preview(client, tid)
    assert [c["remedies"] for c in j["changes"]] == [["Ginkgo"]]


def test_a_blank_head_row_added_after_the_preview_refuses_the_apply(tmp_path, monkeypatch):
    db, tid, client = _app(tmp_path, monkeypatch, ["Gut"], _gut_by_hand)
    j = _preview(client, tid)
    with sqlite3.connect(db) as cx:
        add_chain_row(cx, tid, 5, "", "", "Zeolite")
    assert _apply(client, tid, j).status_code == 409


def test_one_layer_paired_with_two_rows_offers_each_change_once(tmp_path):
    """Review round 1: a topic whose key and display name hit two pattern rows, so
    two targets both hold the layer's key."""
    from dashboard import biofield_clinical_layer_sync as sync
    with sqlite3.connect(str(tmp_path / "c.db")) as cx:
        init_auth_tables(cx)
        tid = create_test(cx, "J", "j@example.com", "2026-10-19")
        _topic(cx, "Gut")
        set_display_label(cx, "Gut", "Digestive Function")
        add_chain_row(cx, tid, 1, "Gut", "Gut", "Old")
        add_chain_row(cx, tid, 1, "Gut", "", "Keep")
        tick = {"checked": True, "selection_saved": True, "selected_remedies": ["Keep"]}
        items = [dict(tick, label="A", stress_pattern="Gut"),
                 dict(tick, label="B", stress_pattern="Digestive Function"),
                 dict(tick, label="Gut")]
        wanted, _ = sync.targets(cx, items)
        assert sum("gut" in w["keys"] for w in wanted) == 2
        ids = [c["id"] for c in sync.plan(cx, tid, items)["changes"]]
    assert ids == ["remove:1"]


# Review round 3: guards no test caught, and two defects.

def test_removing_the_only_remedy_keeps_the_layer(tmp_path, monkeypatch):
    def setup(cx, tid):
        _topic(cx, "Gut")
        add_chain_row(cx, tid, 1, "Gut", "", "Only")
        save_selection(cx, tid, "Gut", ["Other"])
    db, tid, client = _app(tmp_path, monkeypatch, ["Gut"], setup)
    j = _preview(client, tid)
    rm = [c["id"] for c in j["changes"] if c["kind"] == "remove"]
    assert _apply(client, tid, j, rm).status_code == 200
    assert [(r[1], r[3]) for r in _chain(db, tid)] == [("Gut", "")]


def test_a_dose_edited_after_the_preview_refuses_the_apply(tmp_path, monkeypatch):
    db, tid, client = _app(tmp_path, monkeypatch, ["Gut"], _gut_by_hand)
    j = _preview(client, tid)
    with sqlite3.connect(db) as cx:
        cx.execute("UPDATE biofield_auth_chain SET dosage='2 drops' WHERE remedy='Old Remedy'")
    assert _apply(client, tid, j).status_code == 409


def test_a_saved_order_after_the_preview_refuses_the_apply(tmp_path, monkeypatch):
    from dashboard.biofield_clinical_proposals import save_order
    db, tid, client = _app(tmp_path, monkeypatch, ["Gut"], _gut_by_hand)
    j = _preview(client, tid)
    with sqlite3.connect(db) as cx:
        save_order(cx, tid, ["Gut", "Elsewhere"])
        cx.commit()
    assert _apply(client, tid, j).status_code == 409


def test_a_program_layer_is_never_changed(tmp_path, monkeypatch):
    def setup(cx, tid):
        _topic(cx, "Gut")
        add_chain_row(cx, tid, 1, "Gut", "Gut", "Program Remedy", origin="program")
        save_selection(cx, tid, "Gut", ["Microbiome"])
    db, tid, client = _app(tmp_path, monkeypatch, ["Gut"], setup)
    j = _preview(client, tid)
    assert j["changes"] == [] and j["stale"] == []


def test_a_failure_part_way_writes_nothing(tmp_path, monkeypatch):
    from dashboard import biofield_clinical_layer_sync as sync
    def setup(cx, tid):
        _gut_by_hand(cx, tid, confirmed=0)
        _topic(cx, "Vertigo")
        save_selection(cx, tid, "Vertigo", ["B12"])
    db, tid, client = _app(tmp_path, monkeypatch, ["Gut", "Vertigo"], setup)
    j = _preview(client, tid)
    before = _chain(db, tid)
    real, calls = sync._insert, []
    def failing(*a, **k):
        calls.append(1)
        if len(calls) == 2:
            raise RuntimeError("disk")
        return real(*a, **k)
    monkeypatch.setattr(sync, "_insert", failing)
    try:
        _apply(client, tid, j, [c["id"] for c in j["changes"]])
    except RuntimeError:
        pass
    assert len(calls) == 2 and _chain(db, tid) == before


def test_the_plan_order_wins_over_the_request_order(tmp_path, monkeypatch):
    def setup(cx, tid):
        _gut_by_hand(cx, tid)
        save_selection(cx, tid, "Gut", ["Microbiome", "Old Remedy", "X"])
        _topic(cx, "Vertigo")
        add_chain_row(cx, tid, 2, "Vertigo", "Vertigo", "B12")
        save_selection(cx, tid, "Vertigo", ["B12", "X"])
    db, tid, client = _app(tmp_path, monkeypatch, ["Gut", "Vertigo"], setup)
    j = _preview(client, tid)
    ids = [c["id"] for c in j["changes"]]
    assert _apply(client, tid, j, list(reversed(ids))).status_code == 200
    with sqlite3.connect(db) as cx:
        heads = [r[0] for r in cx.execute("SELECT head FROM biofield_auth_chain "
                                          "WHERE remedy='X' ORDER BY id")]
    assert heads == ["Gut"]


def test_a_hand_layer_no_row_names_is_not_flagged_stale(tmp_path, monkeypatch):
    def setup(cx, tid):
        add_chain_row(cx, tid, 1, "Emotional Stress", "Emotional Stress", "Stress Release")
    db, tid, client = _app(tmp_path, monkeypatch, [], setup)
    assert _preview(client, tid)["stale"] == []


def test_a_remedy_on_two_rows_of_a_layer_is_offered_for_removal_on_both(tmp_path, monkeypatch):
    def setup(cx, tid):
        _topic(cx, "Gut")
        add_chain_row(cx, tid, 1, "Gut", "Gut", "Keep")
        add_chain_row(cx, tid, 1, "Gut", "", "Twice")
        add_chain_row(cx, tid, 1, "Gut", "", "Twice")
        save_selection(cx, tid, "Gut", ["Keep"])
    db, tid, client = _app(tmp_path, monkeypatch, ["Gut"], setup)
    j = _preview(client, tid)
    assert [c.get("remedy") for c in j["changes"]] == ["Twice", "Twice"]
    assert _apply(client, tid, j, [c["id"] for c in j["changes"]]).status_code == 200
    assert "Twice" not in {r[3] for r in _chain(db, tid)}


def test_a_row_reaching_a_layer_by_its_second_name_keeps_its_ticks(tmp_path):
    """Round 3, defect B: t1 holds key a, t2 key b, and an item with both merges
    into t1. Both reach layer b; t2's ticks must still be offered."""
    from dashboard import biofield_clinical_layer_sync as sync
    with sqlite3.connect(str(tmp_path / "c.db")) as cx:
        init_auth_tables(cx)
        tid = create_test(cx, "J", "j@example.com", "2026-10-19")
        _topic(cx, "Gut")
        set_display_label(cx, "Gut", "Digestive Function")
        add_chain_row(cx, tid, 1, "Gut", "Gut", "Keep")
        tick = {"checked": True, "selection_saved": True}
        items = [dict(tick, label="A", stress_pattern="Digestive Function",
                      selected_remedies=["Keep"]),
                 dict(tick, label="B", stress_pattern="Gut", selected_remedies=["Only B"]),
                 dict(tick, label="Gut", selected_remedies=["Keep"])]
        changes = sync.plan(cx, tid, items)["changes"]
    assert [(c["kind"], c.get("remedy")) for c in changes] == [("add", "Only B")]


# Carried over from the retired "Clinical layers -> propose" button (2026-10-19).

def test_a_hand_added_row_earns_its_layer(tmp_path, monkeypatch):
    """Alyssa Fukushima (a40), 2026-09-22: a row added by hand lives only as an
    accepted decision, never in the profile."""
    from dashboard.biofield_clinical_checklist import remember_stress_pattern
    from dashboard.biofield_clinical_proposals import decide

    def setup(cx, tid):
        decide(cx, tid, "Sleep Difficulty", "accepted", "Manually added")
        save_selection(cx, tid, "Sleep Difficulty", ["Sleep Syntropy"])
        remember_stress_pattern(cx, "Sleep Difficulty", "Sleep Regulation")
    db, tid, client = _app(tmp_path, monkeypatch, [], setup)
    assert [(c["layer"], c["remedies"]) for c in _preview(client, tid)["changes"]] == \
        [("Sleep Regulation", ["Sleep Syntropy"])]


def test_it_sees_no_scan_findings(tmp_path, monkeypatch):
    """Glen, 2026-09-18: Clinical Summary patterns and remedies only."""
    from dashboard.biofield_stress import add_stress, init_stress_tables

    def setup(cx, tid):
        init_stress_tables(cx)
        add_stress(cx, tid, "ED12 Kidney Driver", source="scan", balance="required")
    db, tid, client = _app(tmp_path, monkeypatch, [], setup)
    assert _preview(client, tid)["changes"] == []


def test_the_old_button_is_gone():
    from dashboard import biofield_report_html as page
    import inspect
    src = inspect.getsource(page)
    assert "clinicalLayers()" not in src and "Clinical layers &rarr; propose" not in src
