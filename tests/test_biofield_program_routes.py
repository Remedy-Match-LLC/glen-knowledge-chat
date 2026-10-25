"""The two buttons: one program from all three stress sources, in Glen's order.

Glen, 2026-09-17: *"2 buttons: full (narrow, bigger program) - vs minimum (wide,
minimal program)."*

Both PROPOSE. Neither writes until he confirms, because the causal chain is the
intake's core clinical artifact. That is the same rule #1717 set for Balance All.
"""
import json
import sqlite3
import pytest

from biofield_local_app import create_app


@pytest.fixture(autouse=True)
def _no_gate(monkeypatch):
    monkeypatch.delenv("CONSOLE_SECRET", raising=False)
    import dashboard as _d
    monkeypatch.setattr(_d, "CONSOLE_SECRET", "", raising=False)


_NO_SCAN = {"status": "none", "found": False, "findings": [],
            "days_ago": None, "fresh": False}

# What the transcript interpreter returns: one layer, its own remedy.
_SPIRIT = {"header": "", "phase": None, "location": "",
           "layers": [{"layer": 1, "head": "Grief", "most_affected": "Grief",
                       "remedy": "Heart Health"}]}


def _app(db, spirit=None):
    return create_app(db, scan_lookup=lambda e: _NO_SCAN,
                      fetch_profile=lambda e: {},
                      fetch_recent_comms=lambda e: {},
                      interpret_complete=lambda s, u: json.dumps(spirit or _SPIRIT))


def _new(client):
    tid = client.post("/author/new").headers["Location"].rstrip("/").split("/")[-1]
    client.post(f"/author/{tid}/header",
                json={"name": "J", "email": "j@x.com", "date": "2026-09-17"})
    client.post(f"/author/{tid}/session", json={"transcript": "she spoke of grief"})
    return tid


def _add_stress(client, tid, label):
    client.post(f"/author/{tid}/stress/add", json={"label": label})


def _chain_len(db, tid):
    with sqlite3.connect(db) as cx:
        n = int(str(tid).lstrip("a") or 0)
        return cx.execute("SELECT COUNT(*) FROM biofield_auth_chain WHERE test_id=?",
                          (n,)).fetchone()[0]


def _stress_count(db):
    with sqlite3.connect(db) as cx:
        return cx.execute("SELECT COUNT(*) FROM biofield_auth_stress").fetchone()[0]


def test_a_proposal_writes_nothing(tmp_path):
    db = str(tmp_path / "c.db")
    client = _app(db).test_client()
    tid = _new(client)
    _add_stress(client, tid, "Fatigue")
    before_chain, before_stress = _chain_len(db, tid), _stress_count(db)
    j = client.post(f"/author/{tid}/program", json={"mode": "full"}).get_json()
    assert j["ok"] and j["proposed"] is True
    assert _chain_len(db, tid) == before_chain, "the proposal wrote to the chain"
    assert _stress_count(db) == before_stress, "the proposal wrote a stress"


def test_a_proposal_runs_spirit_then_mind_then_body(tmp_path):
    client = _app(str(tmp_path / "c.db")).test_client()
    tid = _new(client)
    _add_stress(client, tid, "Fatigue")
    j = client.post(f"/author/{tid}/program", json={"mode": "full"}).get_json()
    assert [s["stage"] for s in j["stages"]] == ["spirit", "mind", "body"]
    assert "Heart Health" in j["remedies"]


def test_the_transcript_layer_is_held_not_written_until_apply(tmp_path):
    db = str(tmp_path / "c.db")
    client = _app(db).test_client()
    tid = _new(client)
    client.post(f"/author/{tid}/program", json={"mode": "full"})
    assert _chain_len(db, tid) == 0
    client.post(f"/author/{tid}/program", json={"mode": "full", "apply": True})
    assert _chain_len(db, tid) >= 1


def test_minimum_never_proposes_more_than_full(tmp_path):
    client = _app(str(tmp_path / "c.db")).test_client()
    tid = _new(client)
    _add_stress(client, tid, "Fatigue")
    full = client.post(f"/author/{tid}/program", json={"mode": "full"}).get_json()
    lean = client.post(f"/author/{tid}/program", json={"mode": "minimum"}).get_json()
    assert set(lean["remedies"]) <= set(full["remedies"])


def test_an_unknown_mode_is_refused(tmp_path):
    client = _app(str(tmp_path / "c.db")).test_client()
    tid = _new(client)
    r = client.post(f"/author/{tid}/program", json={"mode": "whatever"})
    assert r.status_code == 400
    assert _chain_len(str(tmp_path / "c.db"), tid) == 0


