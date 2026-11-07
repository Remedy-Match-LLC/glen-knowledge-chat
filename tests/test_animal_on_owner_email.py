"""A pet started from its owner's email is still an animal, and still has its photo.

Glen, 2026-11-06: "Biofield Intake is not showing a photo for Skylar Verches, Agnes
Verches' dog. It's also recommending FF's instead of just infoceuticals."

Skylar's intake was started from a test request Agnes emailed, so it carried Agnes's
address and no E4L client id. Every species read keyed on that email and found Agnes,
a Human, so every surface offered Functional Formulations. The photo lookup keyed on
the same email, where FileMaker holds two people, so it showed nothing.

The fixture is that shape with invented people: a Human owner on the owner's email, a
Dog with her own E4L record and email, and an intake for the dog on the owner's email.
"""
import sqlite3

import pytest

import dashboard.biofield_reveal_import as RI
from dashboard import biofield_stress as st
from dashboard.animal_infoceuticals import is_infoceutical_remedy
from dashboard.biofield_e4l import (intake_e4l_client, record_species_answer,
                                    resolve_intake_subject, species_for_intake,
                                    suggest_intake_client)

OWNER = "owner@x.com"
PET = "pet@x.com"
FF = "Immune Modulation"
INFO = "ES1 Immune Energetic Star Infoceutical"


@pytest.fixture(autouse=True)
def _gate(monkeypatch):
    monkeypatch.delenv("CONSOLE_SECRET", raising=False)
    import dashboard as _d
    monkeypatch.setattr(_d, "CONSOLE_SECRET", "", raising=False)
    st.set_animal_lookup(None)
    yield
    st.set_animal_lookup(None)


def _e4l(tmp_path, extra=()):
    p = str(tmp_path / "e4l.db")
    cx = sqlite3.connect(p)
    cx.execute("CREATE TABLE e4l_clients (client_id INTEGER PRIMARY KEY, name TEXT, "
               "email TEXT, species TEXT, animal_name TEXT)")
    rows = [(1001, "Ann Owner", OWNER, "Human", ""),
            (1002, "ROVER OWNER", PET, "Dog", "Rover"), *extra]
    cx.executemany("INSERT INTO e4l_clients VALUES (?,?,?,?,?)", rows)
    cx.commit()
    cx.close()
    return p


def _app(tmp_path, e4l=None, **kw):
    from biofield_local_app import create_app
    db = str(tmp_path / "chat_log.db")
    kw.setdefault("scan_lookup", lambda e, client_id=None: {"found": False, "layers": []})
    app = create_app(db, e4l_db=e4l or _e4l(tmp_path),
                     fetch_client_photo=lambda *a, **k: None, **kw)
    return app.test_client(), db


def _intake(client, name, email, client_id=None):
    tid = client.post("/author/new").headers["Location"].rstrip("/").split("/")[-1]
    body = {"name": name, "email": email, "date": "2026-11-06"}
    if client_id is not None:
        body["client_id"] = client_id
    client.post(f"/author/{tid}/header", json=body)
    return tid


def _pet(client):
    """The dog's intake on the owner's email, after Glen confirms the suggestion."""
    tid = _intake(client, "Rover Owner", OWNER)
    j = client.post(f"/author/{tid}/e4l-client/confirm", json={"client_id": 1002}).get_json()
    assert j["ok"] is True, j
    return tid


# ---- the identity and species read --------------------------------------------------

def test_the_pet_on_the_owners_email_is_suggested_not_taken(tmp_path):
    """Review finding 2: a namesake on another email is only offered. Until Glen
    confirms, the subject is unknown and he is asked for the species."""
    p = _e4l(tmp_path)
    assert species_for_intake(p, None, OWNER, "Rover Owner") is None
    assert intake_e4l_client(p, None, OWNER, "Rover Owner") is None
    sug = suggest_intake_client(p, OWNER, "Rover Owner")
    assert sug["client_id"] == 1002 and sug["email"] == PET and sug["species"] == "Dog"


def test_the_stored_e4l_client_id_wins(tmp_path):
    p = _e4l(tmp_path)
    assert species_for_intake(p, "1002", OWNER, "Rover") == "Dog"


def test_the_owner_is_still_a_person(tmp_path):
    p = _e4l(tmp_path)
    assert species_for_intake(p, None, OWNER, "Ann Owner") == "Human"


def test_an_email_only_intake_reads_as_before(tmp_path):
    p = _e4l(tmp_path)
    assert species_for_intake(p, None, PET, "") == "Dog"
    assert species_for_intake(p, None, OWNER, "") == "Human"


def test_a_pet_with_no_species_does_not_borrow_the_owners(tmp_path):
    """Its own record is blank: the owner's Human on the same email must not answer."""
    p = _e4l(tmp_path, extra=[(1003, "Kitty Owner", OWNER, "", "")])
    assert species_for_intake(p, "1003", OWNER, "Kitty Owner") is None


def test_two_namesakes_name_nobody(tmp_path):
    p = _e4l(tmp_path, extra=[(1004, "Rover Owner", "other@x.com", "Dog", "")])
    assert intake_e4l_client(p, None, OWNER, "Rover Owner") is None
    assert suggest_intake_client(p, OWNER, "Rover Owner") is None


# ---- every surface, through the real routes -----------------------------------------

def test_suggestions_treat_the_pet_as_an_animal(tmp_path):
    client, db = _app(tmp_path)
    tid = _pet(client)
    assert st.animal_infoceuticals(sqlite3.connect(db), tid) is not None
    tid2 = _intake(client, "Ann Owner", OWNER)
    assert st.animal_infoceuticals(sqlite3.connect(db), tid2) is None


def test_import_reveal_treats_the_pet_as_an_animal(tmp_path, monkeypatch):
    seen = {}

    def capture(*a, **k):
        seen["is_animal"] = k.get("is_animal")
        return {"found": False}

    monkeypatch.setattr(RI, "synthesize_reveal_layers", capture)
    client, _db = _app(tmp_path, scan_lookup=lambda e, client_id=None: {
        "found": True, "scan_id": 77, "layers": []})
    tid = _pet(client)
    client.post(f"/author/{tid}/e4l/import-reveal", json={})
    assert seen.get("is_animal") is True


def _layers(db, tid):
    return [r[0] for r in sqlite3.connect(db).execute(
        "SELECT remedy FROM biofield_auth_chain WHERE test_id=? ORDER BY id",
        (int(tid.lstrip("a")),))]


def test_the_plus_button_refuses_an_ff_for_the_pet(tmp_path):
    client, db = _app(tmp_path)
    tid = _pet(client)
    j = client.post(f"/author/{tid}/remedy-set/add-one", json={"remedy": FF}).get_json()
    assert j["ok"] is False and j["animal_refused"] == [FF]
    assert _layers(db, tid) == []
    j = client.post(f"/author/{tid}/remedy-set/add-one", json={"remedy": INFO}).get_json()
    assert j["ok"] is True and len(_layers(db, tid)) == 1


def test_the_plus_button_still_adds_an_ff_for_a_person(tmp_path):
    client, db = _app(tmp_path)
    tid = _intake(client, "Ann Owner", OWNER)
    j = client.post(f"/author/{tid}/remedy-set/add-one", json={"remedy": FF}).get_json()
    assert j["ok"] is True and len(_layers(db, tid)) == 1


