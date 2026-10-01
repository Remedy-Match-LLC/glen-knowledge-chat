"""GET /api/people/<id>/attributes: canonical attributes WITH their source.

Isolated: monkeypatches app.LOG_DB to a temp sqlite db and app.CONSOLE_SECRET.
Run via:
  doppler run -p remedy-match -c prd -- env DATA_DIR="$HOME/deploy-chat" \
    ~/.venvs/deploy-chat311/bin/python -m pytest tests/test_people_attributes_api.py
"""
import os
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

if not os.environ.get("PINECONE_API_KEY"):
    pytest.skip("requires app env (use doppler run)", allow_module_level=True)

import app  # noqa: E402


@pytest.fixture
def client(tmp_path, monkeypatch):
    p = str(tmp_path / "chat_log.db")
    with sqlite3.connect(p) as cx:
        cx.execute("CREATE TABLE people (id INTEGER PRIMARY KEY AUTOINCREMENT,"
                   " email TEXT UNIQUE, tags TEXT DEFAULT '[]')")
        cx.execute("INSERT INTO people(id, email) VALUES (7, 'A@x.com')")
        cx.execute("INSERT INTO people(id, email) VALUES (8, 'b@x.com')")
        from dashboard import canonical_tags as ct
        ct.init_tables(cx)
        cx.executemany(
            "INSERT INTO person_attributes(email, field, value, value_norm, source, added_at)"
            " VALUES (?,?,?,?,?,?)", [
                ("a@x.com", "conditions", "Glaucoma", "glaucoma", "document:41", "t"),
                ("a@x.com", "conditions", "Acid reflux", "acid reflux", "console", "t"),
                ("b@x.com", "conditions", "Gout", "gout", "document:9", "t")])
        cx.commit()
    monkeypatch.setattr(app, "LOG_DB", p)
    monkeypatch.setattr(app, "CONSOLE_SECRET", "testkey")
    return app.app.test_client()


def test_returns_this_persons_attributes_with_source(client):
    r = client.get("/api/people/7/attributes", headers={"X-Console-Key": "testkey"})
    assert r.status_code == 200
    assert r.get_json() == {"attributes": [
        {"field": "conditions", "value": "Acid reflux", "source": "console"},
        {"field": "conditions", "value": "Glaucoma", "source": "document:41"}]}


def test_key_only_in_the_header(client):
    assert client.get("/api/people/7/attributes").status_code == 401
    assert client.get("/api/people/7/attributes?key=testkey").status_code == 401
    assert client.get("/api/people/7/attributes",
                      headers={"X-Console-Key": "wrong"}).status_code == 401


def test_unknown_person_is_404(client):
    assert client.get("/api/people/99/attributes",
                      headers={"X-Console-Key": "testkey"}).status_code == 404