def test_layers_already_in_the_chain_are_returned_for_spirit(tmp_path):
    """Glen, 2026-10-03: two layers he had entered read "Spirit: nothing here"."""
    db = str(tmp_path / "c.db")
    client = _app(db).test_client()
    tid = _new(client)
    client.post(f"/author/{tid}/program", json={"mode": "full", "apply": True})
    n = _chain_len(db, tid)
    assert n >= 1
    j = client.post(f"/author/{tid}/program", json={"mode": "minimum"}).get_json()
    assert j["existing_layers"] >= 1
    assert len(j["existing"]) == n
    assert any(e["remedy"] for e in j["existing"])
    assert _chain_len(db, tid) == n          # still a proposal: nothing written


def test_the_page_lists_existing_layers_under_spirit():
    from dashboard import biofield_report_html as R
    import inspect
    src = inspect.getsource(R)
    i = src.index("async function program(mode,inc)")
    body = src[i:src.index("\n}", i)]
    assert "already in the chain" in body
    assert "st.stage==='spirit'" in body
    assert "if(!ex.length&&!st.picks.length&&!st.suppressed.length)" in body


# Glen, 2026-10-24: include or leave out Spirit, Mind and Body before adding the
# program. Default include, except Body when the client's scan is not fresh.
def test_every_section_is_included_unless_told_otherwise(tmp_path):
    client = _app(str(tmp_path / "c.db")).test_client()
    tid = _new(client)
    j = client.post(f"/author/{tid}/program", json={"mode": "minimum"}).get_json()
    assert j["include"] == {"spirit": True, "mind": True, "body": True}


def test_body_reads_not_fresh_without_a_recent_scan(tmp_path):
    client = _app(str(tmp_path / "c.db")).test_client()
    tid = _new(client)
    assert client.post(f"/author/{tid}/program",
                       json={"mode": "minimum"}).get_json()["body_fresh"] is False


def test_body_reads_fresh_with_a_recent_scan(tmp_path):
    fresh = dict(_NO_SCAN, status="fresh", found=True, fresh=True, days_ago=3)
    client = create_app(str(tmp_path / "c.db"), scan_lookup=lambda e: fresh,
                        fetch_profile=lambda e: {}, fetch_recent_comms=lambda e: {},
                        interpret_complete=lambda s, u: json.dumps(_SPIRIT)).test_client()
    tid = _new(client)
    assert client.post(f"/author/{tid}/program",
                       json={"mode": "minimum"}).get_json()["body_fresh"] is True


def test_spirit_left_out_proposes_and_writes_none_of_it(tmp_path):
    db = str(tmp_path / "c.db")
    client = _app(db).test_client()
    tid = _new(client)
    _add_stress(client, tid, "Fatigue")          # a manual stress is a Spirit source
    off = {"spirit": False, "mind": True, "body": True}
    j = client.post(f"/author/{tid}/program",
                    json={"mode": "minimum", "include": off}).get_json()
    assert j["transcript_layers"] == 0 and "Heart Health" not in j["remedies"]
    assert not [s for s in j["stages"] if s["stage"] == "spirit"][0]["picks"]
    client.post(f"/author/{tid}/program",
                json={"mode": "minimum", "apply": True, "force": True, "include": off})
    assert _chain_len(db, tid) == 0


def test_spirit_included_still_writes_the_transcript_layer(tmp_path):
    db = str(tmp_path / "c.db")
    client = _app(db).test_client()
    tid = _new(client)
    client.post(f"/author/{tid}/program",
                json={"mode": "minimum", "apply": True,
                      "include": {"spirit": True, "mind": False, "body": False}})
    assert _chain_len(db, tid) == 1


def test_the_page_has_a_box_per_section_and_sends_the_choice():
    from dashboard import biofield_report_html as R
    import inspect
    src = inspect.getsource(R)
    assert "if(!inc&&j.body_fresh===false)return program(mode,{spirit:true,mind:true,body:false});" in src
    assert "onchange=\\\"programToggle(this)\\\"" in src or "programToggle(this)" in src
    assert "include:_progInc||{spirit:true,mind:true,body:true}});" in src
    assert "if(!on){h+='&nbsp;&nbsp;<span class=food>left out</span><br></div>';return}" in src