def test_apply_to_chain_drops_the_ffs_for_the_pet(tmp_path):
    client, db = _app(tmp_path)
    tid = _pet(client)
    j = client.post(f"/author/{tid}/remedy-set/apply-to-chain",
                    json={"remedies": [FF, INFO]}).get_json()
    assert j["animal_refused"] == [FF] and j["added"] == 1
    assert all(is_infoceutical_remedy(r) for r in _layers(db, tid))


def test_a_layer_pick_refuses_an_ff_for_the_pet(tmp_path):
    client, db = _app(tmp_path)
    tid = _pet(client)
    j = client.post(f"/author/{tid}/layer/1/select", json={"remedy": FF}).get_json()
    assert j["ok"] is False and j["animal_refused"] == [FF]
    assert _layers(db, tid) == []


def _invoice_app(tmp_path):
    calls = {}

    def fake_create(customer, lines, **kw):
        calls["lines"] = lines
        return {"ok": True, "order_id": 7, "external_ref": "X", "total_cents": 100,
                "error": None, "accepted_slugs": []}

    client, db = _app(
        tmp_path,
        invoice_fetch_catalog=lambda: [{"slug": "immune-modulation", "name": FF},
                                       {"slug": "es1", "name": INFO}],
        invoice_create=fake_create,
        invoice_link=lambda oid: {"ok": True, "print_url": "", "error": None},
        invoice_latest=lambda email: {}, invoice_paid_check=lambda email: {})
    return client, db, calls


def _chain(db, tid, rems):
    from dashboard.biofield_authoring import add_chain_row
    with sqlite3.connect(db) as cx:
        for i, r in enumerate(rems, 1):
            add_chain_row(cx, tid, layer=i, head="", most_affected="", remedy=r)


def _slugs(calls):
    return [l.get("slug") for l in calls.get("lines") or []]


def test_the_invoice_draft_leaves_the_ff_off_for_the_pet(tmp_path):
    client, db, calls = _invoice_app(tmp_path)
    tid = _pet(client)
    _chain(db, tid, [FF, INFO])
    j = client.post(f"/author/{tid}/invoice", json={}).get_json()
    assert j["animal_refused"] == [FF]
    assert "immune-modulation" not in _slugs(calls) and "es1" in _slugs(calls)


def test_the_invoice_draft_keeps_the_ff_for_a_person(tmp_path):
    client, db, calls = _invoice_app(tmp_path)
    tid = _intake(client, "Ann Owner", OWNER)
    _chain(db, tid, [FF, INFO])
    j = client.post(f"/author/{tid}/invoice", json={}).get_json()
    assert j["animal_refused"] == []
    assert "immune-modulation" in _slugs(calls)


# ---- the photo ----------------------------------------------------------------------

def _photos(db):
    from dashboard import client_photos as cph
    with sqlite3.connect(db) as cx:
        cx.execute("CREATE TABLE IF NOT EXISTS fmp_snap_clients "
                   "(id_pk TEXT, name_first TEXT, name_last TEXT, email TEXT)")
        cx.executemany("INSERT INTO fmp_snap_clients VALUES (?,?,?,?)",
                       [("21", "Ann", "Owner", OWNER), ("22", "Rover (dog)", "Owner", OWNER)])
        cph.put(cx, OWNER, b"OWNER-PHOTO", "image/jpeg", source="portal-self")
        cph.put(cx, PET, b"PET-PHOTO", "image/jpeg", source="portal-self")
        cx.commit()


def test_the_pets_photo_shows_on_an_intake_under_the_owners_email(tmp_path):
    client, db = _app(tmp_path)
    _pet(client)
    _photos(db)
    r = client.get(f"/client-photo/{OWNER}?name=Rover%20Owner&cid=1002")
    assert r.status_code == 200 and r.data == b"PET-PHOTO"
    assert client.get(f"/client-photo-framing/{OWNER}?name=Rover%20Owner&cid=1002"
                      ).status_code == 200
    # Before Glen confirms, the page holds no id: the owner's email shows nothing.
    assert client.get(f"/client-photo/{OWNER}?name=Rover%20Owner").status_code == 404


def test_the_owners_photo_is_not_shown_for_the_pet(tmp_path):
    """The owner's email-keyed photo is a household photo: it must never stand in."""
    client, db = _app(tmp_path, e4l=_e4l(tmp_path, extra=[]))
    _photos(db)
    with sqlite3.connect(db) as cx:
        cx.execute("DELETE FROM client_photos WHERE email=?", (PET,))
    assert client.get(f"/client-photo/{OWNER}?name=Rover%20Owner").status_code == 404


def test_the_stored_client_id_finds_the_photo(tmp_path):
    client, db = _app(tmp_path)
    _photos(db)
    r = client.get(f"/client-photo/{OWNER}?name=Rover&cid=1002")
    assert r.status_code == 200 and r.data == b"PET-PHOTO"


# ---- scans: a shared email holds a person and an animal -----------------------------
# Coordinator, 2026-11-06: intake 64's scan panel and Import Reveal read Agnes's scans.
# Here both E4L records sit on ONE email, so the email cannot tell them apart.

ANN_SCAN, PET_SCAN = 9001, 9002


def _shared_e4l(tmp_path):
    p = str(tmp_path / "e4l.db")
    cx = sqlite3.connect(p)
    cx.execute("CREATE TABLE e4l_clients (client_id INTEGER PRIMARY KEY, name TEXT, "
               "email TEXT, species TEXT, animal_name TEXT)")
    cx.executemany("INSERT INTO e4l_clients VALUES (?,?,?,?,?)",
                   [(1001, "Ann Owner", OWNER, "Human", ""),
                    (1003, "Rover Owner", OWNER, "Dog", "Rover")])
    cx.execute("CREATE TABLE e4l_scans (scan_id INTEGER PRIMARY KEY, client_id INTEGER, "
               "scan_date TEXT)")
    # The person's scan is the newer, so an email read gives it to the dog too.
    cx.executemany("INSERT INTO e4l_scans VALUES (?,?,?)",
                   [(ANN_SCAN, 1001, "2026-11-03"), (PET_SCAN, 1003, "2026-11-01")])
    cx.execute("CREATE TABLE e4l_scan_results (id INTEGER PRIMARY KEY, scan_id INTEGER, "
               "item_code TEXT, priority_rank INTEGER)")
    cx.executemany("INSERT INTO e4l_scan_results (scan_id,item_code,priority_rank) "
                   "VALUES (?,?,?)", [(ANN_SCAN, "ED1", 1), (PET_SCAN, "ES9", 1)])
    cx.execute("CREATE TABLE e4l_items (code TEXT, name TEXT, full_name TEXT, "
               "e4l_description TEXT, category TEXT)")
    cx.commit()
    cx.close()
    return p


def _shared_app(tmp_path):
    from biofield_local_app import create_app
    db = str(tmp_path / "chat_log.db")
    app = create_app(db, e4l_db=_shared_e4l(tmp_path),
                     fetch_client_photo=lambda *a, **k: None,
                     fetch_runner=lambda **k: {"ok": True})
    return app.test_client(), db


def _panel_scan(client, tid):
    return client.get(f"/author/{tid}/e4l").get_json()["e4l"]


@pytest.mark.parametrize("name,scan_id,code", [("Ann Owner", ANN_SCAN, "ED1"),
                                               ("Rover Owner", PET_SCAN, "ES9")])
