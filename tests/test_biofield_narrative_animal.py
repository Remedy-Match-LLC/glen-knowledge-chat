"""An animal's narrative is addressed to the OWNER and names the animal.

Glen, 2026-09-18: "Address the animal report to the owner (e.g. Aloha Sharon,
Hershey's Biofield Analysis...)". Asked about pronouns, given no store records an
animal's sex: "name only is perfect."

Before this, the prompt always opened "Aloha <first name>," with the client's own
name and spoke to the reader as the patient, so Hershey's report read "Aloha
Hershey, your body...". A human's prompt must stay byte-identical.
"""
import sqlite3

import pytest

from dashboard.biofield_narrative import build_narrative_prompt, generate_narrative
from biofield_local_app import create_app


_HERSHEY = {"name": "Hershey", "species": "Dog", "owner": "Sharon"}


def _report(name="Hershey  Connour"):
    return {
        "test_id": "39", "client": {"name": name, "email": "h@x.com"},
        "date": "2026-09-18",
        "layers": [
            {"layer": 1, "head": "Circulation", "most_affected": "Circulation",
             "remedy": "ED5 Circulation Energetic Driver Infoceutical",
             "dosage": "build up 1 drop a day to 15 drops", "frequency": "daily",
             "timing": "on rising"},
        ],
        "schedule": {"slots": [], "entries": []},
    }


def test_human_prompt_is_unchanged_when_no_animal_is_given():
    assert build_narrative_prompt(_report("Lewis Zardo"), "n") == \
        build_narrative_prompt(_report("Lewis Zardo"), "n", animal=None)


def test_animal_prompt_greets_the_owner_and_names_the_animal():
    p = build_narrative_prompt(_report(), "", animal=_HERSHEY)
    s, u = p["system"], p["user"]
    assert "'Aloha Sharon,'" in s
    assert "Hershey's Biofield Analysis" in s
    assert "Open with 'Aloha <first name>,'" not in s
    assert "OWNER" in u and "Sharon" in u
    assert "ANIMAL" in u and "dog" in u
    assert "PATIENT:" not in u


def test_animal_prompt_forbids_pronouns_for_the_animal():
    s = build_narrative_prompt(_report(), "", animal=_HERSHEY)["system"]
    assert ("Never refer to Hershey with a pronoun: not 'he', 'she', 'him', 'his', "
            "'her', 'hers' or 'it'.") in s
    assert "Never call Hershey 'you'." in s
    assert "repeat the name 'Hershey'" in s


def test_animal_closing_speaks_about_the_animal_not_the_reader():
    s = build_narrative_prompt(_report(), "", animal=_HERSHEY)["system"]
    assert "If Hershey tends to be highly sensitive or reactive" in s
    assert "observe how Hershey responds" in s
    assert "If you tend to be highly sensitive" not in s
    assert "observe how your body responds" not in s


def test_animal_with_no_known_owner_opens_with_a_bare_aloha():
    p = build_narrative_prompt(_report(), "", animal={**_HERSHEY, "owner": ""})
    assert "'Aloha,'" in p["system"]
    assert "'Aloha Sharon,'" not in p["system"]
    assert "Hershey's Biofield Analysis" in p["system"]


def test_greeting_to_the_animal_is_corrected_to_the_owner():
    out = generate_narrative(_report(), "",
                             lambda s, u: "Aloha Hershey,\n\nHershey's Biofield Analysis shows",
                             animal=_HERSHEY)
    assert out.startswith("Aloha Sharon,\n")
    assert "Hershey's Biofield Analysis" in out


def test_a_human_greeting_is_never_rewritten():
    out = generate_narrative(_report("Hershey Smith"), "",
                             lambda s, u: "Aloha Hershey,\n\nYour body identified")
    assert out.startswith("Aloha Hershey,")


# --- the route: species from e4l.db, owner from the injected lookup --------------

@pytest.fixture(autouse=True)
def _no_gate(monkeypatch):
    monkeypatch.delenv("CONSOLE_SECRET", raising=False)
    import dashboard as _d; monkeypatch.setattr(_d, "CONSOLE_SECRET", "", raising=False)


def _e4l(tmp_path, species, email="h@x.com", animal_name=""):
    path = str(tmp_path / "e4l.db")
    cx = sqlite3.connect(path)
    cx.execute("CREATE TABLE e4l_clients (client_id INTEGER, name TEXT, email TEXT, "
               "species TEXT, animal_name TEXT)")
    cx.execute("INSERT INTO e4l_clients VALUES (1, 'Hershey  Connour', ?, ?, ?)",
               (email, species, animal_name))
    cx.commit(); cx.close()
    return path


def _generate(tmp_path, e4l_db, owner_lookup):
    seen = {}
    app = create_app(str(tmp_path / "chat_log.db"),
                     complete=lambda s, u: seen.update(s=s, u=u) or "Aloha Hershey,\n\nx",
                     scan_lookup=lambda e: {}, fetch_profile=lambda e: {},
                     e4l_db=e4l_db, fetch_animal_owner=owner_lookup)
    c = app.test_client()
    tid = c.post("/author/new").headers["Location"].rstrip("/").split("/")[-1]
    c.post(f"/author/{tid}/header", json={"name": "Hershey  Connour", "email": "H@x.com"})
    c.post(f"/author/{tid}/row", json={"layer": 1, "head": "Circulation",
                                       "remedy": "ED5 Circulation Driver"})
    r = c.post(f"/test/{tid}/generate", json={"notes": ""})
    return r.get_json(), seen


def test_route_addresses_a_dog_to_its_owner(tmp_path):
    asked = []
    j, seen = _generate(tmp_path, _e4l(tmp_path, "Dog"),
                        lambda e: asked.append(e) or "Sharon Connour")
    assert asked == ["h@x.com"]
    assert "'Aloha Sharon,'" in seen["s"]
    assert "Hershey's Biofield Analysis" in seen["s"]
    assert j["narrative"].startswith("Aloha Sharon,")


def test_route_prefers_the_recorded_animal_name(tmp_path):
    _, seen = _generate(tmp_path, _e4l(tmp_path, "Dog", animal_name="Hersh"),
                        lambda e: "Sharon")
    assert "Hersh's Biofield Analysis" in seen["s"]


def test_route_leaves_a_human_alone_and_never_asks_for_an_owner(tmp_path):
    asked = []
    _, seen = _generate(tmp_path, _e4l(tmp_path, "Human"), lambda e: asked.append(e) or "X")
    assert asked == []
    assert "Open with 'Aloha <first name>,'" in seen["s"]


def test_route_reads_unknown_species_as_human(tmp_path):
    _, seen = _generate(tmp_path, str(tmp_path / "absent.db"), lambda e: "Sharon")
    assert "Open with 'Aloha <first name>,'" in seen["s"]


def test_route_owner_lookup_failure_still_names_the_animal(tmp_path):
    def boom(e):
        raise RuntimeError("prod down")
    j, seen = _generate(tmp_path, _e4l(tmp_path, "Dog"), boom)
    assert "'Aloha,'" in seen["s"]
    assert "Hershey's Biofield Analysis" in seen["s"]
    assert "narrative" in j
