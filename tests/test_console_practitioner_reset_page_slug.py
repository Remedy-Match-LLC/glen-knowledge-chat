"""Route-level tests for POST /api/console/practitioner/reset-page-slug.

set_page_slug is reachable only from a practitioner's own session, so an
unsuitable or squatted page_slug had no path back except hand-writing SQL
against production. This route calls the same setter with an empty
candidate, gated by the same console-key auth every other /api/console/*
admin route uses.

Mirrors test_affiliate_journey.py's conventions: _load_app() for import,
LOG_DB pointed at a tmp path, the production writer + setter used to seed
state.
"""

import importlib
import sqlite3
import sys
from pathlib import Path

import pytest


def _load_app():
    repo_root = Path(__file__).resolve().parent.parent
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
    try:
        return importlib.import_module("app")
    except Exception as e:
        pytest.skip(f"app module not importable in this env: {e}")


def _bootstrap_db(app_module, db_path):
    orig = app_module.LOG_DB
    app_module.LOG_DB = db_path
    app_module._init_referral_tables()
    app_module.LOG_DB = orig


def _seed_practitioner_with_vanity(db_path, email, vanity):
    """Seed an approved practitioner who has renamed her public URL to
    `vanity`, using the production writer + setter -- the same helper
    test_affiliate_journey.py uses -- so the row is in exactly the state a
    real rename leaves.

    Named "Dr. Mary R Boyd" rather than "Mary Boyd" so the name-derived
    AFFILIATE slug ("dr-mary-r-boyd") is distinct from the vanity name
    ("mary-boyd") -- otherwise "mary-boyd" would be permanently taken as her
    own affiliate slug and every "is it free now" assertion would be
    meaningless.
    """
    from dashboard import affiliate_dashboard as _ad
    from dashboard import practitioner_slugs as _ps
    cx = sqlite3.connect(db_path)
    row = _ad.ensure_affiliate(cx, email, name="Dr. Mary R Boyd")
    assert row, "could not seed the practitioner"
    assert row["slug"] != vanity, "name-derived affiliate slug collides with the test's vanity name"
    _ps.set_page_slug(cx, row["slug"], vanity, reserved=frozenset())
    cx.close()
    return row["slug"]


def _auth(app_module, monkeypatch, secret="sek"):
    monkeypatch.setattr(app_module, "CONSOLE_SECRET", secret, raising=False)


def test_reset_unauthenticated_is_refused(monkeypatch, tmp_path):
    app_module = _load_app()
    dbp = str(tmp_path / "chat_log.db")
    monkeypatch.setattr(app_module, "LOG_DB", dbp)
    _bootstrap_db(app_module, dbp)
    _auth(app_module, monkeypatch)
    owner = _seed_practitioner_with_vanity(dbp, "mary@test.com", "mary-boyd")

    # No X-Console-Key header at all.
    r = app_module.app.test_client().post(
        "/api/console/practitioner/reset-page-slug",
        json={"affiliate_slug": owner})
    assert r.status_code == 401, r.get_data(as_text=True)

    # And a wrong key is refused the same way.
    r2 = app_module.app.test_client().post(
        "/api/console/practitioner/reset-page-slug",
        json={"affiliate_slug": owner},
        headers={"X-Console-Key": "not-the-secret"})
    assert r2.status_code == 401

    # Nothing changed: she still holds her vanity URL.
    with sqlite3.connect(dbp) as cx:
        row = cx.execute("SELECT page_slug FROM affiliate_signups WHERE slug=?",
                         (owner,)).fetchone()
    assert row[0] == "mary-boyd"


