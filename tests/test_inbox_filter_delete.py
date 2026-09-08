"""Deleting a Gmail filter, and recording enough to rebuild it.

`scripts/inbox_triage.py` could create filters and never remove one. A tool
that can dig a hole it cannot fill is how three category filters ended up
archiving mail on arrival with nobody able to undo them.

Deleting a filter is irreversible: Gmail keeps no history and its delete call
returns an empty body. So the delete reads the filter FIRST and returns its
definition, which is everything needed to recreate it. That record is the undo.
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:
    import app, dashboard
    from dashboard import inbox as _inbox
except Exception as e:  # pragma: no cover
    pytest.skip(f"app import needs secrets: {e}", allow_module_level=True)


FILTER = {"id": "f_promo",
          "criteria": {"query": "category:promotions"},
          "action": {"removeLabelIds": ["INBOX"]}}


def _auth(mp):
    for obj in (app, dashboard):
        mp.setattr(obj, "CONSOLE_SECRET", "sek", raising=False)


def _fake_service(existing, deleted):
    """A stand-in for the Gmail service, shaped like the real call chain."""
    class _Filters:
        def list(self, userId=None):
            return _Exec({"filter": list(existing.values())})
        def delete(self, userId=None, id=None):
            # Deliberately does NOT raise on an unknown id. Gmail's own delete
            # is not guaranteed to, and a fake that raises would pass the
            # bad-id test even with the guard removed: it would be testing the
            # fake rather than the code. Verified by mutation.
            deleted.append(id)
            existing.pop(id, None)
            return _Exec("")
    class _Exec:
        def __init__(self, v): self.v = v
        def execute(self): return self.v
    class _Settings:
        def filters(self): return _Filters()
    class _Users:
        def settings(self): return _Settings()
    class _Svc:
        def users(self): return _Users()
    return _Svc()


# ── The delete returns what it destroyed ─────────────────────────────────────

def test_delete_filter_returns_the_definition_it_removed(monkeypatch):
    existing, deleted = {"f_promo": FILTER}, []
    monkeypatch.setattr(_inbox, "_get_gmail_service",
                        lambda: _fake_service(existing, deleted))

    removed = _inbox.delete_filter("f_promo")

    assert deleted == ["f_promo"], f"filter was not deleted, got {deleted}"
    assert removed["criteria"] == {"query": "category:promotions"}, removed
    assert removed["action"] == {"removeLabelIds": ["INBOX"]}, removed


def test_delete_filter_refuses_an_id_that_does_not_exist(monkeypatch):
    """A delete that silently succeeds on a bad id reads as 'already gone'."""
    existing, deleted = {"f_promo": FILTER}, []
    monkeypatch.setattr(_inbox, "_get_gmail_service",
                        lambda: _fake_service(existing, deleted))

    # Named explicitly: pytest.raises(Exception) would otherwise be satisfied by
    # the AttributeError from delete_filter not existing at all.
    assert hasattr(_inbox, "delete_filter"), "delete_filter is not implemented"

    with pytest.raises(ValueError):
        _inbox.delete_filter("f_does_not_exist")

    assert deleted == [], f"deleted something on a bad id: {deleted}"
    assert "f_promo" in existing, "an unrelated filter was removed"


# ── The route ────────────────────────────────────────────────────────────────

def test_route_deletes_and_hands_back_the_definition(monkeypatch):
    _auth(monkeypatch)
    monkeypatch.setattr(_inbox, "delete_filter", lambda fid: dict(FILTER, id=fid))

    r = app.app.test_client().delete(
        "/api/inbox/filters/f_promo", headers={"X-Console-Key": "sek"})
    body = r.get_json()

    assert body.get("ok") is True, body
    assert body["data"]["deleted"]["criteria"]["query"] == "category:promotions", body


def test_route_needs_the_console_key(monkeypatch):
    _auth(monkeypatch)
    monkeypatch.setattr(_inbox, "delete_filter",
                        lambda fid: pytest.fail("deleted without a key"))
    r = app.app.test_client().delete("/api/inbox/filters/f_promo")
    assert r.status_code == 401, r.status_code


def test_route_does_not_delete_on_a_GET(monkeypatch):
    """GET /api/inbox/filters lists. It must never destroy anything."""
    _auth(monkeypatch)
    monkeypatch.setattr(_inbox, "delete_filter",
                        lambda fid: pytest.fail("a GET deleted a filter"))
    monkeypatch.setattr(_inbox, "list_filters", lambda: [FILTER])
    r = app.app.test_client().get(
        "/api/inbox/filters", headers={"X-Console-Key": "sek"})
    assert r.get_json().get("ok") is True
