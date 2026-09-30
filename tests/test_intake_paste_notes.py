# tests/test_intake_paste_notes.py
"""Paste box on the Biofield Intake page (brief 2026-09-30, section 4).

Pasted notes are mined into stresses with source='paste'. The text itself is client
data and must never be stored: only the resulting labels are written.
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


def _new(client):
    return client.post("/author/new").headers["Location"].rstrip("/").split("/")[-1]


def _stresses(client, tid):
    data = client.get(f"/author/{tid}/stresses").get_json()["data"]
    rows = {}
    for s in data["active"] + data["balanced"] + data.get("unassigned", []):
        rows[s["id"]] = s                           # one row per stored stress
    return list(rows.values())


def test_paste_adds_labels_with_paste_source(tmp_path):
    calls = []
    client = _app(str(tmp_path / "p.db"), ["Chronic fatigue", "Poor sleep"], calls).test_client()
    tid = _new(client)
    r = client.post(f"/author/{tid}/mine-paste", json={"text": f"Tired all day. {MARKER}"})
    assert r.status_code == 200 and r.get_json() == {"added": 2}
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
    j = client.post(f"/author/{tid}/mine-paste", json={"text": "notes"}).get_json()
    assert j == {"added": 1}
    labels = [s["label"].lower() for s in _stresses(client, tid)]
    assert labels.count("chronic fatigue") == 1
    # A second paste of the same notes adds nothing.
    assert client.post(f"/author/{tid}/mine-paste", json={"text": "notes"}).get_json() == {"added": 0}


@pytest.mark.parametrize("body", [{"text": ""}, {"text": "   \n\t "}, {}, {"text": 5}])
def test_empty_text_is_400(tmp_path, body):
    calls = []
    client = _app(str(tmp_path / "p.db"), ["X"], calls).test_client()
    tid = _new(client)
    r = client.post(f"/author/{tid}/mine-paste", json=body)
    assert r.status_code == 400
    assert r.get_json()["added"] == 0 and r.get_json()["error"]
    assert calls == []


def test_over_cap_is_400(tmp_path):
    calls = []
    client = _app(str(tmp_path / "p.db"), ["X"], calls).test_client()
    tid = _new(client)
    r = client.post(f"/author/{tid}/mine-paste", json={"text": "a" * 50_001})
    assert r.status_code == 400 and "50,000" in r.get_json()["error"]
    assert calls == []
    ok = client.post(f"/author/{tid}/mine-paste", json={"text": "a" * 50_000})
    assert ok.status_code == 200


def test_pasted_text_is_not_stored(tmp_path, capsys):
    db = str(tmp_path / "p.db")
    client = _app(db, ["Poor sleep"]).test_client()
    tid = _new(client)
    r = client.post(f"/author/{tid}/mine-paste",
                    json={"text": f"Phone note: {MARKER} sleeps badly"})
    assert r.get_json() == {"added": 1}
    hits = []
    with sqlite3.connect(db) as cx:
        tables = [t[0] for t in cx.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
        assert "biofield_auth_stress" in tables     # the search reached the real store
        for t in tables:
            cols = [c[1] for c in cx.execute(f'PRAGMA table_info("{t}")').fetchall()]
            for c in cols:
                n = cx.execute(f'SELECT COUNT(*) FROM "{t}" WHERE CAST("{c}" AS TEXT) LIKE ?',
                               (f"%{MARKER}%",)).fetchone()[0]
                if n:
                    hits.append((t, c))
    assert hits == []
    for f in os.listdir(tmp_path):                  # db, journal, wal, anything else
        with open(tmp_path / f, "rb") as fh:
            assert MARKER.encode() not in fh.read(), f
    out = capsys.readouterr()
    assert MARKER not in out.out and MARKER not in out.err


def test_interpreter_exception_returns_error(tmp_path):
    client = _app(str(tmp_path / "p.db"), raises=RuntimeError("model down")).test_client()
    tid = _new(client)
    r = client.post(f"/author/{tid}/mine-paste", json={"text": "notes"})
    j = r.get_json()
    assert j["added"] == 0 and "model down" in j["error"]


def test_page_has_paste_box_and_posts_to_route():
    from dashboard.biofield_report_html import render_author_html
    rep = {"test_id": "a7", "client": {"name": "J", "email": "j@x.com"}, "date": "",
           "layers": [], "schedule": []}
    h = render_author_html(rep, [], "")
    assert "Paste notes (emails, phone notes)" in h
    assert "<textarea id=pasteNotes" in h
    assert "Find stresses in pasted notes" in h and "onclick=minePaste()" in h
    assert "function minePaste" in h and "/author/a7/mine-paste" in h
    body = h[h.index("function minePaste"):]
    body = body[:body.index("\nasync function", 1)]
    assert "loadStress()" in body and "box.value=''" in body
    assert "Pasted notes" in h                        # the source's plain label


def test_paste_source_is_in_the_mind_stage():
    from dashboard.biofield_program import MIND_SOURCES
    assert "paste" in MIND_SOURCES
