"""Add to plan, through the routes (Glen, 2026-11-07).

Body: the product chosen for each recommended Infoceutical, then the Import Reveal
layers. A same-product layer joins at once; a related one is put on the plan and
proposed; nothing joins until Glen posts a choice. The reveal synthesis is
monkeypatched; the chain, the e4l map and the proposals are real tmp databases."""
import json
import sqlite3

import pytest

from biofield_local_app import create_app
import dashboard.biofield_reveal_import as RI
from dashboard.biofield_authoring import add_chain_row


def _species(monkeypatch, species):
    """The tmp e4l.db has no client table, so the subject is stubbed: nobody on E4L,
    with this species."""
    import dashboard.biofield_e4l as _be
    monkeypatch.setattr(_be, "species_from_e4l", lambda e4l_db, email: species)
    monkeypatch.setattr(_be, "resolve_intake_subject", lambda *a, **k: {
        "by": "none", "client": None, "scan_ids": None, "species": species})


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    import dashboard as _d
    import dashboard.biofield_e4l as _be
    _species(monkeypatch, "Human")
    monkeypatch.delenv("CONSOLE_SECRET", raising=False)
    monkeypatch.setattr(_d, "CONSOLE_SECRET", "", raising=False)
    import dashboard.e4l_scan_marks as _m
    monkeypatch.setattr(_m, "infoceutical_marks",
                        lambda cx, db, scan: {"recommended": {"ES1", "MB8"}})


def _e4l(path):
    cx = sqlite3.connect(path)
    cx.executescript("""
      CREATE TABLE e4l_scans (scan_id INTEGER PRIMARY KEY, scan_date TEXT, raw_pdf_path TEXT);
      CREATE TABLE e4l_items (code TEXT PRIMARY KEY, category TEXT, name TEXT);
      CREATE TABLE e4l_scan_results (id INTEGER PRIMARY KEY, scan_id INTEGER, item_code TEXT,
        priority_rank INTEGER, section_context TEXT);
      CREATE TABLE formulations (id INTEGER PRIMARY KEY, name TEXT);
      CREATE TABLE e4l_formulation_map (id INTEGER PRIMARY KEY, item_code TEXT,
        finding_pattern TEXT, formulation_id INTEGER, priority INTEGER,
        order_tier INTEGER, qualifying_conditions TEXT);
      INSERT INTO e4l_scans VALUES (9, '2026-11-06', '');
      INSERT INTO e4l_items VALUES ('ES1','ES','Lymph'), ('MB8','MB','Love'), ('ED2','ED','Heart');
      INSERT INTO e4l_scan_results (scan_id,item_code,priority_rank,section_context) VALUES
        (9,'MB8',2,'Infoceuticals'), (9,'ES1',1,'Infoceuticals'), (9,'ED2',NULL,'Priority');
      INSERT INTO formulations VALUES (1,'Lymph Flow'), (2,'Immune Modulation'),
        (3,'Microbiome'), (4,'Nous Energy'), (5,'Heart Health');
      INSERT INTO e4l_formulation_map (item_code,formulation_id,priority,order_tier) VALUES
        ('ES1',1,1,1), ('ES1',2,2,1), ('ES1',3,3,2), ('MB8',4,1,1), ('ED2',1,2,1);
    """)
    cx.commit()
    cx.close()


def _reveal(*layers):
    return {"found": True, "scan_id": 9, "scan_date": "2026-11-06", "days_ago": 1,
            "fresh": True, "layers": [dict({"most_affected": "", "summary": "",
                                            "codes": [], "laser": None}, **L)
                                      for L in layers]}