def test_the_scan_panel_shows_only_this_persons_scan(tmp_path, name, scan_id, code):
    client, _db = _shared_app(tmp_path)
    tid = _intake(client, name, OWNER)
    ctx = _panel_scan(client, tid)
    assert ctx["scan_id"] == scan_id
    assert [f["code"] for f in ctx["findings"]] == [code]


def test_the_stored_client_id_picks_the_scan(tmp_path):
    client, _db = _shared_app(tmp_path)
    tid = _intake(client, "Rover", OWNER, client_id="1003")
    assert _panel_scan(client, tid)["scan_id"] == PET_SCAN


def test_the_e4l_refresh_reads_this_persons_scan(tmp_path):
    client, _db = _shared_app(tmp_path)
    tid = _intake(client, "Rover Owner", OWNER)
    j = client.post(f"/author/{tid}/e4l/refresh", json={}).get_json()
    assert j["e4l"]["scan_id"] == PET_SCAN


@pytest.mark.parametrize("name,scan_id", [("Ann Owner", ANN_SCAN),
                                          ("Rover Owner", PET_SCAN)])
def test_import_reveal_synthesises_this_persons_scan(tmp_path, monkeypatch, name, scan_id):
    seen = {}

    def capture(email, scan_id=None, **k):
        seen["scan_id"] = scan_id
        return {"found": False}

    monkeypatch.setattr(RI, "synthesize_reveal_layers", capture)
    client, _db = _shared_app(tmp_path)
    tid = _intake(client, name, OWNER)
    client.post(f"/author/{tid}/e4l/import-reveal", json={})
    assert seen.get("scan_id") == scan_id


def test_an_email_only_intake_still_reads_the_email(tmp_path):
    """No name and no id: the email is the last fallback, as before: every client on it."""
    client, _db = _shared_app(tmp_path)
    tid = _intake(client, "", OWNER)
    assert _panel_scan(client, tid)["scan_id"] == ANN_SCAN


# ==== 2026-11-06 review findings ======================================================

def _e4l_rows(tmp_path, rows, scans=(), merges=()):
    """An e4l.db with these clients, scans (scan_id, client_id, date) and merges."""
    p = str(tmp_path / "e4l.db")
    cx = sqlite3.connect(p)
    cx.execute("CREATE TABLE e4l_clients (client_id INTEGER PRIMARY KEY, name TEXT, "
               "email TEXT, species TEXT, animal_name TEXT)")
    cx.executemany("INSERT INTO e4l_clients VALUES (?,?,?,?,?)", rows)
    cx.execute("CREATE TABLE e4l_scans (scan_id INTEGER PRIMARY KEY, client_id INTEGER, "
               "scan_date TEXT)")
    cx.executemany("INSERT INTO e4l_scans VALUES (?,?,?)", scans)
    cx.execute("CREATE TABLE e4l_scan_results (id INTEGER PRIMARY KEY, scan_id INTEGER, "
               "item_code TEXT, priority_rank INTEGER)")
    cx.execute("CREATE TABLE e4l_items (code TEXT, name TEXT, full_name TEXT, "
               "e4l_description TEXT, category TEXT)")
    if merges:
        cx.execute("CREATE TABLE e4l_identity_merges (dup_client_id INTEGER, "
                   "canonical_client_id INTEGER)")
        cx.executemany("INSERT INTO e4l_identity_merges VALUES (?,?)", merges)
    cx.commit()
    cx.close()
    return p


def _real_app(tmp_path, e4l, **kw):
    from biofield_local_app import create_app
    db = str(tmp_path / "chat_log.db")
    app = create_app(db, e4l_db=e4l, fetch_client_photo=lambda *a, **k: None,
                     fetch_runner=lambda **k: {"ok": True}, **kw)
    return app.test_client(), db


# -- 1. two clients of one name on one email ---------------------------------------

SAM = [(1005, "Sam Owner", OWNER, "Human", ""), (1006, "Sam Owner", OWNER, "Dog", "Sam")]


def test_two_different_people_of_one_name_on_one_email_resolve_to_nobody(tmp_path):
    p = _e4l_rows(tmp_path, SAM)
    res = resolve_intake_subject(p, None, OWNER, "Sam Owner")
    assert res["by"] == "unresolved" and res["ambiguous"] is True
    assert res["species"] is None              # the owner is asked, never hits[0]'s Dog
    client, _db = _real_app(tmp_path, p)
    tid = _intake(client, "Sam Owner", OWNER)
    j = client.post(f"/author/{tid}/remedy-set/add-one", json={"remedy": FF}).get_json()
    assert j.get("needs_species") is True


def test_two_rows_of_one_name_in_one_merge_group_are_one_person(tmp_path):
    rows = [(1005, "Sam Owner", OWNER, "Dog", "Sam"), (1006, "Sam Owner", OWNER, "Dog", "Sam")]
    p = _e4l_rows(tmp_path, rows, merges=[(1006, 1005)])
    res = resolve_intake_subject(p, None, OWNER, "Sam Owner")
    assert res["by"] == "email+name" and res["species"] == "Dog"


def test_the_stored_id_settles_two_of_one_name(tmp_path):
    p = _e4l_rows(tmp_path, SAM)
    assert species_for_intake(p, "1005", OWNER, "Sam Owner") == "Human"


# -- 2. the namesake on another email waits for Glen's Confirm ---------------------

def test_the_page_offers_the_namesake_and_confirm_stores_it(tmp_path):
    client, db = _app(tmp_path)
    tid = _intake(client, "Rover Owner", OWNER)
    page = client.get(f"/author/{tid}").get_data(as_text=True)
    assert "Is this Rover, Dog, on another email?" in page
    assert "confirmE4LClient('1002')" in page
    # Unconfirmed: species unknown, so the + button asks.
    assert client.post(f"/author/{tid}/remedy-set/add-one",
                       json={"remedy": FF}).get_json().get("needs_species") is True
    bad = client.post(f"/author/{tid}/e4l-client/confirm", json={"client_id": 1001})
    assert bad.status_code == 409
    assert client.post(f"/author/{tid}/e4l-client/confirm",
                       json={"client_id": 1002}).get_json() == {"ok": True,
                                                                "client_id": "1002"}
    row = sqlite3.connect(db).execute("SELECT e4l_client_id FROM biofield_auth_tests "
                                      "WHERE id=?", (int(tid.lstrip("a")),)).fetchone()
    assert row[0] == "1002"
    assert "on another email?" not in client.get(f"/author/{tid}").get_data(as_text=True)


# -- 3. a name that matches nobody on its email does not borrow the owner -----------

def test_a_name_matching_nobody_on_the_email_is_not_the_owner(tmp_path):
    client, db = _shared_app(tmp_path)
    p = str(tmp_path / "e4l.db")
    assert species_for_intake(p, None, OWNER, "Kitty Owner") is None
    assert resolve_intake_subject(p, None, OWNER, "Kitty Owner")["by"] == "unresolved"
    tid = _intake(client, "Kitty Owner", OWNER)
    ctx = _panel_scan(client, tid)
    assert ctx["found"] is False and ctx["scan_id"] is None
    _photos(db)
    assert client.get(f"/client-photo/{OWNER}?name=Kitty%20Owner").status_code == 404


# -- 4. species answers are per subject ---------------------------------------------