def test_a_left_out_sections_stresses_never_reach_the_builder(tmp_path, monkeypatch):
    from dashboard import biofield_program as bp
    seen = []
    real = bp.build_program

    def spy(**kw):
        seen.append(kw)
        return real(**kw)
    monkeypatch.setattr(bp, "build_program", spy)
    client = _app(str(tmp_path / "c.db")).test_client()
    tid = _new(client)
    _add_stress(client, tid, "Fatigue")          # manual: a Spirit source
    client.post(f"/author/{tid}/program", json={"mode": "minimum"})
    assert [s.get("label") for s in seen[-1]["stresses"]] == ["Fatigue"]
    client.post(f"/author/{tid}/program", json={
        "mode": "minimum", "include": {"spirit": False, "mind": False, "body": True}})
    assert seen[-1]["stresses"] == [] and seen[-1]["mind_items"] == []


def _mind_with_scan_codes(tmp_path, monkeypatch, include):
    """Judith Ann Tom, 2026-10-24: a ticked condition whose remedy covers two codes on
    an old scan. Returns the written chain rows as (head, most_affected, remedy)."""
    from dashboard import biofield_clinical_proposals as cp
    from dashboard import biofield_stress as st
    monkeypatch.setattr(cp, "selected_items", lambda cx, tid: [
        {"label": "digestive system issues", "remedies": ["GastroZyme"]}])
    db = str(tmp_path / "c.db")
    client = _app(db).test_client()
    tid = _new(client)
    n = int(str(tid).lstrip("a"))
    with sqlite3.connect(db) as cx:
        st.init_stress_tables(cx)
        for code, label in (("ED8", "Stomach Driver"), ("ER58", "Stomach")):
            cx.execute("INSERT INTO biofield_auth_remedy_coverage (test_id, remedy, code) "
                       "VALUES (?,?,?)", (n, "gastrozyme", code))
            # The scan finding itself, so a leak shows as its label as well as its code.
            cx.execute("INSERT INTO biofield_auth_stress (test_id, code, label, source, "
                       "balance, created_at, updated_at) VALUES (?,?,?,'scan','required',"
                       "'2026-10-25T01:49:24Z','2026-10-25T01:49:24Z')", (n, code, label))
        cx.commit()
    j = client.post(f"/author/{tid}/program",
                    json={"mode": "minimum", "include": include}).get_json()
    proposed = [c for s in j["stages"] for p in s["picks"] for c in p["covers"]]
    client.post(f"/author/{tid}/program", json={"mode": "minimum", "apply": True,
                                                 "force": True, "include": include})
    with sqlite3.connect(db) as cx:
        rows = cx.execute("SELECT head, most_affected, remedy FROM biofield_auth_chain "
                          "WHERE test_id=? ORDER BY layer", (n,)).fetchall()
    return proposed, rows


def test_body_left_out_lets_no_scan_code_in_through_a_mind_remedy(tmp_path, monkeypatch):
    off = {"spirit": False, "mind": True, "body": False}
    proposed, rows = _mind_with_scan_codes(tmp_path, monkeypatch, off)
    assert rows == [("digestive system issues", "digestive system issues", "GastroZyme")]
    assert not {"ED8", "ER58", "Stomach Driver", "Stomach"} & set(proposed)


def test_body_included_still_carries_the_scan_codes(tmp_path, monkeypatch):
    # The fixture's control: with Body in, the same remedy brings its codes along.
    on = {"spirit": False, "mind": True, "body": True}
    proposed, rows = _mind_with_scan_codes(tmp_path, monkeypatch, on)
    assert "Stomach Driver" in proposed and "Stomach Driver" in rows[0][1]


def test_body_left_out_keeps_a_spoken_stresses_own_coverage(tmp_path):
    # Review of #2132: the coverage table also holds normalised labels written for
    # Spirit and Mind stresses. Leaving Body out drops only the scan codes.
    db = str(tmp_path / "c.db")
    client = _app(db).test_client()
    tid = _new(client)
    _add_stress(client, tid, "Fatigue")
    with sqlite3.connect(db) as cx:
        cx.execute("INSERT INTO biofield_auth_remedy_coverage (test_id, remedy, code) "
                   "VALUES (?,?,?)", (int(tid.lstrip("a")), "ignatia", "fatigue"))
        cx.commit()
    j = client.post(f"/author/{tid}/program", json={
        "mode": "full", "include": {"spirit": True, "mind": True, "body": False}}).get_json()
    spirit = next(s for s in j["stages"] if s["stage"] == "spirit")
    assert [p["remedy"] for p in spirit["picks"]] == ["ignatia"]
