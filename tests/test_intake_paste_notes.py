# tests/test_intake_paste_notes.py
"""Paste box on the Biofield Intake page (brief 2026-09-30, section 4).

Pasted notes are mined into stresses with source='paste'. The text itself is client
data and must never be stored, logged or echoed: only the resulting labels are written.
No model is called: interpret_complete is a stub in every test."""
import json
import os
import sqlite3

import pytest

from biofield_local_app import create_app

MARKER = "ZQX-PASTE-MARKER-7731"


@pytest.fixture(autouse=True)
def _no_gate(monkeypatch):
    monkeypatch.delenv("CONSOLE_SECRET", raising=False)
    import dashboard as _d
    monkeypatch.setattr(_d, "CONSOLE_SECRET", "", raising=False)


_NONE = {"status": "none", "found": False, "findings": [], "days_ago": None, "fresh": False}


def _app(db, stresses=(), calls=None, raises=None):
    def complete(system, user):
        if calls is not None:
            calls.append(user)
        if raises:
            raise raises
        return json.dumps({"stresses": list(stresses)})
    return create_app(db, scan_lookup=lambda e: _NONE, fetch_profile=lambda e: {},
                      fetch_recent_comms=lambda e: {}, interpret_complete=complete)


def _new(client, email="j@x.com"):
    tid = client.post("/author/new").headers["Location"].rstrip("/").split("/")[-1]
    if email:
        client.post(f"/author/{tid}/header", json={"name": "J", "email": email,
                                                    "date": "2026-06-25"})
    return tid


def _stresses(client, tid):
    data = client.get(f"/author/{tid}/stresses").get_json()["data"]
    rows = {}
    for s in data["active"] + data["balanced"] + data.get("unassigned", []):
        rows[s["id"]] = s                           # one row per stored stress
    return list(rows.values())


