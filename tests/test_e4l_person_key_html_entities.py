"""E4L stores some client names HTML-escaped. A name key must decode them, or an
intake named "Desiree' DallaGuardia" never matches the E4L client
"Desiree&#39; DallaGuardia" and her photo stays hidden (Glen, 2026-11-10)."""
import sqlite3

from dashboard.biofield_e4l import _person_key, resolve_intake_subject


def test_an_escaped_apostrophe_keys_like_a_plain_one():
    assert _person_key("Desiree&#39; DallaGuardia") == _person_key("Desiree' DallaGuardia")
    assert _person_key("Smith &amp; Jones") == _person_key("Smith & Jones")
    assert _person_key("Desiree&amp;#39; DallaGuardia") == _person_key("Desiree' DallaGuardia")


def test_a_different_name_still_does_not_match():
    assert _person_key("Desiree&#39; DallaGuardia") != _person_key("Susan DallaGuardia")


def test_the_intake_resolves_to_the_escaped_e4l_client(tmp_path):
    db = str(tmp_path / "e4l.db")
    from dashboard import biofield_e4l as E
    cx = sqlite3.connect(db)
    E.init_db(cx) if hasattr(E, "init_db") else None
    cx.close()
    cx = sqlite3.connect(db)
    cols = [c[1] for c in cx.execute("pragma table_info(e4l_clients)")]
    if not cols:
        cx.execute("CREATE TABLE e4l_clients (client_id INTEGER PRIMARY KEY, name TEXT, "
                   "email TEXT, species TEXT, animal_name TEXT)")
        cols = ["client_id", "name", "email", "species", "animal_name"]
    # A second subject on the same email with the same first name, so only the full
    # name can pick Desiree's record (round 2).
    for row in ({"client_id": 340277, "name": "Desiree&#39; DallaGuardia",
                 "email": "d@example.com", "species": "Human", "animal_name": ""},
                {"client_id": 340278, "name": "Desiree&#39; Smith",
                 "email": "d@example.com", "species": "Human", "animal_name": ""}):
        use = [c for c in row if c in cols]
        cx.execute(f"INSERT INTO e4l_clients ({','.join(use)}) VALUES ({','.join('?'*len(use))})",
                   [row[c] for c in use])
    cx.commit(); cx.close()
    res = resolve_intake_subject(db, "", "d@example.com", "Desiree' DallaGuardia")
    assert res["by"] == "email+name", res["by"]
    assert int((res["client"] or {}).get("client_id") or (res["client"] or {}).get("id")) == 340277


def test_the_subject_name_is_decoded():
    from dashboard.biofield_e4l import subject_name
    assert subject_name((None, None, None, "", "Desiree&#39; DallaGuardia")) == "Desiree' DallaGuardia"


def test_the_photo_name_key_decodes_too(tmp_path):
    from dashboard import client_photos as P
    assert P._name_key("Desiree&#39; DallaGuardia") == P._name_key("Desiree' DallaGuardia")
    cx = sqlite3.connect(str(tmp_path / "c.db"))
    cx.execute("CREATE TABLE fmp_snap_clients (id_pk TEXT, email TEXT, name_first TEXT, name_last TEXT)")
    cx.execute("INSERT INTO fmp_snap_clients VALUES ('4576','d@example.com','Desiree''','DallaGuardia')")
    cx.commit()
    assert P.fmp_people_for_email(cx, "d@example.com"), "fixture must be read"
    assert P.fmp_person_for(cx, "d@example.com", "Desiree&#39; DallaGuardia") == "4576"


def test_a_decoded_space_is_a_word_break_not_deleted():
    from dashboard import client_photos as P
    for key in (_person_key, P._name_key):
        assert key("Ann&nbsp;A Bell") == "ann a bell"
        assert key("Ann&nbsp;A Bell") != key("Anna Bell")
