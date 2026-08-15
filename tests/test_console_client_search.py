import sqlite3

import app as app_module


def _seed(path):
    from dashboard import client_portal
    with sqlite3.connect(path) as cx:
        cx.execute("CREATE TABLE people (id INTEGER PRIMARY KEY, name TEXT, email TEXT)")
        cx.execute("INSERT INTO people (name,email) VALUES (?,?)", ("Alice Person", "alice@example.com"))
        client_portal.init_client_portal_table(cx)
        client_portal.upsert_portal(cx, "sfnase@hotmail.com", "Steve Fox", {})


def test_client_search_merges_people_and_portal_only_clients(tmp_path, monkeypatch):
    db_path = str(tmp_path / "clients.db")
    _seed(db_path)
    monkeypatch.setattr(app_module, "LOG_DB", db_path)
    monkeypatch.setattr(app_module, "_bos_actor", lambda: object())
    client = app_module.app.test_client()

    steve = client.get("/api/console/client-search?q=steve").get_json()
    assert steve == {"ok": True, "clients": [
        {"email": "sfnase@hotmail.com", "name": "Steve Fox", "has_portal": True}
    ]}

    alice = client.get("/api/console/client-search?q=alice").get_json()
    assert alice["clients"][0]["email"] == "alice@example.com"
    assert alice["clients"][0]["has_portal"] is False


def test_client_search_requires_console_auth(monkeypatch):
    monkeypatch.setattr(app_module, "_bos_actor", lambda: None)
    response = app_module.app.test_client().get("/api/console/client-search?q=steve")
    assert response.status_code == 401