def test_reset_by_affiliate_slug_restores_and_frees_vanity(monkeypatch, tmp_path):
    app_module = _load_app()
    dbp = str(tmp_path / "chat_log.db")
    monkeypatch.setattr(app_module, "LOG_DB", dbp)
    _bootstrap_db(app_module, dbp)
    _auth(app_module, monkeypatch)
    owner = _seed_practitioner_with_vanity(dbp, "mary@test.com", "mary-boyd")

    r = app_module.app.test_client().post(
        "/api/console/practitioner/reset-page-slug",
        json={"affiliate_slug": owner},
        headers={"X-Console-Key": "sek"})
    assert r.status_code == 200, r.get_data(as_text=True)
    body = r.get_json()
    assert body["ok"] is True
    assert body["affiliate_slug"] == owner
    assert body["page_slug"] == owner

    with sqlite3.connect(dbp) as cx:
        row = cx.execute("SELECT page_slug FROM affiliate_signups WHERE slug=?",
                         (owner,)).fetchone()
        assert row[0] == owner, "her public URL must be her own affiliate slug again"

        # The vanity name is free: someone else can now claim it as HER page
        # slug via the real setter, going through the real namespace guards.
        from dashboard import affiliate_dashboard as _ad
        from dashboard import practitioner_slugs as _ps
        other = _ad.ensure_affiliate(cx, "someone.else@test.com", name="Someone Else")
        stored = _ps.set_page_slug(cx, other["slug"], "mary-boyd", reserved=frozenset())
        assert stored == "mary-boyd"


def test_reset_by_email_targets_correct_row(monkeypatch, tmp_path):
    app_module = _load_app()
    dbp = str(tmp_path / "chat_log.db")
    monkeypatch.setattr(app_module, "LOG_DB", dbp)
    _bootstrap_db(app_module, dbp)
    _auth(app_module, monkeypatch)
    owner = _seed_practitioner_with_vanity(dbp, "mary@test.com", "mary-boyd")

    r = app_module.app.test_client().post(
        "/api/console/practitioner/reset-page-slug",
        json={"email": "MARY@test.com"},   # case-insensitive, matching the writers
        headers={"X-Console-Key": "sek"})
    assert r.status_code == 200, r.get_data(as_text=True)
    body = r.get_json()
    assert body["affiliate_slug"] == owner

    with sqlite3.connect(dbp) as cx:
        row = cx.execute("SELECT page_slug FROM affiliate_signups WHERE slug=?",
                         (owner,)).fetchone()
    assert row[0] == owner


def test_reset_is_safe_to_call_twice(monkeypatch, tmp_path):
    app_module = _load_app()
    dbp = str(tmp_path / "chat_log.db")
    monkeypatch.setattr(app_module, "LOG_DB", dbp)
    _bootstrap_db(app_module, dbp)
    _auth(app_module, monkeypatch)
    owner = _seed_practitioner_with_vanity(dbp, "mary@test.com", "mary-boyd")

    client = app_module.app.test_client()
    r1 = client.post("/api/console/practitioner/reset-page-slug",
                     json={"affiliate_slug": owner},
                     headers={"X-Console-Key": "sek"})
    r2 = client.post("/api/console/practitioner/reset-page-slug",
                     json={"affiliate_slug": owner},
                     headers={"X-Console-Key": "sek"})
    assert r1.status_code == 200, r1.get_data(as_text=True)
    assert r2.status_code == 200, r2.get_data(as_text=True)
    assert r1.get_json()["page_slug"] == r2.get_json()["page_slug"] == owner


def test_reset_missing_target_is_readable_400(monkeypatch, tmp_path):
    app_module = _load_app()
    dbp = str(tmp_path / "chat_log.db")
    monkeypatch.setattr(app_module, "LOG_DB", dbp)
    _bootstrap_db(app_module, dbp)
    _auth(app_module, monkeypatch)

    r = app_module.app.test_client().post(
        "/api/console/practitioner/reset-page-slug",
        json={}, headers={"X-Console-Key": "sek"})
    assert r.status_code == 400
    assert "required" in (r.get_json() or {}).get("error", "")


def test_reset_unknown_email_is_readable_404(monkeypatch, tmp_path):
    app_module = _load_app()
    dbp = str(tmp_path / "chat_log.db")
    monkeypatch.setattr(app_module, "LOG_DB", dbp)
    _bootstrap_db(app_module, dbp)
    _auth(app_module, monkeypatch)

    r = app_module.app.test_client().post(
        "/api/console/practitioner/reset-page-slug",
        json={"email": "nobody@test.com"}, headers={"X-Console-Key": "sek"})
    assert r.status_code == 404
