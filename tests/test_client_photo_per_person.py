"""Client photos belong to a person, not an email.

2026-10-09: Debra Herndon's Biofield Intake page and portal Body Map showed her
daughter Eliana's photo. Five FileMaker records share the family's email, and the photo
store was keyed by email. Glen: on a shared email, Biofield Intake offers the photos to
choose from; client surfaces show nothing that is not assigned to that person.
"""
import inspect
import sqlite3

import pytest

from biofield_local_app import create_app
from dashboard import client_photos as cph

FAMILY = "family@example.com"
HERNDONS = [("6250", "Debra", "Herndon"), ("6345", "Christopher", "Herndon"),
            ("6427", "Anastasia", "Herndon"), ("6512", "Paul", "Herndon"),
            ("21402", "Eliana", "Herndon")]


@pytest.fixture(autouse=True)
def _no_console_gate(monkeypatch):
    monkeypatch.delenv("CONSOLE_SECRET", raising=False)
    monkeypatch.delenv("PORTAL_PUBLISH_BASE_URL", raising=False)
    import dashboard as _d
    monkeypatch.setattr(_d, "CONSOLE_SECRET", "", raising=False)


def _db(tmp_path, extra=()):
    db = str(tmp_path / "chat_log.db")
    with sqlite3.connect(db) as cx:
        cx.execute("CREATE TABLE fmp_snap_clients (id_pk TEXT, email TEXT, "
                   "name_first TEXT, name_last TEXT)")
        for pk, f, l in HERNDONS:
            cx.execute("INSERT INTO fmp_snap_clients VALUES(?,?,?,?)", (pk, FAMILY, f, l))
        cx.execute("INSERT INTO fmp_snap_clients VALUES('900','solo@example.com','Sam','Solo')")
        for row in extra:
            cx.execute("INSERT INTO fmp_snap_clients VALUES(?,?,?,?)", row)
    return db


def _intake(db, name, email):
    from dashboard.biofield_authoring import create_test
    with sqlite3.connect(db) as cx:
        return f"a{create_test(cx, name, email, '2026-10-09')}"


def _no_fetch(email, client_id=None):
    return None


# --- the person resolver -------------------------------------------------------------

@pytest.mark.parametrize("name,want", [
    ("Debra Herndon", "6250"), ("debra herndon", "6250"), ("Deb Herndon", "6250"),
    ("Eliana Herndon", "21402"),
    ("Herndon", None), ("", None), ("Debra Smith", None), ("D Herndon", None),
])
def test_an_email_and_name_resolve_to_one_filemaker_record(tmp_path, name, want):
    with sqlite3.connect(_db(tmp_path)) as cx:
        assert cph.fmp_person_for(cx, FAMILY, name) == want


def test_a_one_record_email_still_needs_the_name_to_match(tmp_path):
    # Review round 1: a family member with no FileMaker record, on a parent's email,
    # resolved to the parent and their upload became the parent's photo.
    with sqlite3.connect(_db(tmp_path)) as cx:
        assert cph.fmp_person_for(cx, "solo@example.com", "Sam Solo") == "900"
        assert cph.fmp_person_for(cx, "solo@example.com", "Samuel Solo") == "900"
        assert cph.fmp_person_for(cx, "solo@example.com", "Jane Newkid") is None
        assert cph.fmp_person_for(cx, "solo@example.com", "") is None
        assert cph.fmp_person_for(cx, "nobody@example.com", "Sam Solo") is None


def test_an_id_filemaker_reuses_for_another_name_resolves_to_nothing(tmp_path):
    # 33 FileMaker ids sit on 67 records (production-3a, 2026-10-09).
    db = _db(tmp_path, extra=[("6250", "other@example.com", "Maria", "Ktenas")])
    with sqlite3.connect(db) as cx:
        assert cph.fmp_person_for(cx, FAMILY, "Debra Herndon") is None


def test_two_records_matching_the_name_resolve_to_nothing(tmp_path):
    db = _db(tmp_path, extra=[("7000", FAMILY, "Debra", "Herndon")])
    with sqlite3.connect(db) as cx:
        assert cph.fmp_person_for(cx, FAMILY, "Debra Herndon") is None


