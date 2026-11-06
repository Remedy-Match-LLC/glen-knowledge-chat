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
from dashboard.biofield_e4l import intake_e4l_client, species_for_intake

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
    app = create_app(db, e4l_db=e4l or _e4l(tmp_path),
                     scan_lookup=lambda e: {"found": False, "layers": []},
                     fetch_client_photo=lambda *a, **k: None, **kw)
    return app.test_client(), db


def _intake(client, name, email, client_id=None):
    tid = client.post("/author/new").headers["Location"].rstrip("/").split("/")[-1]
    body = {"name": name, "email": email, "date": "2026-11-06"}
    if client_id is not None:
        body["client_id"] = client_id
    client.post(f"/author/{tid}/header", json=body)
    return tid


# ---- the identity and species read --------------------------------------------------

def test_the_pet_on_the_owners_email_reads_as_a_dog(tmp_path):
    p = _e4l(tmp_path)
    assert species_for_intake(p, None, OWNER, "Rover Owner") == "Dog"
    assert intake_e4l_client(p, None, OWNER, "Rover Owner")["email"] == PET


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
    assert intake_e4l_client(p, None, OWNER, "Rover Owner")["by"] == "email"


# ---- every surface, through the real routes -----------------------------------------

def test_suggestions_treat_the_pet_as_an_animal(tmp_path):
    client, db = _app(tmp_path)
    tid = _intake(client, "Rover Owner", OWNER)
    assert st.animal_infoceuticals(sqlite3.connect(db), tid) is not None
    tid2 = _intake(client, "Ann Owner", OWNER)
    assert st.animal_infoceuticals(sqlite3.connect(db), tid2) is None


def test_import_reveal_treats_the_pet_as_an_animal(tmp_path, monkeypatch):
    seen = {}

    def capture(*a, **k):
        seen["is_animal"] = k.get("is_animal")
        return {"found": False}

    monkeypatch.setattr(RI, "synthesize_reveal_layers", capture)
    client, _db = _app(tmp_path)
    tid = _intake(client, "Rover Owner", OWNER)
    client.post(f"/author/{tid}/e4l/import-reveal", json={})
    assert seen.get("is_animal") is True


def _layers(db, tid):
    return [r[0] for r in sqlite3.connect(db).execute(
        "SELECT remedy FROM biofield_auth_chain WHERE test_id=? ORDER BY id",
        (int(tid.lstrip("a")),))]


def test_the_plus_button_refuses_an_ff_for_the_pet(tmp_path):
    client, db = _app(tmp_path)
    tid = _intake(client, "Rover Owner", OWNER)
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
    tid = _intake(client, "Rover Owner", OWNER)
    j = client.post(f"/author/{tid}/remedy-set/apply-to-chain",
                    json={"remedies": [FF, INFO]}).get_json()
    assert j["animal_refused"] == [FF] and j["added"] == 1
    assert all(is_infoceutical_remedy(r) for r in _layers(db, tid))


def test_a_layer_pick_refuses_an_ff_for_the_pet(tmp_path):
    client, db = _app(tmp_path)
    tid = _intake(client, "Rover Owner", OWNER)
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
    tid = _intake(client, "Rover Owner", OWNER)
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
    _intake(client, "Rover Owner", OWNER)
    _photos(db)
    r = client.get(f"/client-photo/{OWNER}?name=Rover%20Owner")
    assert r.status_code == 200 and r.data == b"PET-PHOTO"
    assert client.get(f"/client-photo-framing/{OWNER}?name=Rover%20Owner").status_code == 200


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