def _client(tmp_path, monkeypatch, result, setup=None):
    monkeypatch.setattr(RI, "synthesize_reveal_layers", lambda *a, **k: result)
    db = str(tmp_path / "chat_log.db")
    e4l = str(tmp_path / "e4l.db")
    _e4l(e4l)
    with sqlite3.connect(db) as cx:
        cx.execute("CREATE TABLE fmp_snap_products (product_name TEXT, active TEXT, "
                   "dosage TEXT, dosage_freq TEXT, dosage_timing TEXT, "
                   "doses_per_bottle TEXT)")
        cx.executemany("INSERT INTO fmp_snap_products VALUES (?, 'yes', '', '', '', '')",
                       [("ES1 Lymph Energetic Star Infoceutical",),
                        ("MB8 Love Infoceutical",)])
    client = create_app(db, e4l_db=e4l,
                        scan_lookup=lambda e, client_id=None: {"found": False}).test_client()
    tid = client.post("/author/new").headers["Location"].rstrip("/").split("/")[-1]
    client.post(f"/author/{tid}/header", json={"name": "Jo", "email": "jo@x.com",
                                               "date": "2026-11-07"})
    if setup:
        with sqlite3.connect(db) as cx:
            setup(cx, tid)
    return client, tid, db


def _chain(db):
    with sqlite3.connect(db) as cx:
        return [(r[0], r[1], r[2], json.loads(r[3] or "[]")) for r in cx.execute(
            "SELECT layer,head,remedy,codes FROM biofield_auth_chain ORDER BY layer,id")]


URL = "/author/{}/e4l/add-to-plan"


def test_on_an_empty_plan_the_chosen_products_come_first_then_the_reveal(
        tmp_path, monkeypatch):
    res = _reveal({"n": 1, "title": "Lymph drainage", "remedy_name": "Lymph Flow",
                   "codes": ["ES1", "ER5"]},
                  {"n": 2, "title": "Kidney Health", "remedy_name": "Kidney Kare",
                   "codes": ["ED12"]})
    client, tid, db = _client(tmp_path, monkeypatch, res)
    j = client.post(URL.format(tid), json={"choices": {"ES1": "Lymph Flow"}}).get_json()
    assert j["ok"], j
    # Row 1 chose its top Remedy Match; MB8 took its Infoceutical by default. The
    # reveal's Lymph Flow layer is the same product, so it joined row 1.
    assert _chain(db) == [(1, "Lymph", "Lymph Flow", ["ES1", "ER5"]),
                          (2, "Love", "MB8 Love Infoceutical", ["MB8"]),
                          (3, "Kidney Health", "Kidney Kare", ["ED12"])]


def test_a_choice_the_row_does_not_offer_is_refused(tmp_path, monkeypatch):
    client, tid, db = _client(tmp_path, monkeypatch, _reveal())
    j = client.post(URL.format(tid), json={"choices": {"ES1": "Heart Health"}}).get_json()
    assert j["ok"] is False and "not offered" in j["reason"] and _chain(db) == []


def test_an_animal_always_takes_the_infoceutical(tmp_path, monkeypatch):
    _species(monkeypatch, "Dog")
    client, tid, db = _client(tmp_path, monkeypatch, _reveal())
    j = client.post(URL.format(tid), json={"choices": {"ES1": "Lymph Flow"}}).get_json()
    assert j["ok"], j
    assert [r[2] for r in _chain(db)] == ["ES1 Lymph Energetic Star Infoceutical",
                                          "MB8 Love Infoceutical"]


def _gut(cx, tid):
    add_chain_row(cx, tid, 1, "Gut", "Gut", "Alkalize Powder", origin="clinical",
                  codes=["ED2"])


def _three_way():
    return _reveal({"n": 1, "title": "Acid load", "most_affected": "Kidney",
                    "remedy_name": "Alkalize Powder", "codes": ["ED2", "ED12"]},
                   {"n": 2, "title": "Gut Repair", "remedy_name": "Gut Restore",
                    "codes": ["ET5"]},
                   {"n": 3, "title": "Bone Density", "remedy_name": "Bone Builder"})


def _added(client, tid, choices=None):
    j = client.post(URL.format(tid), json={"choices": choices or {}}).get_json()
    assert j["needs_confirm"], j
    return j, client.post(URL.format(tid), json={"token": j["token"]}).get_json()