# --- what a client surface may show --------------------------------------------------

@pytest.mark.parametrize("source", ["intake-survey", "fmp", "console", "fmp-intake-upload", "ghl"])
def test_a_client_surface_never_shows_an_email_photo_someone_else_supplied(tmp_path, source):
    with sqlite3.connect(str(tmp_path / "x.db")) as cx:
        cph.put(cx, FAMILY, b"ELIANA", "image/png", source=source)
        assert cph.for_client_surface(cx, FAMILY) is None
        assert cph.for_client_surface(cx, FAMILY, "6250") is None


def test_a_client_surface_shows_the_persons_own_photo_or_their_own_upload(tmp_path):
    with sqlite3.connect(str(tmp_path / "x.db")) as cx:
        cph.put(cx, FAMILY, b"SELF", "image/png", source="portal-self")
        assert cph.for_client_surface(cx, FAMILY)["blob"] == b"SELF"
        # Review round 1: another family member's portal-self upload on the shared email
        # must not show on a portal that names a person.
        assert cph.for_client_surface(cx, FAMILY, "6250") is None
        cph.put_for_client(cx, "6250", FAMILY, b"DEBRA", "image/png", source="fmp")
        assert cph.for_client_surface(cx, FAMILY, "6250")["blob"] == b"DEBRA"


def test_a_bulk_load_never_replaces_a_clients_own_upload(tmp_path):
    with sqlite3.connect(str(tmp_path / "x.db")) as cx:
        cph.put_for_client(cx, "6250", FAMILY, b"SELF", "image/png", source="portal-self")
        assert cph.put_for_client(cx, "6250", FAMILY, b"FMP", "image/png",
                                  source="fmp", force=False) is None
        assert cph.get_for_client(cx, "6250")["blob"] == b"SELF"
        assert cph.put_for_client(cx, "6345", FAMILY, b"FMP", "image/png",
                                  source="fmp", force=False) == "6345"


# --- Biofield Intake ------------------------------------------------------------------

def test_a_shared_email_photo_never_shows_for_a_family_member(tmp_path):
    db = _db(tmp_path)
    with sqlite3.connect(db) as cx:
        cph.put(cx, FAMILY, b"ELIANA", "image/png", source="intake-survey")
    c = create_app(db, fetch_client_photo=_no_fetch).test_client()
    assert c.get(f"/client-photo/{FAMILY}?name=Debra%20Herndon").status_code == 404
    assert c.get(f"/client-photo/{FAMILY}").status_code == 404
    assert c.get(f"/client-photo-framing/{FAMILY}?name=Debra%20Herndon").status_code == 404


def test_the_persons_own_photo_shows_on_a_shared_email(tmp_path):
    db = _db(tmp_path)
    with sqlite3.connect(db) as cx:
        cph.put(cx, FAMILY, b"ELIANA", "image/png", source="intake-survey")
        cph.put_for_client(cx, "6250", FAMILY, b"DEBRA", "image/png", source="fmp")
    c = create_app(db, fetch_client_photo=_no_fetch).test_client()
    assert c.get(f"/client-photo/{FAMILY}?name=Debra%20Herndon").data == b"DEBRA"
    assert c.get(f"/client-photo/{FAMILY}?name=Paul%20Herndon").status_code == 404


def test_a_one_record_email_still_shows_its_email_photo(tmp_path):
    db = _db(tmp_path)
    with sqlite3.connect(db) as cx:
        cph.put(cx, "solo@example.com", b"SAM", "image/png", source="fmp")
        cph.put(cx, "unknown@example.com", b"U", "image/png", source="fmp")
    c = create_app(db, fetch_client_photo=_no_fetch).test_client()
    assert c.get("/client-photo/solo@example.com?name=Sam%20Solo").data == b"SAM"
    assert c.get("/client-photo/unknown@example.com").data == b"U"
    # a name FileMaker does not have on that email sees nothing, not Sam's photo
    assert c.get("/client-photo/solo@example.com?name=Jane%20Newkid").status_code == 404