def _all_text_hits(db, needle):
    hits = []
    with sqlite3.connect(db) as cx:
        tables = [t[0] for t in cx.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
        assert "biofield_auth_stress" in tables     # the search reached the real store
        for t in tables:
            for c in [c[1] for c in cx.execute(f'PRAGMA table_info("{t}")').fetchall()]:
                n = cx.execute(f'SELECT COUNT(*) FROM "{t}" WHERE CAST("{c}" AS TEXT) LIKE ?',
                               (f"%{needle}%",)).fetchone()[0]
                if n:
                    hits.append((t, c))
    return hits


def _stress_count(db):
    with sqlite3.connect(db) as cx:
        try:
            return cx.execute("SELECT COUNT(*) FROM biofield_auth_stress").fetchone()[0]
        except sqlite3.OperationalError:
            return 0


def test_paste_adds_labels_with_paste_source(tmp_path):
    calls = []
    client = _app(str(tmp_path / "p.db"), ["Chronic fatigue", "Poor sleep"], calls).test_client()
    tid = _new(client)
    r = client.post(f"/author/{tid}/mine-paste", json={"text": f"Tired all day. {MARKER}"})
    assert r.status_code == 200 and r.get_json() == {"added": 2, "skipped": 0}
    assert calls and MARKER in calls[0]          # the pasted text reached the interpreter
    rows = {(s["label"], s["source"]) for s in _stresses(client, tid)}
    assert {("Chronic fatigue", "paste"), ("Poor sleep", "paste")} <= rows


def test_label_already_on_test_is_not_added_twice(tmp_path):
    db = str(tmp_path / "p.db")
    client = _app(db, ["Chronic fatigue", "Poor sleep"]).test_client()
    tid = _new(client)
    from dashboard import biofield_stress as _st
    with sqlite3.connect(db) as cx:
        assert _st.add_stress(cx, tid, "chronic fatigue", source="comm")
    assert client.post(f"/author/{tid}/mine-paste", json={"text": "notes"}).get_json()["added"] == 1
    labels = [s["label"].lower() for s in _stresses(client, tid)]
    assert labels.count("chronic fatigue") == 1
    assert client.post(f"/author/{tid}/mine-paste", json={"text": "notes"}).get_json()["added"] == 0


@pytest.mark.parametrize("body", [{"text": ""}, {"text": "   \n\t "}, {}, {"text": 5}])
def test_empty_text_is_400(tmp_path, body):
    calls = []
    client = _app(str(tmp_path / "p.db"), ["X"], calls).test_client()
    tid = _new(client)
    r = client.post(f"/author/{tid}/mine-paste", json=body)
    assert r.status_code == 400
    assert r.get_json()["added"] == 0 and r.get_json()["error"]
    assert calls == []


@pytest.mark.parametrize("raw", ["[1, 2]", '"just a string"', "42", "null", "{not json"])
def test_non_object_body_is_400(tmp_path, raw):
    calls = []
    client = _app(str(tmp_path / "p.db"), ["X"], calls).test_client()
    tid = _new(client)
    r = client.post(f"/author/{tid}/mine-paste", data=raw, content_type="application/json")
    assert r.status_code == 400 and r.get_json()["error"]
    assert calls == []


def test_over_cap_is_400(tmp_path):
    calls = []
    client = _app(str(tmp_path / "p.db"), ["X"], calls).test_client()
    tid = _new(client)
    r = client.post(f"/author/{tid}/mine-paste", json={"text": "a" * 50_001})
    assert r.status_code == 400 and "50,000" in r.get_json()["error"]
    assert calls == []
    assert client.post(f"/author/{tid}/mine-paste", json={"text": "a" * 50_000}).status_code == 200


def test_no_client_email_refuses(tmp_path):
    db = str(tmp_path / "p.db")
    calls = []
    client = _app(db, ["Poor sleep"], calls).test_client()
    tid = _new(client, email=None)
    j = client.post(f"/author/{tid}/mine-paste", json={"text": "notes"}).get_json()
    assert j == {"added": 0, "error": "No client selected yet"}
    assert calls == [] and _stress_count(db) == 0


@pytest.mark.parametrize("tid", ["a99999", "0", "-1", "a0", "abc"])
def test_unknown_test_refuses_and_writes_nothing(tmp_path, tid):
    db = str(tmp_path / "p.db")
    calls = []
    client = _app(db, ["Poor sleep"], calls).test_client()
    _new(client)                                    # a real test exists alongside
    r = client.post(f"/author/{tid}/mine-paste", json={"text": "notes"})
    assert r.status_code == 404 and r.get_json()["added"] == 0
    assert calls == [] and _stress_count(db) == 0


def test_pasted_text_is_not_stored(tmp_path, capsys):
    db = str(tmp_path / "p.db")
    client = _app(db, ["Poor sleep"]).test_client()
    tid = _new(client)
    r = client.post(f"/author/{tid}/mine-paste",
                    json={"text": f"Phone note: {MARKER} sleeps badly"})
    assert r.get_json()["added"] == 1
    assert _all_text_hits(db, MARKER) == []
    for f in os.listdir(tmp_path):                  # db, journal, wal, anything else
        with open(tmp_path / f, "rb") as fh:
            assert MARKER.encode() not in fh.read(), f
    out = capsys.readouterr()
    assert MARKER not in out.out and MARKER not in out.err


def test_interpreter_exception_is_not_echoed(tmp_path):
    client = _app(str(tmp_path / "p.db"),
                  raises=RuntimeError(f"bad json near: {MARKER} sleeps badly")).test_client()
    tid = _new(client)
    r = client.post(f"/author/{tid}/mine-paste", json={"text": f"{MARKER} sleeps badly"})
    j = r.get_json()
    assert j["added"] == 0 and j["error"] == "Could not read the notes. Nothing more was added."
    assert MARKER not in r.get_data(as_text=True)


def test_partial_failure_reports_adds_so_far(tmp_path, monkeypatch):
    db = str(tmp_path / "p.db")
    client = _app(db, ["Poor sleep", "Chronic fatigue", "Headache"]).test_client()
    tid = _new(client)
    from dashboard import biofield_stress as _st
    real, n = _st.add_stress, {"calls": 0}

    def flaky(cx, t, label, **kw):
        n["calls"] += 1
        if n["calls"] == 2:
            raise sqlite3.OperationalError(f"insert failed for {MARKER}")
        return real(cx, t, label, **kw)
    monkeypatch.setattr(_st, "add_stress", flaky)
    r = client.post(f"/author/{tid}/mine-paste", json={"text": "notes"})
    j = r.get_json()
    assert j["added"] == 1 and j["error"] == "Could not read the notes. Nothing more was added."
    assert MARKER not in r.get_data(as_text=True)
    monkeypatch.setattr(_st, "add_stress", real)
    assert [s["label"] for s in _stresses(client, tid)] == ["Poor sleep"]


def test_label_echoing_the_notes_is_skipped(tmp_path):
    db = str(tmp_path / "p.db")
    sentence = f"Agnes wrote that {MARKER} and she has not slept well for weeks now since June"
    assert len(sentence) > 80
    client = _app(db, ["Poor sleep", sentence, f"Short {MARKER}\nsecond line"]).test_client()
    tid = _new(client)
    j = client.post(f"/author/{tid}/mine-paste", json={"text": sentence}).get_json()
    assert j == {"added": 1, "skipped": 2}
    assert _all_text_hits(db, MARKER) == []
    assert [s["label"] for s in _stresses(client, tid)] == ["Poor sleep"]


def test_page_has_paste_box_and_posts_to_route():
    from dashboard.biofield_report_html import render_author_html
    rep = {"test_id": "a7", "client": {"name": "J", "email": "j@x.com"}, "date": "",
           "layers": [], "schedule": []}
    h = render_author_html(rep, [], "")
    assert "Paste notes (emails, phone notes)" in h
    assert "<textarea id=pasteNotes" in h
    box = h[h.index("<textarea id=pasteNotes"):]
    assert "autocomplete=off" in box[:box.index(">")]
    assert "Find stresses in pasted notes" in h and "onclick=minePaste(this)" in h
    assert "function minePaste" in h and "/author/a7/mine-paste" in h
    body = h[h.index("function minePaste"):]
    body = body[:body.index("\nasync function", 1)]
    assert "btn.disabled=true" in body
    assert "finally{if(btn)btn.disabled=false}" in body
    assert "if(box.value===text)box.value=''" in body
    # loadStress() runs whenever something was added, before the error branch returns.
    assert body.index("if(j.added>0)loadStress()") < body.index("if(j.error)")
    assert "Pasted notes" in h                        # the source's plain label


def test_paste_source_is_in_the_mind_stage():
    from dashboard.biofield_program import MIND_SOURCES
    assert "paste" in MIND_SOURCES