def test_on_a_plan_with_layers_it_previews_then_joins_relates_and_adds(tmp_path, monkeypatch):
    client, tid, db = _client(tmp_path, monkeypatch, _three_way(), _gut)
    j = client.post(URL.format(tid), json={}).get_json()
    assert j["needs_confirm"] and len(_chain(db)) == 1, "the preview wrote"
    p = j["preview"]
    assert [(L["remedy"], L["into"], L["codes"]) for L in p["joined"]] == [
        ("Alkalize Powder", 1, ["ED12"])]
    assert [(L["title"], L["into"], L["rule"]) for L in p["related"]] == [
        ("Gut Repair", 1, "same heading")]
    assert [L["remedy"] for L in p["add"]] == ["ES1 Lymph Energetic Star Infoceutical",
                                               "MB8 Love Infoceutical", "Bone Builder"]
    done = client.post(URL.format(tid), json={"token": j["token"]}).get_json()
    assert done["ok"] and len(done["proposals"]) == 1
    chain = _chain(db)
    # The same product joined layer 1: its new code is there, and no second row.
    assert chain[0] == (1, "Gut", "Alkalize Powder", ["ED2", "ED12"])
    assert [r[2] for r in chain].count("Alkalize Powder") == 1
    with sqlite3.connect(db) as cx:
        tail = cx.execute("SELECT most_affected FROM biofield_auth_chain "
                          "WHERE remedy='Alkalize Powder'").fetchone()[0]
    assert tail == "Gut, Kidney"
    # The related layer is held in the list, never written (Glen, 2026-11-07: "yes,
    # hold them in the list"), so the added layers leave no gap for it.
    assert [(r[0], r[1], r[2]) for r in chain[1:]] == [
        (2, "Lymph", "ES1 Lymph Energetic Star Infoceutical"),
        (3, "Love", "MB8 Love Infoceutical"), (4, "Bone Density", "Bone Builder")]
    props = client.get(f"/author/{tid}/add-to-plan/proposals").get_json()["proposals"]
    assert [(q["source_head"], q["into_head"], q["source_remedies"], q["into_remedies"])
            for q in props] == [("Gut Repair", "Gut", ["Gut Restore"], ["Alkalize Powder"])]


def test_a_used_or_forged_token_writes_nothing(tmp_path, monkeypatch):
    client, tid, db = _client(tmp_path, monkeypatch, _three_way(), _gut)
    j, done = _added(client, tid)
    n = len(_chain(db))
    again = client.post(URL.format(tid), json={"token": j["token"]}).get_json()
    forged = client.post(URL.format(tid), json={"token": "forged"}).get_json()
    assert again["ok"] is False and forged["ok"] is False and len(_chain(db)) == n


def test_a_plan_changed_after_the_preview_is_refused(tmp_path, monkeypatch):
    client, tid, db = _client(tmp_path, monkeypatch, _three_way(), _gut)
    j = client.post(URL.format(tid), json={}).get_json()
    with sqlite3.connect(db) as cx:
        add_chain_row(cx, tid, 2, "Sleep", "", "Calm Night")
    done = client.post(URL.format(tid), json={"token": j["token"]}).get_json()
    assert done["ok"] is False and "changed" in done["reason"] and len(_chain(db)) == 2


def _resolve(client, tid, choice):
    pid = client.get(f"/author/{tid}/add-to-plan/proposals").get_json()["proposals"][0]["id"]
    return client.post(f"/author/{tid}/add-to-plan/resolve", json={"id": pid, "choice": choice})


def _gut_rows(db):
    return [r for r in _chain(db) if r[1] == "Gut"]


@pytest.mark.parametrize("choice,remedies", [
    ("existing", ["Alkalize Powder"]),
    ("new", ["Gut Restore"]),
    ("both", ["Alkalize Powder", "Gut Restore"]),
])
def test_each_join_choice_keeps_the_remedies_glen_picked(tmp_path, monkeypatch, choice,
                                                         remedies):
    client, tid, db = _client(tmp_path, monkeypatch, _three_way(), _gut)
    _added(client, tid)
    r = _resolve(client, tid, choice)
    assert r.status_code == 200, r.get_json()
    gut = _gut_rows(db)
    assert sorted(x[2] for x in gut) == remedies
    assert not any(x[1] == "Gut Repair" for x in _chain(db)), "the related layer stayed apart"
    # Whatever is kept, the layer carries both layers' codes.
    assert {c for x in gut for c in x[3]} >= {"ED2", "ED12", "ET5"}
    assert client.get(f"/author/{tid}/add-to-plan/proposals").get_json()["proposals"] == []