def test_the_intake_page_refreshes_this_persons_row_from_prod(tmp_path):
    db = _db(tmp_path)
    calls = []

    def fetch(email, client_id=None):
        calls.append((email, client_id))
        if client_id == "6250":
            return {"blob": b"DEBRA", "content_type": "image/png", "source": "fmp"}
        return {"blob": b"ELIANA", "content_type": "image/png", "source": "intake-survey"}
    c = create_app(db, fetch_client_photo=fetch).test_client()
    assert c.get(f"/client-photo/{FAMILY}?name=Debra%20Herndon").data == b"DEBRA"
    assert (FAMILY, "6250") in calls
    with sqlite3.connect(db) as cx:
        assert cph.get(cx, FAMILY) is None          # the shared email row was never pulled


def test_candidates_list_every_family_photo_and_the_pick_saves_for_this_person(tmp_path):
    db = _db(tmp_path)
    with sqlite3.connect(db) as cx:
        cph.put(cx, FAMILY, b"HOUSE", "image/png", source="intake-survey")
        cph.put_for_client(cx, "21402", FAMILY, b"ELIANA", "image/png", source="fmp")
        cph.put_for_client(cx, "6345", FAMILY, b"CHRIS", "image/png", source="fmp")
    tid = _intake(db, "Debra Herndon", FAMILY)
    c = create_app(db, fetch_client_photo=_no_fetch).test_client()
    j = c.get(f"/client-photo-candidates/{FAMILY}?name=Debra%20Herndon").get_json()
    assert j["shared"] is True and j["count"] == 5 and j["person"] == "6250"
    keys = [x["key"] for x in j["candidates"]]
    assert sorted(keys) == ["21402", "6345", "email"]
    assert c.get(j["candidates"][0]["url"]).status_code == 200
    r = c.post(f"/test/{tid}/photo-pick", json={"key": "6345"}).get_json()
    assert r["ok"] and r["person"] == "6250"
    with sqlite3.connect(db) as cx:
        assert cph.get_for_client(cx, "6250")["blob"] == b"CHRIS"
        assert cph.get_for_client(cx, "6345")["blob"] == b"CHRIS"   # the source is untouched
        assert cph.get(cx, FAMILY)["blob"] == b"HOUSE"              # never the email row
    assert c.get(f"/client-photo/{FAMILY}?name=Debra%20Herndon").data == b"CHRIS"


def test_a_pick_needs_the_intake_name_to_name_one_person(tmp_path):
    db = _db(tmp_path)
    with sqlite3.connect(db) as cx:
        cph.put_for_client(cx, "21402", FAMILY, b"ELIANA", "image/png", source="fmp")
    tid = _intake(db, "Herndon Family", FAMILY)
    c = create_app(db, fetch_client_photo=_no_fetch).test_client()
    assert c.post(f"/test/{tid}/photo-pick", json={"key": "21402"}).status_code == 409
    j = c.get(f"/client-photo-candidates/{FAMILY}?name=Herndon%20Family").get_json()
    assert j["shared"] is True and j["person"] is None


def test_a_pick_cannot_reach_another_emails_photo(tmp_path):
    db = _db(tmp_path)
    with sqlite3.connect(db) as cx:
        cph.put_for_client(cx, "900", "solo@example.com", b"SAM", "image/png", source="fmp")
    tid = _intake(db, "Debra Herndon", FAMILY)
    c = create_app(db, fetch_client_photo=_no_fetch).test_client()
    assert c.post(f"/test/{tid}/photo-pick", json={"key": "900"}).status_code == 404
    assert c.get(f"/client-photo-candidate/{FAMILY}/900").status_code == 404
    assert c.get("/client-photo-candidates/solo@example.com").get_json()["shared"] is False


