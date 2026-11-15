"""Live session segments 1 to 3 (plan 2026-11-07): Terrain, BSI, Regulation.

Storage of the four BSI numbers, the per-field save, the interpret path, the
/author/<tid>/terrain route and the editor page.
"""
import json
import re
import sqlite3

from biofield_local_app import create_app
from dashboard.biofield_authoring import (authored_report, create_test, init_auth_tables,
                                          set_terrain_field, update_terrain)
from dashboard.biofield_interpret import build_interpret_prompt, interpret_transcript
from dashboard.biofield_report_html import render_author_html
from tests.test_biofield_local_app import _seed, _species_known, _no_console_gate  # noqa: F401


def _cx(tmp_path):
    cx = sqlite3.connect(str(tmp_path / "t.db"))
    init_auth_tables(cx)
    return cx


# ---- storage ---------------------------------------------------------------

def test_columns_exist_and_are_nullable(tmp_path):
    cx = _cx(tmp_path)
    cols = {r[1]: r for r in cx.execute("PRAGMA table_info(biofield_auth_tests)")}
    for c in ("bsi_a", "bsi_b", "bsi_c", "bsi_d"):
        assert c in cols and cols[c][2].upper() == "INTEGER" and cols[c][3] == 0
    tid = create_test(cx, "J", "j@x.com", "2026-11-14")
    assert authored_report(cx, tid)["bsi"] == [None, None, None, None]


def test_init_twice_is_harmless(tmp_path):
    cx = _cx(tmp_path)
    init_auth_tables(cx)
    init_auth_tables(cx)


def test_update_terrain_saves_bsi_all_or_nothing_and_never_clobbers(tmp_path):
    cx = _cx(tmp_path)
    tid = create_test(cx, "J", "j@x.com", "2026-11-14")
    update_terrain(cx, tid, bsi=[13, 8, 5, 3])
    assert authored_report(cx, tid)["bsi"] == [13, 8, 5, 3]
    # A pass that heard nothing keeps what was stored.
    update_terrain(cx, tid, phase=None, location="", bsi=None)
    assert authored_report(cx, tid)["bsi"] == [13, 8, 5, 3]
    # A partial or partly invalid reading never merges into a hybrid: all four stay.
    for partial in ([15, 10, None, None], [None, 9, 5, 3], [15, 10, 0, 3], [15, 10, "x", 3],
                    [15, 10, 5], [15, 10, 5, 3, 1]):
        update_terrain(cx, tid, bsi=partial)
        assert authored_report(cx, tid)["bsi"] == [13, 8, 5, 3], partial
    # A full reading replaces all four.
    update_terrain(cx, tid, bsi=["15", 10, 7, 2])
    assert authored_report(cx, tid)["bsi"] == [15, 10, 7, 2]
    # A BSI-only pass leaves phase and location alone, and the reverse.
    update_terrain(cx, tid, phase=4, location="kidney")
    update_terrain(cx, tid, bsi=[12, 8, 5, 3])
    rep = authored_report(cx, tid)
    assert (rep["phase"], rep["location"], rep["bsi"]) == (4, "kidney", [12, 8, 5, 3])


def test_set_terrain_field_validates_and_clears(tmp_path):
    cx = _cx(tmp_path)
    tid = create_test(cx, "J", "j@x.com", "2026-11-14")
    assert set_terrain_field(cx, tid, "bsi_c", "7") == 7
    assert set_terrain_field(cx, tid, "phase", "3") == 3
    assert set_terrain_field(cx, tid, "location", " liver ") == "liver"
    for field, bad in (("bsi_a", "22"), ("bsi_a", "0"), ("phase", "6"), ("nope", "1"),
                       ("name", "x")):
        try:
            set_terrain_field(cx, tid, field, bad)
        except ValueError:
            pass
        else:
            raise AssertionError(f"{field}={bad!r} was accepted")
    rep = authored_report(cx, tid)
    assert (rep["phase"], rep["location"], rep["bsi"]) == (3, "liver", [None, None, 7, None])
    set_terrain_field(cx, tid, "bsi_c", "")
    set_terrain_field(cx, tid, "phase", "")
    rep = authored_report(cx, tid)
    assert rep["phase"] is None and rep["bsi"][2] is None


# ---- interpret --------------------------------------------------------------

def test_prompt_asks_for_bsi_and_keeps_phase_and_location():
    sys_ = build_interpret_prompt("x")["system"]
    assert '"bsi": [int, int, int, int]' in sys_
    assert "set top-level `phase`" in sys_ and "top-level `location`" in sys_


