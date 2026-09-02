"""POST /author/<id>/e4l/import-reveal imports synthesized reveal layers as
needs-review chain rows. synthesize_reveal_layers is monkeypatched so the test
never runs the real vault pipeline; import_layers_to_test runs for real on a tmp db."""
import sqlite3
import pytest

from biofield_local_app import create_app
import dashboard.biofield_reveal_import as RI


@pytest.fixture(autouse=True)
def _no_gate(monkeypatch):
    monkeypatch.delenv("CONSOLE_SECRET", raising=False)
    # dashboard/__init__.py captures CONSOLE_SECRET at import; reloading
    # app does not reset it, so clear the copy the guard actually reads.
    import dashboard as _d; monkeypatch.setattr(_d, "CONSOLE_SECRET", "", raising=False)


_FRESH = {"found": True, "scan_id": 900, "scan_date": "2026-06-22", "days_ago": 3,
          "fresh": True, "layers": [
              {"n": 1, "title": "Oxidative load", "summary": "",
               "most_affected": "Cell membrane", "remedy_name": "Neuro Magnesium"}]}
_STALE = {"found": True, "scan_id": 900, "scan_date": "2026-06-01", "days_ago": 24,
          "fresh": False, "layers": []}
_NONE = {"found": False, "scan_id": None, "scan_date": None, "days_ago": None,
         "fresh": False, "layers": []}


def _new_test_with_email(client, email):
    tid = client.post("/author/new").headers["Location"].rstrip("/").split("/")[-1]
    client.post(f"/author/{tid}/header", json={"name": "Jane", "email": email,
                                               "date": "2026-06-25"})
    return tid


def test_import_writes_rows_when_fresh(tmp_path, monkeypatch):
    monkeypatch.setattr(RI, "synthesize_reveal_layers", lambda *a, **k: _FRESH)
    db = str(tmp_path / "chat_log.db")
    client = create_app(db, scan_lookup=lambda e: _NONE).test_client()
    tid = _new_test_with_email(client, "jane@x.com")
    j = client.post(f"/author/{tid}/e4l/import-reveal", json={}).get_json()
    assert j["ok"] is True and j["imported"] == 1
    # row landed, unconfirmed
    cx = sqlite3.connect(db)
    row = cx.execute("SELECT remedy, confirmed FROM biofield_auth_chain").fetchone()
    assert row[0] == "Neuro Magnesium" and row[1] == 0


def test_import_rejects_stale_scan(tmp_path, monkeypatch):
    monkeypatch.setattr(RI, "synthesize_reveal_layers", lambda *a, **k: _STALE)
    db = str(tmp_path / "chat_log.db")
    client = create_app(db, scan_lookup=lambda e: _NONE).test_client()
    tid = _new_test_with_email(client, "jane@x.com")
    j = client.post(f"/author/{tid}/e4l/import-reveal", json={}).get_json()
    assert j["ok"] is False and "24" in j["reason"]


def test_import_accepts_stale_scan_only_with_explicit_override(tmp_path, monkeypatch):
    stale_with_layers = dict(_STALE, layers=_FRESH["layers"])
    monkeypatch.setattr(RI, "synthesize_reveal_layers", lambda *a, **k: stale_with_layers)
    db = str(tmp_path / "chat_log.db")
    client = create_app(db, scan_lookup=lambda e: _NONE).test_client()
    tid = _new_test_with_email(client, "jane@x.com")
    j = client.post(f"/author/{tid}/e4l/import-reveal",
                    json={"allow_stale": True}).get_json()
    assert j["ok"] is True and j["imported"] == 1


def test_request_fresh_scan_emails_selected_client(tmp_path):
    sent = []
    def send(email, name):
        sent.append((email, name))
        return True
    db = str(tmp_path / "chat_log.db")
    client = create_app(db, scan_lookup=lambda e: _NONE,
                        scan_request_email=send).test_client()
    tid = _new_test_with_email(client, "jane@x.com")
    j = client.post(f"/author/{tid}/e4l/request-fresh", json={}).get_json()
    assert j == {"ok": True, "email": "jane@x.com"}
    assert sent == [("jane@x.com", "Jane")]