BLANK = [(1001, "Ann Owner", OWNER, "", ""), (1003, "Rover Owner", OWNER, "", "Rover")]


def test_an_answer_for_the_pet_does_not_answer_for_the_owner(tmp_path):
    p = _e4l_rows(tmp_path, BLANK)
    client, _db = _real_app(tmp_path, p)
    pet = _intake(client, "Rover Owner", OWNER)
    ann = _intake(client, "Ann Owner", OWNER)
    assert client.post(f"/author/{pet}/species", json={"species": "dog"}).get_json() == \
        {"ok": True, "species": "Dog"}
    assert species_for_intake(p, None, OWNER, "Rover Owner") == "Dog"
    assert species_for_intake(p, None, OWNER, "Ann Owner") is None
    assert client.post(f"/author/{ann}/remedy-set/add-one",
                       json={"remedy": FF}).get_json().get("needs_species") is True


def test_an_unresolved_subjects_answer_is_kept_by_email_and_name(tmp_path):
    p = _e4l_rows(tmp_path, [(1001, "Ann Owner", OWNER, "Human", "")])
    client, _db = _real_app(tmp_path, p)
    tid = _intake(client, "Kitty Owner", OWNER)
    client.post(f"/author/{tid}/species", json={"species": "Cat"})
    assert species_for_intake(p, None, OWNER, "Kitty Owner") == "Cat"
    assert species_for_intake(p, None, OWNER, "Other Owner") is None
    assert species_for_intake(p, None, OWNER, "Ann Owner") == "Human"


def test_an_old_email_only_answer_applies_only_to_a_lone_client(tmp_path):
    """An email-only row from before the migration, on the old table shape."""
    p = _e4l_rows(tmp_path, BLANK + [(1009, "Solo Person", "solo@x.com", "", "")])
    cx = sqlite3.connect(p)
    cx.execute("CREATE TABLE species_answers (email TEXT PRIMARY KEY, species TEXT NOT NULL, "
               "answered_at TEXT NOT NULL)")
    cx.executemany("INSERT INTO species_answers VALUES (?,?,'2026-10-01')",
                   [(OWNER, "Dog"), ("solo@x.com", "Human")])
    cx.commit()
    cx.close()
    assert species_for_intake(p, None, OWNER, "Ann Owner") is None     # two on the email
    assert species_for_intake(p, None, "solo@x.com", "Solo Person") == "Human"
    assert species_for_intake(p, None, "solo@x.com", "") == "Human"
    assert species_for_intake(p, None, "solo@x.com", "Someone Else") is None
    # The migration is additive: the old rows survive a new answer.
    record_species_answer(p, OWNER, "Human", client_id=1001)
    cols = {r[1] for r in sqlite3.connect(p).execute("PRAGMA table_info(species_answers)")}
    assert {"client_id", "subject_email", "name_key"} <= cols
    assert species_for_intake(p, None, "solo@x.com", "Solo Person") == "Human"
    assert species_for_intake(p, None, OWNER, "Ann Owner") == "Human"
    assert species_for_intake(p, None, OWNER, "Rover Owner") is None


# -- 5. the page sends and keeps the client id --------------------------------------

def _save_header_post(h_client_id):
    """Run the page's own saveHeader under node; return the body it posts."""
    import json
    import re
    import shutil
    import subprocess
    from dashboard import biofield_report_html as H
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    fn = re.search(r"async function saveHeader\(\)\{.*?\n \}?astat\('Header saved\.'\);setE4L\(j\)\}",
                   H._AUTHOR_JS, re.S).group(0)
    js = """
var els={h_name:{value:'Rover Owner'},h_email:{value:'owner@x.com'},h_date:{value:'2026-11-06'},
         h_client_id:{value:%(cid)s}};
var document={getElementById:function(i){return els[i]||null}};
function val(i){return els[i]?els[i].value:''}
function set(i,v){if(els[i])els[i].value=v}
function orderToken(){return 'tok'}
function astat(){} function setE4L(){}
var sent=null;
async function post(p,b){sent={path:p,body:b};return {ok:true,client_id:'1002'}}
%(fn)s
saveHeader().then(function(){console.log(JSON.stringify({sent:sent,kept:els.h_client_id.value}))});
""" % {"cid": json.dumps(h_client_id), "fn": fn}
    out = subprocess.run([node, "-e", js], capture_output=True, text=True, timeout=20)
    assert out.returncode == 0, out.stderr[-1500:]
    return json.loads(out.stdout.strip().splitlines()[-1])


def test_the_page_posts_the_client_id_it_holds_and_keeps_the_reply():
    r = _save_header_post("1002")
    assert r["sent"]["path"] == "/author/__TID__/header"
    assert r["sent"]["body"]["client_id"] == "1002"
    assert r["kept"] == "1002"


def test_what_the_page_posts_stores_the_id(tmp_path):
    """The body saveHeader really sends, posted to the route."""
    client, db = _app(tmp_path)
    tid = client.post("/author/new").headers["Location"].rstrip("/").split("/")[-1]
    body = _save_header_post("1002")["sent"]["body"]
    j = client.post(f"/author/{tid}/header", json=body).get_json()
    assert j["client_id"] == "1002"
    assert species_for_intake(str(tmp_path / "e4l.db"), "1002", OWNER,
                              "Rover Owner") == "Dog"
    page = client.get(f"/author/{tid}").get_data(as_text=True)
    assert 'id=h_client_id value="1002"' in page


def test_a_match_on_the_email_by_name_is_stored_and_a_new_name_drops_it(tmp_path):
    client, db = _shared_app(tmp_path)
    tid = _intake(client, "Rover Owner", OWNER)

    def stored():
        return sqlite3.connect(db).execute(
            "SELECT e4l_client_id FROM biofield_auth_tests WHERE id=?",
            (int(tid.lstrip("a")),)).fetchone()[0]
    assert stored() == "1003"
    # A different name with no id: the old id must not win.
    j = client.post(f"/author/{tid}/header", json={"name": "Ann Owner", "email": OWNER,
                                                   "date": "2026-11-06"}).get_json()
    assert stored() == "1001" and j["client_id"] == "1001"
    assert _panel_scan(client, tid)["scan_id"] == ANN_SCAN


def test_typing_a_name_by_hand_drops_the_held_id(tmp_path):
    from dashboard import biofield_report_html as H
    assert "function dropClientId(){E4L_CLIENT_ID=null;set('h_client_id','')}" in H._AUTHOR_JS
    client, _db = _app(tmp_path)
    tid = _intake(client, "Ann Owner", OWNER)
    page = client.get(f"/author/{tid}").get_data(as_text=True)
    assert 'id=h_name autocomplete=off oninput="dropClientId();nameSearch()"' in page
    assert 'id=h_email autocomplete=off oninput="dropClientId();emailSearch()"' in page


# -- 6. an unresolved named subject reads no scan and no reveal ---------------------