def test_interpret_parses_bsi():
    def fake(bsi):
        return lambda s, u: json.dumps({"phase": 2, "location": "toxicity", "bsi": bsi,
                                        "layers": []})
    assert interpret_transcript("t", fake([21, 1, 1, 1]))["bsi"] == [21, 1, 1, 1]
    assert interpret_transcript("t", fake(["13", 8, 5, 3]))["bsi"] == [13, 8, 5, 3]
    for bad in (None, [], [1, 2, 3], "13 8 5 3", [None, None, None, None], [0, 0, 0, 0],
                [13, 30, 5, None], [15, 10, None, None], [13, 8, 5, 22]):
        assert interpret_transcript("t", fake(bad))["bsi"] is None, bad
    assert interpret_transcript("", fake([1, 1, 1, 1]))["bsi"] is None


def _app(db, payload):
    client = create_app(db, interpret_complete=lambda s, u: json.dumps(payload)).test_client()
    tid = client.post("/author/new").headers["Location"].rstrip("/").rsplit("/", 1)[-1]
    client.post(f"/author/{tid}/session", json={"transcript": "BSI 13 times 8 to 5 times 3"})
    return client, tid


def _report(db, tid):
    with sqlite3.connect(db) as cx:
        return authored_report(cx, tid)


def test_interpret_route_partial_bsi_leaves_all_four_stored(tmp_path):
    db = str(tmp_path / "chat_log.db")
    _seed(db)
    client, tid = _app(db, {"bsi": [13, 8, 5, 3], "layers": []})
    client.post(f"/author/{tid}/interpret", json={})
    client2 = create_app(db, interpret_complete=lambda s, u: json.dumps(
        {"bsi": [15, 10, None, None], "layers": []})).test_client()
    j = client2.post(f"/author/{tid}/interpret", json={}).get_json()
    assert j["bsi"] is None
    assert _report(db, tid)["bsi"] == [13, 8, 5, 3]


def test_interpret_route_saves_bsi_and_a_later_pass_does_not_wipe_it(tmp_path):
    db = str(tmp_path / "chat_log.db")
    _seed(db)
    client, tid = _app(db, {"phase": 4, "location": "kidney", "bsi": [13, 8, 5, 3],
                            "layers": []})
    j = client.post(f"/author/{tid}/interpret", json={}).get_json()
    assert j["bsi"] == [13, 8, 5, 3]
    rep = _report(db, tid)
    assert (rep["phase"], rep["location"], rep["bsi"]) == (4, "kidney", [13, 8, 5, 3])
    # Second interpret with no BSI spoken.
    client2 = create_app(db, interpret_complete=lambda s, u: json.dumps({"layers": []})).test_client()
    client2.post(f"/author/{tid}/interpret", json={})
    assert _report(db, tid)["bsi"] == [13, 8, 5, 3]
    # And the reloaded editor page shows the stored values.
    h = client2.get(f"/author/{tid}").get_data(as_text=True)
    assert "id=bsi_a" in h and "value='13'" in h and "value='3'" in h
    assert "Level 4 · Positive" in h


# ---- the route ------------------------------------------------------------

def test_terrain_route_saves_each_field_and_answers_with_regulation(tmp_path):
    db = str(tmp_path / "chat_log.db")
    _seed(db)
    client, tid = _app(db, {"layers": []})
    url = f"/author/{tid}/terrain"
    for field, v in (("bsi_a", "13"), ("bsi_b", "8"), ("bsi_c", "5")):
        j = client.post(url, json={"field": field, "value": v}).get_json()
        assert j["ok"] and j["regulation"] is None
    j = client.post(url, json={"field": "bsi_d", "value": "3"}).get_json()
    assert j["bsi"] == [13, 8, 5, 3] and j["regulation"] == {"level": 4, "name": "Positive"}
    assert j["warnings"] == []
    j = client.post(url, json={"field": "bsi_d", "value": "9"}).get_json()
    assert j["regulation"] == {"level": 3, "name": "Mixed"}
    assert j["warnings"] == ["D is greater than C"]
    j = client.post(url, json={"field": "phase", "value": "2"}).get_json()
    assert j["phase"] == 2
    j = client.post(url, json={"field": "location", "value": "large intestine meridian"}).get_json()
    assert j["location"] == "large intestine meridian"