def test_keep_separate_adds_the_held_layer_at_the_end_and_closes_the_proposal(
        tmp_path, monkeypatch):
    client, tid, db = _client(tmp_path, monkeypatch, _three_way(), _gut)
    _added(client, tid)
    before = _chain(db)
    assert _resolve(client, tid, "separate").status_code == 200
    assert _chain(db) == before + [(5, "Gut Repair", "Gut Restore", ["ET5"])]
    assert client.get(f"/author/{tid}/add-to-plan/proposals").get_json()["proposals"] == []


def test_a_proposal_whose_layer_was_deleted_is_closed(tmp_path, monkeypatch):
    client, tid, db = _client(tmp_path, monkeypatch, _three_way(), _gut)
    _added(client, tid)
    pid = client.get(f"/author/{tid}/add-to-plan/proposals").get_json()["proposals"][0]["id"]
    with sqlite3.connect(db) as cx:
        cx.execute("DELETE FROM biofield_auth_chain WHERE head='Gut'")
    assert client.get(f"/author/{tid}/add-to-plan/proposals").get_json()["proposals"] == []
    r = client.post(f"/author/{tid}/add-to-plan/resolve", json={"id": pid, "choice": "both"})
    assert r.status_code == 409


def test_an_unknown_choice_is_refused(tmp_path, monkeypatch):
    client, tid, db = _client(tmp_path, monkeypatch, _three_way(), _gut)
    _added(client, tid)
    before = _chain(db)
    assert _resolve(client, tid, "merge").status_code == 400 and _chain(db) == before


def test_the_page_shows_the_body_panel_with_its_choices(tmp_path, monkeypatch):
    import dashboard.biofield_e4l  # noqa: F401
    res = _reveal()
    monkeypatch.setattr(RI, "synthesize_reveal_layers", lambda *a, **k: res)
    db = str(tmp_path / "chat_log.db")
    e4l = str(tmp_path / "e4l.db")
    _e4l(e4l)
    scan = {"found": True, "scan_id": 9, "scan_date": "2026-11-06", "days_ago": 1,
            "status": "fresh", "infoceuticals": [], "stresses": []}
    client = create_app(db, e4l_db=e4l,
                        scan_lookup=lambda e, client_id=None: scan).test_client()
    tid = client.post("/author/new").headers["Location"].rstrip("/").split("/")[-1]
    client.post(f"/author/{tid}/header", json={"name": "Jo", "email": "jo@x.com",
                                               "date": "2026-11-07"})
    html = client.get(f"/author/{tid}/e4l").get_json()["html"]
    assert "id=bodypanel" in html and "onclick=bodyAddToPlan()>Add to plan" in html
    assert html.index("data-code='ES1'") < html.index("data-code='MB8'"), "scan rank order"
    assert "All Infoceuticals" in html and "All Remedy Matches" in html
    assert 'value="Lymph Flow"' in html and 'value="Immune Modulation"' in html
    assert 'value="Microbiome"' in html and "second order" in html
    assert "shares ES1, ED2" in html          # Lymph Flow is mapped to ED2 on this scan too
    page = client.get(f"/author/{tid}").get_data(as_text=True)
    assert "id=atpList" in page and "atpLoad()" in page
    assert "onclick=clinicalLayersUpdate()>Add to plan</button>" in page


# Mind: the Update Clinical button, relabelled. A new layer related to one already
# on the plan is proposed after it lands.