def test_an_upload_on_a_shared_email_saves_for_the_person(tmp_path):
    db = _db(tmp_path)
    tid = _intake(db, "Debra Herndon", FAMILY)
    c = create_app(db, fetch_client_photo=_no_fetch).test_client()
    import io
    r = c.post(f"/test/{tid}/photo", data={"photo": (io.BytesIO(b"NEW"), "p.png")},
               content_type="multipart/form-data")
    assert r.get_json()["ok"]
    with sqlite3.connect(db) as cx:
        assert cph.get_for_client(cx, "6250")["blob"] == b"NEW"
        assert cph.get(cx, FAMILY) is None


def test_an_upload_refuses_when_the_name_is_not_the_one_record_on_the_email(tmp_path):
    db = _db(tmp_path)
    tid = _intake(db, "Jane Newkid", "solo@example.com")
    c = create_app(db, fetch_client_photo=_no_fetch).test_client()
    import io
    r = c.post(f"/test/{tid}/photo", data={"photo": (io.BytesIO(b"KID"), "p.png")},
               content_type="multipart/form-data")
    assert r.status_code == 409
    with sqlite3.connect(db) as cx:
        assert cph.get_for_client(cx, "900") is None and cph.get(cx, "solo@example.com") is None


def test_an_upload_refuses_when_the_name_names_nobody_on_a_shared_email(tmp_path):
    db = _db(tmp_path)
    tid = _intake(db, "Herndon Family", FAMILY)
    c = create_app(db, fetch_client_photo=_no_fetch).test_client()
    import io
    r = c.post(f"/test/{tid}/photo", data={"photo": (io.BytesIO(b"NEW"), "p.png")},
               content_type="multipart/form-data")
    assert r.status_code == 409
    with sqlite3.connect(db) as cx:
        assert cph.get(cx, FAMILY) is None


def test_the_photo_fetch_never_puts_the_console_key_in_the_url():
    import biofield_local_app
    src = inspect.getsource(biofield_local_app._default_fetch_client_photo)
    assert "&key=" not in src and "?key=" not in src
    assert '"X-Console-Key": key' in src


# --- the portal ----------------------------------------------------------------------

def _portal(tmp_path, monkeypatch, content):
    import importlib
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    import app as appmod
    importlib.reload(appmod)
    from dashboard import client_portal as cp
    with sqlite3.connect(appmod.LOG_DB) as cx:
        cp.init_client_portal_table(cx)
        tok, _ = cp.upsert_portal(cx, FAMILY, "Debra Herndon", content)
        cx.commit()
    return appmod, tok


def test_the_portal_photo_follows_the_same_rule(tmp_path, monkeypatch):
    appmod, tok = _portal(tmp_path, monkeypatch, {"client_id": "6250"})
    with sqlite3.connect(appmod.LOG_DB) as cx:
        cph.put(cx, FAMILY, b"ELIANA", "image/png", source="intake-survey")
    c = appmod.app.test_client()
    assert c.get(f"/api/portal/{tok}/photo").status_code == 404
    assert c.get(f"/api/portal/{tok}/photo/framing").status_code == 404
    with sqlite3.connect(appmod.LOG_DB) as cx:
        cph.put_for_client(cx, "6250", FAMILY, b"DEBRA", "image/png", source="fmp")
    r = c.get(f"/api/portal/{tok}/photo")          # the same token serves her own row,
    assert r.status_code == 200 and r.data == b"DEBRA"   # so the 404s above were the rule
    assert c.get(f"/api/portal/{tok}/photo/framing").status_code == 200


def test_the_onboarding_photo_step_uses_the_same_rule(tmp_path):
    from dashboard import portal_onboarding as ob
    with sqlite3.connect(str(tmp_path / "x.db")) as cx:
        cph.put(cx, FAMILY, b"ELIANA", "image/png", source="portal-self")

        def done(cid):
            st = ob.build_status(cx, FAMILY, cid)
            steps = [x for ph in st.get("phases", []) for x in ph.get("steps", [])] \
                if isinstance(st, dict) and st.get("phases") else []
            hits = [x for x in steps if x.get("key") == "photo"]
            assert hits, list(st)[:10]
            return hits[0]["done"]
        assert done("6250") is False
        cph.put_for_client(cx, "6250", FAMILY, b"DEBRA", "image/png", source="fmp")
        assert done("6250") is True