def test_terrain_route_refuses_bad_input_without_writing(tmp_path):
    db = str(tmp_path / "chat_log.db")
    _seed(db)
    client, tid = _app(db, {"layers": []})
    url = f"/author/{tid}/terrain"
    client.post(url, json={"field": "bsi_a", "value": "13"})
    for body in ({"field": "bsi_a", "value": "22"}, {"field": "phase", "value": "9"},
                 {"field": "name", "value": "x"}, {}):
        r = client.post(url, json=body)
        assert r.status_code == 400 and r.get_json()["ok"] is False, body
    assert _report(db, tid)["bsi"][0] == 13


def test_terrain_route_sits_behind_the_console_gate(tmp_path, monkeypatch):
    """Same protection as the neighbouring editor routes: the app-wide console key."""
    db = str(tmp_path / "chat_log.db")
    _seed(db)
    import dashboard as _d
    monkeypatch.setenv("CONSOLE_SECRET", "s3cret-test")
    monkeypatch.setattr(_d, "CONSOLE_SECRET", "s3cret-test", raising=False)
    client = create_app(db).test_client()
    r = client.post("/author/a1/terrain", json={"field": "bsi_a", "value": "5"})
    assert r.status_code == 401
    r = client.post("/author/a1/header", json={"name": "x"})
    assert r.status_code == 401


# ---- the page ---------------------------------------------------------------

def _page(**rep):
    base = {"test_id": "a7", "client": {"name": "J", "email": "j@x.com"}, "date": "",
            "layers": [], "schedule": []}
    base.update(rep)
    return render_author_html(base, [], "")


def _session_block(h):
    i = h.index("<h2>Live session (voice)</h2>")
    return h[i:h.index("<h2>Narrative</h2>", i)]


def test_segments_sit_in_the_live_session_block_above_record():
    s = _session_block(_page())
    assert s.index("id=terrainSegs") < s.index("recStart()")
    for label in (">Terrain<", ">Biological Score Index<", ">Regulation<"):
        assert label in s, label
    assert "Enter all four numbers" in s


def test_phase_dropdown_lists_blank_then_the_five_phases():
    s = _session_block(_page(phase=3))
    sel = re.search(r"<select id=t_phase[^>]*>(.*?)</select>", s).group(1)
    opts = re.findall(r"<option value='([^']*)'( selected)?>([^<]*)</option>", sel)
    assert [o[0] for o in opts] == ["", "1", "2", "3", "4", "5"]
    assert opts[1][2] == "1 &middot; Terrain Revive"
    assert [o[0] for o in opts if o[1]] == ["3"]


def test_four_bsi_inputs_are_1_to_21_and_laid_out_a_x_b_to_c_x_d():
    s = _session_block(_page(bsi=[13, 8, 5, 3], location="kidney"))
    for letter in "abcd":
        m = re.search(r"<input id=bsi_%s [^>]*>" % letter, s)
        assert m and "min=1" in m.group(0) and "max=21" in m.group(0)
        assert f"saveTerrain('bsi_{letter}',this)" in m.group(0)
    text = re.sub(r"<[^>]+>", "", s[s.index("Biological Score Index"):s.index("<span id=t_regulation")])
    text = text.replace("Regulation", "")
    assert text.split() == ["Biological", "Score", "Index", "x", "to", "x"]
    assert 'value="kidney"' in s
    assert "Level 4 · Positive" in s


def test_warning_line_shows_for_readings_the_plan_says_never_happen():
    s = _session_block(_page(bsi=[3, 5, 3, 5]))
    assert "B is greater than A; D is greater than C" in s
    assert "Level 1 · Blocked" in s


def test_visible_text_has_no_em_dash_and_no_all_caps_label():
    s = _session_block(_page(bsi=[13, 8, 5, 3]))
    seg = s[s.index("id=terrainSegs"):s.index("recStart()")]
    visible = re.sub(r"<[^>]+>", " ", seg)
    assert "—" not in visible and "&mdash;" not in visible
    for word in re.findall(r"[A-Za-z]{3,}", visible):
        assert not word.isupper(), word


def test_page_js_posts_the_field_to_the_terrain_route():
    h = _page()
    assert "post('/author/a7/terrain',{field:field,value:v})" in h
    assert "function regulationLevel(" in h and "function renderTerrain(" in h
    assert "window._tStored=" in h


def test_page_embeds_the_stored_state_it_renders_from():
    s = _session_block(_page(phase=2, location="a</script>b", bsi=[13, 8, 5, 3]))
    m = re.search(r"<script>window._tStored=(\{.*?\})</script>", s)
    assert m and "</script>b" not in m.group(1)
    st = json.loads(m.group(1))
    assert st == {"phase": 2, "location": "a</script>b", "bsi": [13, 8, 5, 3]}