def test_import_needs_confirm_then_appends_with_force(tmp_path, monkeypatch):
    monkeypatch.setattr(RI, "synthesize_reveal_layers", lambda *a, **k: _FRESH)
    db = str(tmp_path / "chat_log.db")
    client = create_app(db, scan_lookup=lambda e: _NONE).test_client()
    tid = _new_test_with_email(client, "jane@x.com")
    client.post(f"/author/{tid}/e4l/import-reveal", json={})          # first import (1 row)
    j = client.post(f"/author/{tid}/e4l/import-reveal", json={}).get_json()
    assert j == {"ok": False, "needs_confirm": True, "existing": 1,
                 "existing_rows": 1, "existing_layers": 1}
    j2 = client.post(f"/author/{tid}/e4l/import-reveal", json={"force": True}).get_json()
    assert j2["ok"] is True and j2["imported"] == 1
    cx = sqlite3.connect(db)
    assert cx.execute("SELECT COUNT(*) FROM biofield_auth_chain").fetchone()[0] == 2


def test_forced_import_appends_below_existing_layer_with_multiple_remedies(tmp_path, monkeypatch):
    reveal = dict(_FRESH, layers=[
        {"n": 1, "title": "Reveal one", "remedy_name": "Neuroprotect"},
        {"n": 2, "title": "Reveal two", "remedy_name": "Magnesium"},
    ])
    monkeypatch.setattr(RI, "synthesize_reveal_layers", lambda *a, **k: reveal)
    db = str(tmp_path / "chat_log.db")
    client = create_app(db, scan_lookup=lambda e: _NONE).test_client()
    tid = _new_test_with_email(client, "jane@x.com")
    with sqlite3.connect(db) as cx:
        from dashboard.biofield_authoring import add_chain_row
        add_chain_row(cx, tid, 1, "Glaucoma", "", "OcuFlow Bedtime")
        add_chain_row(cx, tid, 1, "", "", "IOP Syntropy")
    prompt = client.post(f"/author/{tid}/e4l/import-reveal", json={}).get_json()
    assert prompt["existing_layers"] == 1 and prompt["existing_rows"] == 2
    done = client.post(f"/author/{tid}/e4l/import-reveal", json={"force": True}).get_json()
    assert done["ok"] is True
    with sqlite3.connect(db) as cx:
        rows = cx.execute("SELECT layer,remedy FROM biofield_auth_chain ORDER BY layer,id").fetchall()
    assert rows == [(1, "OcuFlow Bedtime"), (1, "IOP Syntropy"),
                    (2, "Neuroprotect"), (3, "Magnesium")]


def test_import_no_client_email(tmp_path, monkeypatch):
    monkeypatch.setattr(RI, "synthesize_reveal_layers", lambda *a, **k: _FRESH)
    db = str(tmp_path / "chat_log.db")
    client = create_app(db, scan_lookup=lambda e: _NONE).test_client()
    tid = client.post("/author/new").headers["Location"].rstrip("/").split("/")[-1]
    j = client.post(f"/author/{tid}/e4l/import-reveal", json={}).get_json()
    assert j["ok"] is False and "client" in j["reason"].lower()


def test_import_handles_synthesis_failure(tmp_path, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("pinecone down")
    monkeypatch.setattr(RI, "synthesize_reveal_layers", boom)
    db = str(tmp_path / "chat_log.db")
    client = create_app(db, scan_lookup=lambda e: _NONE).test_client()
    tid = _new_test_with_email(client, "jane@x.com")
    r = client.post(f"/author/{tid}/e4l/import-reveal", json={})
    assert r.status_code == 200
    j = r.get_json()
    assert j["ok"] is False and "fail" in j["reason"].lower()
