"""Console page for reviewing practitioner profile drafts.

Backend endpoints already have coverage in test_practitioner_review_console.py.
This file covers: the page itself serves, the queue endpoint carries a display
name (best-effort, degrading to the id on any lookup failure), the page wires
up all three endpoints, and the page follows Glen's copy rules.
"""
import os
import re

import pytest

if not os.environ.get("PINECONE_API_KEY"):
    pytest.skip("needs doppler env for import app", allow_module_level=True)

import app as appmod

PID = "11111111-1111-1111-1111-111111111111"

BANNED_PHRASES = [
    "one honest thing",
    "to be honest",
    "honestly",
    "i'll be candid",
]


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(appmod, "CONSOLE_SECRET", "s3cret")
    monkeypatch.setitem(appmod.app.config, "TESTING", True)
    return appmod.app.test_client()


def _page_html():
    path = os.path.join(os.path.dirname(appmod.__file__), "static",
                        "console-practitioner-drafts.html")
    with open(path, encoding="utf-8") as f:
        return f.read()


def test_page_serves_html(client):
    r = client.get("/console/practitioner-drafts")
    assert r.status_code == 200
    assert "text/html" in r.content_type
    body = r.get_data(as_text=True)
    assert "<html" in body.lower()


def test_queue_returns_display_name_alongside_practitioner_id(client, monkeypatch, tmp_path):
    monkeypatch.setattr(appmod, "_console_key_ok", lambda: True)
    monkeypatch.setattr(appmod, "LOG_DB", str(tmp_path / "chat_log.db"))
    monkeypatch.setattr(appmod, "_practitioner_display_name", lambda pid: "Dr. Ashley Price")

    def _fake_list(cx, status=None, limit=200):
        return [{"practitioner_id": PID, "status": "submitted", "fields": {"bio": "x"}}]

    monkeypatch.setattr("dashboard.practitioner_drafts.list_by_status", _fake_list)
    r = client.get("/api/console/practitioner-drafts")
    assert r.status_code == 200
    draft = r.get_json()["drafts"][0]
    assert draft["practitioner_id"] == PID
    assert draft["display_name"] == "Dr. Ashley Price"


def test_queue_still_returns_rows_when_the_name_lookup_raises(client, monkeypatch, tmp_path):
    """A lookup failure must degrade to the id, never break the queue."""
    monkeypatch.setattr(appmod, "_console_key_ok", lambda: True)
    monkeypatch.setattr(appmod, "LOG_DB", str(tmp_path / "chat_log.db"))

    def _boom(pid):
        raise RuntimeError("supabase unreachable")

    monkeypatch.setattr(appmod, "_practitioner_display_name", _boom)

    def _fake_list(cx, status=None, limit=200):
        return [{"practitioner_id": PID, "status": "submitted", "fields": {"bio": "x"}}]

    monkeypatch.setattr("dashboard.practitioner_drafts.list_by_status", _fake_list)
    r = client.get("/api/console/practitioner-drafts")
    assert r.status_code == 200
    drafts = r.get_json()["drafts"]
    assert len(drafts) == 1
    assert drafts[0]["practitioner_id"] == PID
    assert drafts[0]["display_name"] is None


def test_page_references_all_three_endpoints():
    html = _page_html()
    assert "/api/console/practitioner-drafts" in html
    assert "/approve" in html
    assert "/reject" in html


def test_page_has_no_em_dash():
    html = _page_html()
    assert "—" not in html


def test_page_has_no_banned_honesty_phrases():
    html = _page_html().lower()
    for phrase in BANNED_PHRASES:
        assert phrase not in html, f"banned phrase found: {phrase!r}"


def test_page_has_no_uppercase_text_transform():
    html = _page_html()
    assert not re.search(r"text-transform\s*:\s*uppercase", html, re.IGNORECASE)