def test_a_new_mind_layer_related_to_an_existing_one_is_proposed(tmp_path, monkeypatch):
    from dashboard import biofield_clinical_sections as cs
    from dashboard.biofield_clinical_proposals import save_selection

    def setup(cx, tid):
        add_chain_row(cx, tid, 1, "Gut", "Bloating", "Gut Restore", origin="scan",
                      codes=["ET5"])
        cs.upsert_item(cx, "Gut and digestion", cs.NUTRIENTS, {"name": "Vitamin C"})
        save_selection(cx, tid, "Gut and digestion", ["Alkalize Powder"])
        cx.commit()
    db = str(tmp_path / "chat_log.db")
    with sqlite3.connect(db) as cx:
        from dashboard.biofield_authoring import create_test, init_auth_tables
        init_auth_tables(cx)
        tid = create_test(cx, "Jo", "jo@x.com", "2026-11-07")
        setup(cx, tid)
    client = create_app(db, fetch_profile=lambda email: {"conditions": ["Gut and digestion"]},
                        fetch_recent_comms=lambda email: {}).test_client()
    url = f"/author/{tid}/clinical-items/layers/update"
    j = client.post(url, json={}).get_json()
    new = [c["id"] for c in j["changes"] if c["kind"] == "new"]
    assert new, j
    done = client.post(url, json={"apply": True, "digest": j["digest"],
                                  "chosen": new}).get_json()
    assert done["ok"] and len(done["proposals"]) == 1
    props = client.get(f"/author/{tid}/add-to-plan/proposals").get_json()["proposals"]
    assert [(q["source_head"], q["into_head"]) for q in props] == [("Gut and digestion", "Gut")]
    # The Mind layer is held in the list until Glen picks, then lands at the end.
    assert [r[1] for r in _chain(db)] == ["Gut"]
    r = client.post(f"/author/{tid}/add-to-plan/resolve",
                    json={"id": props[0]["id"], "choice": "separate"})
    assert r.status_code == 200, r.get_json()
    assert [(x[0], x[1], x[2]) for x in _chain(db)] == [
        (1, "Gut", "Gut Restore", ), (2, "Gut and digestion", "Alkalize Powder")]


def test_a_preview_is_written_once_even_when_the_write_leaves_the_plan_unchanged(
        tmp_path, monkeypatch):
    # Every layer joins with nothing new, so the plan reads the same afterwards and
    # only the one-use token stops a second confirm.
    import dashboard.biofield_body_panel as _bp
    monkeypatch.setattr(_bp, "chosen_layers", lambda panel, choices, is_animal=False: ([], []))
    res = _reveal({"n": 1, "title": "Acid", "remedy_name": "Alkalize Powder", "codes": ["ED2"],
                   "most_affected": "Gut"})
    client, tid, db = _client(tmp_path, monkeypatch, res, _gut)
    j = client.post(URL.format(tid), json={}).get_json()
    first = client.post(URL.format(tid), json={"token": j["token"]}).get_json()
    again = client.post(URL.format(tid), json={"token": j["token"]}).get_json()
    assert first["ok"] and again["ok"] is False


def test_a_held_related_layer_is_off_the_chain_and_the_invoice_until_glen_picks(
        tmp_path, monkeypatch):
    client, tid, db = _client(tmp_path, monkeypatch, _three_way(), _gut)
    _added(client, tid)
    sent = []
    catalog = [{"slug": s, "name": n} for s, n in (
        ("alkalize-powder", "Alkalize Powder"), ("gut-restore", "Gut Restore"),
        ("bone-builder", "Bone Builder"), ("es1", "ES1 Lymph Energetic Star Infoceutical"),
        ("mb8", "MB8 Love Infoceutical"))]
    import biofield_local_app as app_mod
    inv = app_mod.create_app(
        db, e4l_db=str(tmp_path / "e4l.db"), invoice_fetch_catalog=lambda: catalog,
        invoice_create=lambda c, lines, replace_open=False, invoice_note=None,
        idempotency_key="", **k: sent.append([l.get("slug") for l in lines])
        or {"ok": True, "order_id": len(sent)},
        invoice_paid_check=lambda email: {"paid": True},
        invoice_latest=lambda email: {}).test_client()

    def invoiced():
        r = inv.post(f"/author/{tid}/invoice", json={})
        assert r.status_code == 200, r.get_data(as_text=True)[:300]
        return sent[-1]
    assert "Gut Restore" not in [r[2] for r in _chain(db)]
    assert "gut-restore" not in invoiced()
    assert _resolve(client, tid, "both").status_code == 200
    assert "Gut Restore" in [r[2] for r in _chain(db)]
    assert "gut-restore" in invoiced()
