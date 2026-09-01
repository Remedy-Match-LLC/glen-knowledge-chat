"""Route-level tests for affiliate journey wiring.

Mirrors test_begin_routes.py conventions:
  - _load_app() for import
  - monkeypatch LOG_DB to a tmp path
  - init_journey_tables + _init_referral_tables to bootstrap the DB
  - stubs for all external/network calls
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
    """Create all required tables in the test DB."""
    import begin_funnel
    with sqlite3.connect(db_path) as cx:
        begin_funnel.init_journey_tables(cx)
    # Use the app's own table-init so affiliate_signups etc. are present
    orig = app_module.LOG_DB
    app_module.LOG_DB = db_path
    app_module._init_referral_tables()
    app_module.LOG_DB = orig


def _get_journey(db_path, session_id):
    import begin_funnel
    with sqlite3.connect(db_path) as cx:
        begin_funnel.init_journey_tables(cx)
        return begin_funnel.get_state(cx, session_id=session_id)


def _count_become_affiliate_events(db_path, session_id):
    with sqlite3.connect(db_path) as cx:
        row = cx.execute(
            "SELECT COUNT(*) FROM journey_events "
            "WHERE session_id=? AND trigger='paid_fork' AND detail='become_affiliate'",
            (session_id,),
        ).fetchone()
        return row[0] if row else 0


# ---------------------------------------------------------------------------
# Test 1: form POST with preset amg_session -> 302 + journey row at choose_path
# ---------------------------------------------------------------------------

def test_apply_form_creates_journey_row_choose_path(monkeypatch, tmp_path):
    app_module = _load_app()
    db = str(tmp_path / "chat_log.db")
    monkeypatch.setattr(app_module, "LOG_DB", db)
    _bootstrap_db(app_module, db)

    # Stub out all external calls
    monkeypatch.setattr(app_module, "_rebrandly_create", lambda *a, **k: "https://short.link/test")
    monkeypatch.setattr(app_module, "ghl_onboard_contact", lambda *a, **k: {"contact_id": "x"})
    monkeypatch.setattr(app_module, "_capture_concierge_referral", lambda *a, **k: None)

    client = app_module.app.test_client()
    client.set_cookie("amg_session", "sess-ada-001")

    r = client.post("/affiliate/apply-form", data={
        "name": "Ada Lovelace",
        "email": "ada.form.unique@test.com",
        "organization": "TestOrg",
        "tos": "true",
    })

    assert r.status_code == 302
    location = r.headers.get("Location", "")
    assert "/affiliate/portal?token=" in location

    st = _get_journey(db, "sess-ada-001")
    assert st["current_rung"] == "choose_path", f"got {st['current_rung']}"
    assert st["path"] == "pay_forward"
    assert st["first_name"] == "Ada"
    assert st["last_name"] == "Lovelace"


# ---------------------------------------------------------------------------
# Test 2: no amg_session cookie -> response sets amg_session + journey row exists
# ---------------------------------------------------------------------------

def test_apply_form_mints_session_when_no_cookie(monkeypatch, tmp_path):
    app_module = _load_app()
    db = str(tmp_path / "chat_log.db")
    monkeypatch.setattr(app_module, "LOG_DB", db)
    _bootstrap_db(app_module, db)

    monkeypatch.setattr(app_module, "_rebrandly_create", lambda *a, **k: "")
    monkeypatch.setattr(app_module, "ghl_onboard_contact", lambda *a, **k: {"contact_id": "x"})
    monkeypatch.setattr(app_module, "_capture_concierge_referral", lambda *a, **k: None)

    client = app_module.app.test_client()
    # No amg_session cookie set

    r = client.post("/affiliate/apply-form", data={
        "name": "Bob Builder",
        "email": "bob.builder.unique@test.com",
        "organization": "",
        "tos": "true",
    })

    assert r.status_code == 302

    # amg_session should be set in response cookies
    set_cookie_header = r.headers.get("Set-Cookie", "")
    assert "amg_session=" in set_cookie_header, (
        f"Expected amg_session cookie; got Set-Cookie: {set_cookie_header!r}"
    )

    # Extract the minted session id from the cookie
    import re
    m = re.search(r"amg_session=([^;]+)", set_cookie_header)
    assert m, "Could not parse amg_session value from Set-Cookie"
    minted = m.group(1)

    # Journey row must exist for that session
    st = _get_journey(db, minted)
    assert st["current_rung"] == "choose_path", f"got {st['current_rung']}"
    assert st["path"] == "pay_forward"


# ---------------------------------------------------------------------------
# Test 3: double-POST (same session) -> exactly ONE become_affiliate event
# ---------------------------------------------------------------------------

def test_apply_form_idempotent_no_double_stamp(monkeypatch, tmp_path):
    app_module = _load_app()
    db = str(tmp_path / "chat_log.db")
    monkeypatch.setattr(app_module, "LOG_DB", db)
    _bootstrap_db(app_module, db)

    monkeypatch.setattr(app_module, "_rebrandly_create", lambda *a, **k: "")
    monkeypatch.setattr(app_module, "ghl_onboard_contact", lambda *a, **k: {"contact_id": "x"})
    monkeypatch.setattr(app_module, "_capture_concierge_referral", lambda *a, **k: None)

    client = app_module.app.test_client()
    client.set_cookie("amg_session", "sess-idem-007")

    # First POST: new signup
    r1 = client.post("/affiliate/apply-form", data={
        "name": "Carol Idempotent",
        "email": "carol.idem.unique@test.com",
        "organization": "",
        "tos": "true",
    })
    assert r1.status_code == 302

    # Second POST: same email -> already-registered early-return path
    r2 = client.post("/affiliate/apply-form", data={
        "name": "Carol Idempotent",
        "email": "carol.idem.unique@test.com",
        "organization": "",
        "tos": "true",
    })
    assert r2.status_code == 302

    count = _count_become_affiliate_events(db, "sess-idem-007")
    assert count == 1, f"Expected exactly 1 become_affiliate event, got {count}"


# ---------------------------------------------------------------------------
# Test 4: journey failure must NOT break signup (safety test)
# ---------------------------------------------------------------------------

def test_apply_form_survives_journey_failure(monkeypatch, tmp_path):
    app_module = _load_app()
    db = str(tmp_path / "chat_log.db")
    monkeypatch.setattr(app_module, "LOG_DB", db)
    _bootstrap_db(app_module, db)

    monkeypatch.setattr(app_module, "_rebrandly_create", lambda *a, **k: "")
    monkeypatch.setattr(app_module, "ghl_onboard_contact", lambda *a, **k: {"contact_id": "x"})
    monkeypatch.setattr(app_module, "_capture_concierge_referral", lambda *a, **k: None)

    # Make record_unlock raise to simulate engine failure
    import begin_funnel

    def _raise(*a, **k):
        raise RuntimeError("simulated engine failure")

    monkeypatch.setattr(begin_funnel, "record_unlock", _raise)

    client = app_module.app.test_client()
    client.set_cookie("amg_session", "sess-fail-999")

    r = client.post("/affiliate/apply-form", data={
        "name": "Dave Survivor",
        "email": "dave.survivor.unique@test.com",
        "organization": "",
        "tos": "true",
    })

    # Signup must still succeed — the 302 redirect to portal must be returned
    assert r.status_code == 302, f"Expected 302 but got {r.status_code}"
    location = r.headers.get("Location", "")
    assert "/affiliate/portal?token=" in location, (
        f"Expected portal redirect; got Location: {location!r}"
    )


# ---------------------------------------------------------------------------
# Test 5: JSON apply parity -> 201 + choose_path journey row
# ---------------------------------------------------------------------------

def test_apply_json_creates_journey_row_choose_path(monkeypatch, tmp_path):
    app_module = _load_app()
    db = str(tmp_path / "chat_log.db")
    monkeypatch.setattr(app_module, "LOG_DB", db)
    _bootstrap_db(app_module, db)

    monkeypatch.setattr(app_module, "_rebrandly_create", lambda *a, **k: "")
    monkeypatch.setattr(app_module, "ghl_onboard_contact", lambda *a, **k: {"contact_id": "x"})
    monkeypatch.setattr(app_module, "_capture_concierge_referral", lambda *a, **k: None)

    client = app_module.app.test_client()
    client.set_cookie("amg_session", "sess-json-042")

    r = client.post("/affiliate/apply", json={
        "name": "Eve JSON",
        "email": "eve.json.unique@test.com",
        "organization": "JSONCorp",
        "tos": True,
    })

    assert r.status_code == 200
    body = r.get_json()
    assert body.get("ok") is True
    assert "/affiliate/portal?token=" in body.get("portal_url", "")

    st = _get_journey(db, "sess-json-042")
    assert st["current_rung"] == "choose_path", f"got {st['current_rung']}"
    assert st["path"] == "pay_forward"
    assert st["first_name"] == "Eve"
    assert st["last_name"] == "JSON"


# ---------------------------------------------------------------------------
# Slug minting asks the WHOLE namespace, not just affiliate_signups.slug
#
# Both signup routes write the minted value to page_slug as well as slug, and
# the unique index ux_affiliate_page_slug is on page_slug. A pre-check that
# asked only `WHERE slug=?` could not see a vanity name another practitioner
# had already claimed: it passed, and the INSERT then died. The base is
# deterministic from org-or-name, so every retry reproduced the identical
# collision and that person could never sign up.
# ---------------------------------------------------------------------------

def _claim_page_slug(db_path, vanity):
    """Seed a practitioner who has renamed her public URL to `vanity`.

    Uses the production writer + the production setter, so the row is in
    exactly the state a real rename leaves: `vanity` occupied in page_slug and
    absent from slug -- the split the broken pre-check could not see.
    """
    from dashboard import affiliate_dashboard as _ad
    from dashboard import practitioner_slugs as _ps
    cx = sqlite3.connect(db_path)
    row = _ad.ensure_affiliate(cx, "glen@test.com", name="Remedy Match")
    assert row, "could not seed the practitioner who holds the vanity URL"
    _ps.set_page_slug(cx, row["slug"], vanity, reserved=frozenset())
    cx.close()
    return row["slug"]


def _stub_externals(app_module, monkeypatch, minted):
    monkeypatch.setattr(app_module, "_rebrandly_create",
                        lambda **k: (minted.append(k.get("slashtag")),
                                     "https://truly.vip/stub")[1])
    monkeypatch.setattr(app_module, "ghl_onboard_contact",
                        lambda *a, **k: {"contact_id": "x"})
    monkeypatch.setattr(app_module, "_capture_concierge_referral",
                        lambda *a, **k: None)


def test_apply_form_suffixes_a_slug_another_practitioner_holds_as_her_url(
        monkeypatch, tmp_path):
    """Glen's public URL is /mary-boyd. Mary Boyd then signs up.

    Before the fix this redirected a public visitor to an error page reading
    "UNIQUE constraint failed" -- and _rebrandly_create had already minted
    truly.vip/mary-boyd pointing at a row that never came to exist, once per
    retry.
    """
    app_module = _load_app()
    dbp = str(tmp_path / "chat_log.db")
    monkeypatch.setattr(app_module, "LOG_DB", dbp)
    _bootstrap_db(app_module, dbp)
    owner = _claim_page_slug(dbp, "mary-boyd")

    minted = []
    _stub_externals(app_module, monkeypatch, minted)

    r = app_module.app.test_client().post("/affiliate/apply-form", data={
        "name": "Mary Boyd", "email": "mary.boyd.form@test.com", "tos": "true"})

    assert r.status_code == 302
    assert "error=" not in r.headers.get("Location", ""), r.headers.get("Location")

    with sqlite3.connect(dbp) as cx:
        row = cx.execute("SELECT slug, page_slug FROM affiliate_signups"
                         " WHERE email=?", ("mary.boyd.form@test.com",)).fetchone()
        held = cx.execute("SELECT page_slug FROM affiliate_signups WHERE slug=?",
                          (owner,)).fetchone()
    assert row is not None, "the signup row was never written"
    assert row[0] != "mary-boyd" and row[0].startswith("mary-boyd-"), row
    assert row[1] == row[0], "her page_slug must be her own name, not NULL"
    # No orphaned shortlink: the ONE slashtag minted is the slug that was
    # actually stored.
    assert minted == [row[0]], minted
    # And Glen's published URL is untouched.
    assert held[0] == "mary-boyd"


def test_apply_json_suffixes_a_slug_another_practitioner_holds_as_her_url(
        monkeypatch, tmp_path):
    """Same collision on the JSON route, which answered a 409 carrying a raw
    database error that no retry could clear."""
    app_module = _load_app()
    dbp = str(tmp_path / "chat_log.db")
    monkeypatch.setattr(app_module, "LOG_DB", dbp)
    _bootstrap_db(app_module, dbp)
    _claim_page_slug(dbp, "mary-boyd")
    _stub_externals(app_module, monkeypatch, [])

    r = app_module.app.test_client().post("/affiliate/apply", json={
        "name": "Mary Boyd", "email": "mary.boyd.json@test.com", "tos": True})

    assert r.status_code == 200, r.get_data(as_text=True)
    body = r.get_json()
    assert body.get("ok") is True, body
    assert body["slug"] != "mary-boyd"
    assert body["slug"].startswith("mary-boyd-"), body["slug"]


# ---------------------------------------------------------------------------
# A shortlink must never be minted before the row that would own it. If the
# INSERT fails, _rebrandly_create must not have been called at all -- calling
# it first would mint a truly.vip/<slug> pointing at a row that never came to
# exist, and a retry would mint another one every time.
# ---------------------------------------------------------------------------

class _BoomConn(sqlite3.Connection):
    """A sqlite3.Connection that raises on the INSERT this test targets and
    behaves normally otherwise -- simulating any failure between "slug
    decided" and "row committed" without disturbing the pre-insert namespace
    check, which runs its own unrelated queries on its own connection."""

    def execute(self, sql, *a, **k):
        if "INSERT INTO affiliate_signups" in sql:
            raise RuntimeError("simulated insert-path failure")
        return super().execute(sql, *a, **k)


def test_apply_form_failed_insert_mints_no_shortlink(monkeypatch, tmp_path):
    app_module = _load_app()
    dbp = str(tmp_path / "chat_log.db")
    monkeypatch.setattr(app_module, "LOG_DB", dbp)
    _bootstrap_db(app_module, dbp)

    minted = []
    _stub_externals(app_module, monkeypatch, minted)

    def _boom_connect(path, timeout=5.0):
        return sqlite3.connect(path, timeout=timeout, factory=_BoomConn)
    monkeypatch.setattr(app_module.db, "connect", _boom_connect)

    r = app_module.app.test_client().post("/affiliate/apply-form", data={
        "name": "Frank Failure", "email": "frank.failure.unique@test.com",
        "tos": "true"})

    assert r.status_code == 302
    assert "error=" in r.headers.get("Location", ""), r.headers.get("Location")
    assert minted == [], f"shortlink minted despite failed insert: {minted}"

    with sqlite3.connect(dbp) as cx:
        row = cx.execute(
            "SELECT 1 FROM affiliate_signups WHERE email=?",
            ("frank.failure.unique@test.com",)).fetchone()
    assert row is None, "no row should have been written"