def test_an_unresolved_subject_gets_no_reveal(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(RI, "synthesize_reveal_layers",
                        lambda *a, **k: calls.append(k) or {"found": False})
    client, _db = _shared_app(tmp_path)
    tid = _intake(client, "Kitty Owner", OWNER)
    client.post(f"/author/{tid}/species", json={"species": "Cat"})
    j = client.post(f"/author/{tid}/e4l/import-reveal", json={}).get_json()
    assert calls == [] and j.get("ok") is not True


# -- 7. the resolved client id wins over the email and name -------------------------

def test_the_resolved_id_wins_over_a_photo_the_email_and_name_find(tmp_path):
    from dashboard import client_photos as cph
    client, db = _app(tmp_path)
    _photos(db)
    with sqlite3.connect(db) as cx:
        cph.put_for_client(cx, "21", OWNER, b"ANN-PHOTO", "image/jpeg", source="portal-self")
    # Ann Owner on OWNER is a FileMaker person with her own photo, so the email and
    # name find it. The stored id says the dog, and the id wins.
    assert client.get(f"/client-photo/{OWNER}?name=Ann%20Owner").data == b"ANN-PHOTO"
    r = client.get(f"/client-photo/{OWNER}?name=Ann%20Owner&cid=1002")
    assert r.status_code == 200 and r.data == b"PET-PHOTO"


# -- 8. the invoice --------------------------------------------------------------------

def test_an_ff_on_the_open_draft_is_left_off_for_the_pet(tmp_path):
    calls = {}

    def fake_create(customer, lines, **kw):
        calls["lines"] = lines
        return {"ok": True, "order_id": 7, "external_ref": "X", "total_cents": 100,
                "error": None, "accepted_slugs": []}

    previous = {"ok": True, "order_id": 7, "status": "open", "pay_status": "",
                "items": [{"slug": "immune-modulation", "qty": 1, "source": "manual"}]}
    client, db = _app(
        tmp_path,
        invoice_fetch_catalog=lambda: [{"slug": "immune-modulation", "name": FF},
                                       {"slug": "es1", "name": INFO}],
        invoice_create=fake_create,
        invoice_link=lambda oid: {"ok": True, "print_url": "", "error": None},
        invoice_latest=lambda email: previous, invoice_paid_check=lambda email: {})
    tid = _pet(client)
    _chain(db, tid, [INFO])
    j = client.post(f"/author/{tid}/invoice", json={}).get_json()
    assert "immune-modulation" not in _slugs(calls) and "es1" in _slugs(calls)
    assert j["animal_refused"] == [FF] and FF in j["warning"]


def test_an_unknown_species_blocks_ffs_on_the_invoice(tmp_path):
    calls = {}

    def fake_create(customer, lines, **kw):
        calls["lines"] = lines
        return {"ok": True, "order_id": 7, "external_ref": "X", "total_cents": 100,
                "error": None, "accepted_slugs": []}

    client, db = _app(
        tmp_path, e4l=str(tmp_path / "missing-e4l.db"),
        invoice_fetch_catalog=lambda: [{"slug": "immune-modulation", "name": FF},
                                       {"slug": "es1", "name": INFO}],
        invoice_create=fake_create,
        invoice_link=lambda oid: {"ok": True, "print_url": "", "error": None},
        invoice_latest=lambda email: {}, invoice_paid_check=lambda email: {})
    tid = _intake(client, "Ann Owner", OWNER)
    _chain(db, tid, [FF, INFO])
    j = client.post(f"/author/{tid}/invoice", json={}).get_json()
    assert j["species_unknown"] is True and j["animal_refused"] == [FF]
    assert "immune-modulation" not in _slugs(calls) and "es1" in _slugs(calls)
    assert "species is unknown" in j["warning"]


# -- 9. a person's scans on an older row of the same name ---------------------------

def test_scans_on_an_older_row_of_the_same_name_are_read(tmp_path):
    rows = [(1001, "Ann Owner", OWNER, "Human", ""), (1007, "Ann Owner", OWNER, "Human", ""),
            (1003, "Rover Owner", OWNER, "Dog", "Rover")]
    p = _e4l_rows(tmp_path, rows, scans=[(9001, 1001, "2026-11-01"),
                                         (9002, 1003, "2026-11-04")])
    client, _db = _real_app(tmp_path, p)
    tid = _intake(client, "Ann Owner", OWNER, client_id="1007")
    # Ann's own older row, never the dog's newer scan.
    assert _panel_scan(client, tid)["scan_id"] == 9001


# -- 10. a whole infoceutical name, after resolution --------------------------------

@pytest.mark.parametrize("name,ok", [
    (INFO, True),
    ("Day Infoceutical", True),
    ("EI13 Hearing & Learning Energetic Integrator Infoceutical", True),
    (f"{INFO} + Day Infoceutical", True),
    (f"{FF} + {INFO}", False),
    ("Immune Modulation Infoceutical", False),
    ("ES1 Infoceutical", False),
    (FF, False),
])
def test_only_whole_infoceutical_names_pass(name, ok):
    assert is_infoceutical_remedy(name) is ok


def test_the_layer_pick_checks_the_name_that_is_stored(tmp_path, monkeypatch):
    import biofield_local_app as BLA
    monkeypatch.setattr(BLA, "resolve_remedy_name",
                        lambda cx, n: FF if n == INFO.lower() else n)
    monkeypatch.setattr(BLA, "remedy_dosing", lambda cx, n: {})
    client, db = _app(tmp_path)
    tid = _pet(client)
    j = client.post(f"/author/{tid}/layer/1/select",
                    json={"remedy": INFO.lower()}).get_json()
    assert j["ok"] is False and j["animal_refused"] == [FF]
    j = client.post(f"/author/{tid}/remedy-set/add-one", json={"remedy": INFO.lower()}).get_json()
    assert j["ok"] is False and j["animal_refused"] == [FF]
    assert _layers(db, tid) == []


# -- 11. the other ways into the chain ----------------------------------------------

def test_a_hand_typed_row_for_the_pet_leaves_the_ff_off(tmp_path):
    client, db = _app(tmp_path)
    tid = _pet(client)
    j = client.post(f"/author/{tid}/row", json={"layer": 1, "head": "Liver",
                                               "remedy": FF, "dosage": "2 caps"}).get_json()
    assert j["animal_refused"] == [FF]
    assert _layers(db, tid) == [""]
    rid = j["rid"]
    j = client.post(f"/author/{tid}/row/{rid}", json={"remedy": FF, "head": "Kidney"}).get_json()
    assert j["animal_refused"] == [FF]
    row = sqlite3.connect(db).execute("SELECT head, remedy, dosage FROM biofield_auth_chain "
                                      "WHERE id=?", (rid,)).fetchone()
    assert row == ("Kidney", "", "")
    client.post(f"/author/{tid}/row/{rid}", json={"remedy": INFO})
    assert _layers(db, tid) == [INFO]


def test_a_persons_hand_typed_ff_is_kept(tmp_path):
    client, db = _app(tmp_path)
    tid = _intake(client, "Ann Owner", OWNER)
    j = client.post(f"/author/{tid}/row", json={"layer": 1, "remedy": FF}).get_json()
    assert "animal_refused" not in j and _layers(db, tid) == [FF]


def test_interpret_leaves_the_ff_off_for_the_pet(tmp_path, monkeypatch):
    import biofield_local_app as BLA
    from dashboard.biofield_narrative import save_notes
    monkeypatch.setattr(BLA, "interpret_transcript", lambda *a, **k: {"layers": [
        {"layer": 1, "head": "Liver", "most_affected": "Liver", "remedy": FF},
        {"layer": 2, "head": "Lymph", "most_affected": "Lymph", "remedy": INFO}]})
    monkeypatch.setattr(BLA, "resolve_remedy_name", lambda cx, n: n)
    client, db = _app(tmp_path)
    tid = _pet(client)
    with sqlite3.connect(db) as cx:
        save_notes(cx, tid, "spoken session")
    j = client.post(f"/author/{tid}/interpret", json={}).get_json()
    assert j["animal_refused"] == [FF] and j["added"] == 2
    assert _layers(db, tid) == ["", INFO]


def test_the_program_apply_leaves_the_ff_off_for_the_pet(tmp_path, monkeypatch):
    import biofield_local_app as BLA
    from dashboard import biofield_program as BP
    monkeypatch.setattr(BLA, "resolve_remedy_name", lambda cx, n: n)
    monkeypatch.setattr(BP, "build_program", lambda **k: {"remedies": [FF, INFO], "stages": [
        {"stage": "mind", "picks": [{"remedy": FF, "covers": []},
                                    {"remedy": INFO, "covers": []}],
         "suppressed": [], "uncovered": []}]})
    client, db = _app(tmp_path)
    tid = _pet(client)
    j = client.post(f"/author/{tid}/program",
                    json={"apply": True, "mode": "full",
                          "include": {"spirit": False}}).get_json()
    assert j["applied"] is True and j["animal_refused"] == [FF]
    assert _layers(db, tid) == [INFO]


def test_a_clinical_balance_leaves_the_ff_off_for_the_pet(tmp_path, monkeypatch):
    import biofield_local_app as BLA
    monkeypatch.setattr(BLA, "resolve_remedy_name", lambda cx, n: n)
    client, db = _app(tmp_path)
    tid = _pet(client)
    j = client.post(f"/author/{tid}/clinical-items/balance",
                    json={"label": "Itchy skin", "layer": 1,
                          "remedies": [FF, INFO]}).get_json()
    assert j["animal_refused"] == [FF]
    assert FF not in _layers(db, tid) and INFO in _layers(db, tid)


# -- 12. one resolution per request, and no full-name scan on a normal request ------

def test_one_resolution_per_request_and_no_name_scan(tmp_path, monkeypatch):
    from dashboard import biofield_e4l as BE
    real = BE.resolve_intake_subject
    seen = {"resolve": 0, "suggest": 0}

    def counting(*a, **k):
        seen["resolve"] += 1
        return real(*a, **k)

    def no_scan(*a, **k):
        seen["suggest"] += 1
        return None
    client, db = _app(tmp_path)
    tid = _pet(client)
    monkeypatch.setattr(BE, "resolve_intake_subject", counting)
    monkeypatch.setattr(BE, "suggest_intake_client", no_scan)
    client.post(f"/author/{tid}/remedy-set/apply-to-chain", json={"remedies": [FF, INFO]})
    assert seen == {"resolve": 1, "suggest": 0}
    seen["resolve"] = 0
    client.get(f"/author/{tid}")
    assert seen["suggest"] == 0              # resolved by id: nothing to offer


def test_a_first_name_typed_alone_finds_its_own_client_but_a_surname_does_not(tmp_path):
    """Finding 3 keeps a short form: "Sasha" on Sasha Takahashi's email is Sasha. An
    owner's surname alone, or a pet's full name, on her email is not her."""
    p = _e4l_rows(tmp_path, [(1001, "Agnes Verches", OWNER, "Human", "")])
    assert resolve_intake_subject(p, None, OWNER, "Agnes")["by"] == "email+first"
    assert species_for_intake(p, None, OWNER, "Agnes") == "Human"
    assert resolve_intake_subject(p, None, OWNER, "Verches")["by"] == "unresolved"
    assert species_for_intake(p, None, OWNER, "Skylar Verches") is None


# ==== 2026-11-06 review, round 3 ======================================================
# E4L stores a pet with animal_name set, and its `name` is often the OWNER's. A record
# with an animal_name goes by it; one without goes by `name`.

AGNES = "agnes@x.com"


def _household(tmp_path, pet_species=""):
    """Agnes and her dog Skylar on one email; the dog's record carries Agnes's name."""
    return _e4l_rows(
        tmp_path,
        [(1001, "Agnes Verches", AGNES, "Human", ""),
         (1003, "Agnes Verches", AGNES, pet_species, "Skylar")],
        scans=[(9001, 1001, "2026-11-04"), (9003, 1003, "2026-11-01")])


# -- 1. one kind of subject per union; a pet is never its owner ---------------------

def test_a_stored_pet_id_never_reads_its_owners_scans(tmp_path):
    client, _db = _real_app(tmp_path, _household(tmp_path))
    tid = _intake(client, "Skylar", AGNES, client_id="1003")
    assert _panel_scan(client, tid)["scan_id"] == 9003


def test_the_owner_and_the_pet_on_one_email_each_resolve_to_themselves(tmp_path):
    p = _household(tmp_path, pet_species="Dog")
    owner = resolve_intake_subject(p, None, AGNES, "Agnes Verches")
    assert owner["client"]["client_id"] == 1001 and owner["scan_ids"] == (1001,)
    pet = resolve_intake_subject(p, None, AGNES, "Skylar Verches")
    assert pet["client"]["client_id"] == 1003 and pet["species"] == "Dog"
    assert resolve_intake_subject(p, None, AGNES, "Skylar")["client"]["client_id"] == 1003


def test_two_unmerged_rows_of_one_person_are_one_person(tmp_path):
    """Finding 9 again, with no client id posted: the scans on the older row count."""
    rows = [(1001, "Ann Owner", OWNER, "Human", ""), (1007, "Ann Owner", OWNER, "Human", ""),
            (1003, "Rover Owner", OWNER, "Dog", "Rover")]
    p = _e4l_rows(tmp_path, rows, scans=[(9001, 1001, "2026-11-01"),
                                         (9002, 1003, "2026-11-04")])
    client, _db = _real_app(tmp_path, p)
    tid = _intake(client, "Ann Owner", OWNER)
    assert _panel_scan(client, tid)["scan_id"] == 9001


# -- 2. a stored pet id never inherits the owner's or the intake email's answer -----

def test_a_pet_reads_only_its_own_species_answer(tmp_path):
    p = _e4l_rows(tmp_path, [(1001, "Ann Owner", OWNER, "", ""),
                             (1002, "Ann Owner", PET, "", "Rover")])
    record_species_answer(p, OWNER, "Human")                          # old email-only
    record_species_answer(p, OWNER, "Human", name="Rover Owner")      # the intake's email
    record_species_answer(p, OWNER, "Human", client_id=1001)          # the owner
    assert species_for_intake(p, "1002", OWNER, "Rover Owner") is None
    record_species_answer(p, PET, "Dog", name="Rover")                # its own email+name
    assert species_for_intake(p, "1002", OWNER, "Rover Owner") == "Dog"


# -- 3. an old email-only answer holds for an email with no E4L client --------------

def test_an_old_answer_holds_for_a_person_with_no_e4l_record(tmp_path):
    p = _e4l_rows(tmp_path, [(1001, "Ann Owner", OWNER, "Human", "")])
    record_species_answer(p, "new@x.com", "Human")
    assert species_for_intake(p, None, "new@x.com", "New Person") == "Human"


# -- 4. an unknown species keeps FFs off every write, and an animal off the portal --

def _unknown_app(tmp_path, **kw):
    """No e4l.db at all: the read fails, so nobody knows the species."""
    return _app(tmp_path, e4l=str(tmp_path / "missing-e4l.db"), **kw)


def test_a_name_that_does_not_match_tells_the_operator(tmp_path):
    """Bob for Robert: the FF is left off, and the reply says why."""
    p = _e4l_rows(tmp_path, [(1001, "Robert Smith", OWNER, "", "")])
    client, db = _real_app(tmp_path, p)
    tid = _intake(client, "Bob Smith", OWNER)
    j = client.post(f"/author/{tid}/row", json={"layer": 1, "remedy": FF}).get_json()
    assert j["animal_refused"] == [FF] and j["species_unknown"] is True
    assert "species is unknown" in j["error"]


def test_an_unknown_species_keeps_ffs_off_rows_interpret_balance_and_program(
        tmp_path, monkeypatch):
    import biofield_local_app as BLA
    from dashboard import biofield_program as BP
    from dashboard.biofield_narrative import save_notes
    monkeypatch.setattr(BLA, "resolve_remedy_name", lambda cx, n: n)
    monkeypatch.setattr(BLA, "interpret_transcript", lambda *a, **k: {"layers": [
        {"layer": 1, "head": "Liver", "most_affected": "Liver", "remedy": FF}]})
    monkeypatch.setattr(BP, "build_program", lambda **k: {"remedies": [FF], "stages": [
        {"stage": "mind", "picks": [{"remedy": FF, "covers": []}],
         "suppressed": [], "uncovered": []}]})
    client, db = _unknown_app(tmp_path)
    tid = _intake(client, "Ann Owner", OWNER)
    with sqlite3.connect(db) as cx:
        save_notes(cx, tid, "spoken")
    replies = [
        client.post(f"/author/{tid}/row", json={"layer": 1, "remedy": FF}).get_json(),
        client.post(f"/author/{tid}/interpret", json={}).get_json(),
        client.post(f"/author/{tid}/clinical-items/balance",
                    json={"label": "Itch", "layer": 1, "remedies": [FF]}).get_json(),
        client.post(f"/author/{tid}/program", json={"apply": True, "force": True,
                                                    "include": {"spirit": False}}).get_json()]
    for j in replies:
        assert j["animal_refused"] == [FF] and j["species_unknown"] is True, j
    assert FF not in _layers(db, tid)


def test_an_unknown_species_keeps_ffs_off_the_layer_sync(tmp_path, monkeypatch):
    from dashboard import biofield_e4l as BE
    from tests.test_clinical_layer_sync import _app as sync_app, _apply, _chain as sync_chain
    from tests.test_clinical_layer_sync import _gut_by_hand, _preview
    monkeypatch.setattr(BE, "species_from_e4l", lambda e4l_db, email: None)
    db, tid, client = sync_app(tmp_path, monkeypatch, ["Gut"], _gut_by_hand)
    j = _preview(client, tid)
    r = _apply(client, tid, j, [c["id"] for c in j["changes"]]).get_json()
    assert r["animal_refused"] == ["Transform"] and r["species_unknown"] is True
    assert "Transform" not in {row[3] for row in sync_chain(db, tid)}


def test_an_ff_already_on_the_pets_chain_never_reaches_her_portal(tmp_path, monkeypatch):
    monkeypatch.setenv("PORTAL_PUBLISH_BASE_URL", "http://portal.invalid")
    client, db = _app(tmp_path)
    tid = _pet(client)
    _chain(db, tid, [FF, INFO])               # written before the rule existed
    r = client.post(f"/test/{tid}/publish-portal", json={})
    j = r.get_json()
    assert r.status_code == 409 and j["animal_refused"] == [FF]
    assert "Not published" in j["error"] and FF in j["error"]


def test_an_unknown_species_blocks_the_publish_too(tmp_path, monkeypatch):
    monkeypatch.setenv("PORTAL_PUBLISH_BASE_URL", "http://portal.invalid")
    client, db = _unknown_app(tmp_path)
    tid = _intake(client, "Ann Owner", OWNER)
    _chain(db, tid, [FF])
    j = client.post(f"/test/{tid}/publish-portal", json={}).get_json()
    assert j["species_unknown"] is True and j["animal_refused"] == [FF]


def test_the_invoice_pay_card_is_checked_for_an_ff(tmp_path):
    published = []
    latest = {"ok": True, "order_id": 7, "status": "open", "pay_status": "",
              "items": [{"slug": "immune-modulation", "qty": 1}, {"slug": "es1", "qty": 1}]}
    client, db = _app(
        tmp_path,
        invoice_fetch_catalog=lambda: [{"slug": "immune-modulation", "name": FF},
                                       {"slug": "es1", "name": INFO}],
        invoice_latest=lambda email: latest,
        invoice_publish=lambda oid: published.append(oid) or {"ok": True})
    tid = _pet(client)
    r = client.post(f"/author/{tid}/invoice/publish", json={"order_id": 7})
    assert r.status_code == 409 and r.get_json()["animal_refused"] == [FF]
    r = client.post(f"/author/{tid}/invoice/publish", json={"order_id": 8})
    assert r.status_code == 409 and "could not be checked" in r.get_json()["error"]
    latest["items"] = [{"slug": "es1", "qty": 1}]
    assert client.post(f"/author/{tid}/invoice/publish",
                       json={"order_id": 7}).get_json() == {"ok": True}
    assert published == [7]


# -- 5. an open draft on a shared email that is someone else's is left alone ---------

def _shared_invoice_app(tmp_path, latest):
    calls = {}

    def fake_create(customer, lines, **kw):
        calls["lines"], calls["kw"] = lines, kw
        return {"ok": True, "order_id": 7, "external_ref": "X", "total_cents": 100,
                "error": None, "accepted_slugs": []}
    client, db = _app(
        tmp_path,
        invoice_fetch_catalog=lambda: [{"slug": "immune-modulation", "name": FF},
                                       {"slug": "es1", "name": INFO}],
        invoice_create=fake_create,
        invoice_link=lambda oid: {"ok": True, "print_url": "", "error": None},
        invoice_latest=lambda email: latest, invoice_paid_check=lambda email: {},
        order_fetch=lambda ids: ([], None))
    return client, db, calls


def test_the_pet_never_edits_the_owners_open_draft(tmp_path):
    from dashboard import biofield_progress as bp
    latest = {"ok": True, "order_id": 7, "status": "open", "pay_status": "",
              "items": [{"slug": "immune-modulation", "qty": 1, "source": "biofield"}]}
    client, db, calls = _shared_invoice_app(tmp_path, latest)
    ann = _intake(client, "Ann Owner", OWNER)
    pet = _pet(client)
    with sqlite3.connect(db) as cx:
        bp.link_order(cx, ann, 7)
    _chain(db, pet, [INFO])
    r = client.post(f"/author/{pet}/invoice", json={})
    assert r.status_code == 409 and "Ann Owner" in r.get_json()["error"]
    assert calls == {}
    # Ann raising her own draft still updates it.
    _chain(db, ann, [FF])
    assert client.post(f"/author/{ann}/invoice", json={}).get_json()["ok"] is True
    assert calls["kw"]["update_order_id"] == 7


def test_an_unstored_draft_on_a_shared_email_is_left_alone(tmp_path):
    latest = {"ok": True, "order_id": 7, "status": "open", "pay_status": "", "items": []}
    client, db, calls = _shared_invoice_app(tmp_path, latest)
    _intake(client, "Ann Owner", OWNER)
    pet = _pet(client)
    _chain(db, pet, [INFO])
    assert client.post(f"/author/{pet}/invoice", json={}).status_code == 409
    assert calls == {}


# -- 6. the photo is read by the resolved client's own name and email ---------------

def test_the_photo_uses_the_pets_own_name_never_the_header_name(tmp_path):
    from dashboard import client_photos as cph
    client, db = _app(tmp_path)
    with sqlite3.connect(db) as cx:
        cx.execute("CREATE TABLE IF NOT EXISTS fmp_snap_clients "
                   "(id_pk TEXT, name_first TEXT, name_last TEXT, email TEXT)")
        cx.executemany("INSERT INTO fmp_snap_clients VALUES (?,?,?,?)",
                       [("31", "Rover", "", PET), ("32", "Ann", "Owner", PET)])
        cph.put_for_client(cx, "31", PET, b"ROVER", "image/jpeg", source="portal-self")
        cph.put_for_client(cx, "32", PET, b"ANN", "image/jpeg", source="portal-self")
        cx.commit()
    r = client.get(f"/client-photo/{OWNER}?name=Ann%20Owner&cid=1002")
    assert r.status_code == 200 and r.data == b"ROVER"


# -- 7. a stale tab cannot confirm over a newer pick ---------------------------------

def test_confirm_refuses_when_the_page_holds_an_old_id(tmp_path):
    client, db = _app(tmp_path)
    tid = _intake(client, "Rover Owner", OWNER)
    r = client.post(f"/author/{tid}/e4l-client/confirm",
                    json={"client_id": 1002, "held": "1001"})
    assert r.status_code == 409 and "out of date" in r.get_json()["error"]
    assert client.post(f"/author/{tid}/e4l-client/confirm",
                       json={"client_id": 1002, "held": ""}).get_json()["ok"] is True
    from dashboard import biofield_report_html as H
    assert "{client_id:cid,held:val('h_client_id')}" in H._AUTHOR_JS


# -- 8. a resolved subject never reads scans by the email ----------------------------

def test_a_resolved_subject_never_falls_back_to_the_email(tmp_path):
    from biofield_local_app import create_app
    db = str(tmp_path / "chat_log.db")
    app = create_app(db, e4l_db=_e4l(tmp_path), fetch_client_photo=lambda *a, **k: None,
                     scan_lookup=lambda e: {"found": True, "scan_id": 5, "layers": []})
    app.testing = False
    client = app.test_client()
    tid = _intake(client, "Rover", OWNER, client_id="1002")
    r = client.get(f"/author/{tid}/e4l")
    assert r.status_code == 200
    assert r.get_json()["e4l"]["found"] is False and r.get_json()["e4l"]["scan_id"] is None


# -- 9. a pet's one-word name is a suggestion -----------------------------------------

def test_a_pets_one_word_name_is_suggested(tmp_path):
    p = _e4l(tmp_path)
    assert suggest_intake_client(p, OWNER, "Rover")["client_id"] == 1002
    assert intake_e4l_client(p, None, OWNER, "Rover") is None
    assert suggest_intake_client(p, OWNER, "Ann") is None       # a person's first name



# ==== 2026-11-06 outside review of 539eefb3 ===========================================

def test_the_pet_never_publishes_the_owners_order(tmp_path):
    from dashboard import biofield_progress as bp
    published = []
    latest = {"ok": True, "order_id": 7, "status": "open", "pay_status": "",
              "items": [{"slug": "es1", "qty": 1}]}
    client, db = _app(
        tmp_path, invoice_fetch_catalog=lambda: [{"slug": "es1", "name": INFO}],
        invoice_latest=lambda email: latest,
        invoice_publish=lambda oid: published.append(oid) or {"ok": True})
    ann = _intake(client, "Ann Owner", OWNER)
    pet = _pet(client)
    with sqlite3.connect(db) as cx:
        bp.link_order(cx, ann, 7)
    r = client.post(f"/author/{pet}/invoice/publish", json={"order_id": 7})
    assert r.status_code == 409 and "Ann Owner" in r.get_json()["error"]
    assert published == []


def test_no_email_is_no_exemption_from_the_species_gate(tmp_path):
    client, db = _unknown_app(tmp_path)
    tid = client.post("/author/new").headers["Location"].rstrip("/").split("/")[-1]
    j = client.post(f"/author/{tid}/row", json={"layer": 1, "remedy": FF}).get_json()
    assert j["animal_refused"] == [FF] and j["species_unknown"] is True
    assert client.post(f"/author/{tid}/remedy-set/add-one",
                       json={"remedy": FF}).get_json().get("needs_species") is True


def _old_schema(tmp_path):
    """An e4l.db from before animal_name: every read of it fails."""
    p = str(tmp_path / "e4l.db")
    cx = sqlite3.connect(p)
    cx.execute("CREATE TABLE e4l_clients (client_id INTEGER PRIMARY KEY, name TEXT, "
               "email TEXT, species TEXT)")
    cx.execute("INSERT INTO e4l_clients VALUES (1001, 'Ann Owner', ?, 'Human')", (OWNER,))
    cx.execute("CREATE TABLE e4l_scans (scan_id INTEGER PRIMARY KEY, client_id INTEGER, "
               "scan_date TEXT)")
    cx.execute("INSERT INTO e4l_scans VALUES (9001, 1001, '2026-11-01')")
    cx.commit()
    cx.close()
    return p


def test_a_resolver_error_fails_closed_for_a_named_or_stored_intake(tmp_path):
    p = _old_schema(tmp_path)
    for cid, name in (("1001", ""), (None, "Rover Owner")):
        res = resolve_intake_subject(p, cid, OWNER, name)
        assert res["by"] == "unresolved" and res["species"] is None
    assert species_for_intake(p, None, OWNER, "") == "Human"       # a bare email reads it
    client, _db = _real_app(tmp_path, p)
    tid = _intake(client, "Rover Owner", OWNER)
    assert _panel_scan(client, tid)["scan_id"] is None


def test_an_ambiguous_name_takes_no_answer(tmp_path):
    p = _e4l_rows(tmp_path, [(1005, "Sam Owner", OWNER, "", ""),
                             (1006, "Sam Owner", OWNER, "", "Sam")])
    record_species_answer(p, OWNER, "Dog", name="Sam Owner")
    res = resolve_intake_subject(p, None, OWNER, "Sam Owner")
    assert res["ambiguous"] is True and res["species"] is None


def test_an_answer_with_no_client_table_is_an_error_the_page_shows(tmp_path):
    p = str(tmp_path / "e4l.db")
    sqlite3.connect(p).close()                                  # no e4l_clients table
    client, _db = _real_app(tmp_path, p)
    tid = _intake(client, "Ann Owner", OWNER)
    r = client.post(f"/author/{tid}/species", json={"species": "Human"})
    assert r.status_code == 500 and "not recorded" in r.get_json()["error"]
    assert record_species_answer(str(tmp_path / "nothing.db"), OWNER, "Dog") is None
    assert not (tmp_path / "nothing.db").exists()
    from dashboard import biofield_report_html as H
    assert "if(!(r&&r.ok)){alert((r&&r.error)||'The answer was not recorded.');return false}" \
        in H._AUTHOR_JS
