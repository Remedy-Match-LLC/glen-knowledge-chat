# tests/test_practitioner_profile_routes.py
import os
import pathlib
import pytest
if not os.environ.get("PINECONE_API_KEY"):
    pytest.skip("needs doppler env for import app", allow_module_level=True)
import app as appmod


class _FakeCur:
    def __init__(self, row): self._row = row; self.calls = []
    def execute(self, sql, params=()): self.calls.append((" ".join(sql.split()), list(params)))
    def fetchone(self): return self._row
    def close(self): pass


class _FakeCtx:
    def __init__(self, cur): self.cur = cur
    def __enter__(self): return self.cur
    def __exit__(self, *a): return False


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(appmod, "_practitioner_session_pid", lambda: "pid-123")
    from dashboard import practitioner_settings as _ps
    monkeypatch.setattr(_ps, "init_settings_table", lambda cx: None)
    monkeypatch.setattr(_ps, "get_settings",
                        lambda cx, pid: {"branding": {}, "pricing": {}, "chat_enabled": False})
    # Stub the sqlite writes too. The POST handler calls set_branding/set_pricing,
    # which open LOG_DB — a real write that these profile-focused tests don't
    # exercise and that fails order-dependently in the full suite when an earlier
    # test has left LOG_DB pointing at a torn-down tmp path. Tests that assert on
    # these (e.g. the partial-write test) override them with their own counters.
    monkeypatch.setattr(_ps, "set_branding", lambda *a, **k: None)
    monkeypatch.setattr(_ps, "set_pricing", lambda *a, **k: None)
    appmod.app.config["TESTING"] = True
    return appmod.app.test_client()


def test_get_settings_includes_profile(client, monkeypatch):
    row = {"bio": "I heal", "photo_url": "https://x/p.jpg", "specialties": ["Acupuncture"],
           "city": "Hilo", "state": "HI", "accepting_new_patients": True,
           "profile_self_authored_at": "2026-07-20T00:00:00Z", "show_contact": False}
    import db_supabase
    monkeypatch.setattr(db_supabase, "supabase_cursor", lambda: _FakeCtx(_FakeCur(row)))
    r = client.get("/api/practitioner/settings")
    assert r.status_code == 200
    prof = r.get_json()["profile"]
    assert prof["bio"] == "I heal"
    assert prof["services"] == ["Acupuncture"]
    assert prof["self_authored"] is True


def test_get_settings_profile_omitted_when_supabase_down(client, monkeypatch):
    """Fail-soft: a Supabase error during the profile read must not 500 the
    settings page — the profile key is simply omitted, other keys remain."""
    import db_supabase
    def _boom():
        raise RuntimeError("supabase down")
    monkeypatch.setattr(db_supabase, "supabase_cursor", _boom)
    r = client.get("/api/practitioner/settings")
    assert r.status_code == 200
    body = r.get_json()
    assert "profile" not in body
    assert body["ok"] is True


def test_post_saves_profile_when_present(client, monkeypatch):
    saved = {}
    from dashboard import practitioner_profile as _pp
    def _fake_save(cx, pid, profile):
        saved["pid"] = pid; saved["profile"] = profile
        return {"bio": "I heal", "photo_url": "", "services": [], "city": "Hilo",
                "state": "HI", "accepting_clients": True}
    monkeypatch.setattr(_pp, "save_draft", _fake_save)
    r = client.post("/api/practitioner/settings", json={
        "profile": {"bio": "I heal", "city": "Hilo", "state": "HI"}})
    assert r.status_code == 200
    assert saved["pid"] == "pid-123"
    assert r.get_json()["profile"]["bio"] == "I heal"


def test_post_without_profile_does_not_touch_it(client, monkeypatch):
    from dashboard import practitioner_profile as _pp
    called = {"n": 0}
    monkeypatch.setattr(_pp, "save_draft", lambda *a, **k: called.__setitem__("n", called["n"] + 1))
    r = client.post("/api/practitioner/settings", json={"branding": {}})
    assert r.status_code == 200
    assert called["n"] == 0
    assert "profile" not in r.get_json()


def test_post_profile_bad_bio_returns_400(client, monkeypatch):
    from dashboard import practitioner_profile as _pp
    def _boom(cx, pid, profile): raise ValueError("bio exceeds 600 characters")
    monkeypatch.setattr(_pp, "save_draft", _boom)
    r = client.post("/api/practitioner/settings", json={"profile": {"bio": "x" * 601}})
    assert r.status_code == 400


def test_post_bad_bio_does_not_partially_persist_branding(client, monkeypatch):
    """A bundled request (branding + a too-long bio) must 400 WITHOUT writing
    the branding change first. The editor UI sends everything in one payload,
    so a bio-length failure must not silently persist branding/pricing."""
    from dashboard import practitioner_settings as _ps
    from dashboard import practitioner_profile as _pp

    branding_calls = {"n": 0}
    pricing_calls = {"n": 0}
    save_draft_calls = {"n": 0}

    def _fake_set_branding(cx, pid, branding, *, chat_enabled=None):
        branding_calls["n"] += 1

    def _fake_set_pricing(cx, pid, pricing):
        pricing_calls["n"] += 1

    def _fake_save_draft(cx, pid, profile):
        # Mirrors the real save_draft: validates (and can raise ValueError
        # on a too-long bio) before "writing" — just like production.
        save_draft_calls["n"] += 1
        bio = _pp.sanitize_bio(profile.get("bio", ""))
        return {"bio": bio}

    monkeypatch.setattr(_ps, "set_branding", _fake_set_branding)
    monkeypatch.setattr(_ps, "set_pricing", _fake_set_pricing)
    monkeypatch.setattr(_pp, "save_draft", _fake_save_draft)

    r = client.post("/api/practitioner/settings", json={
        "branding": {"practice_name": "New Name"},
        "profile": {"bio": "x" * 601},
    })

    assert r.status_code == 400
    assert branding_calls["n"] == 0
    assert pricing_calls["n"] == 0
    assert save_draft_calls["n"] == 0


# --- C2: the editor must show the practitioner her own pending edit ---------

def test_get_settings_overlays_the_draft_over_the_live_row(client, monkeypatch, tmp_path):
    """C2: the GET used to read only the Postgres row, so after saving, a
    reload pre-filled the form with the OLD live text and her pending edit was
    gone from the textarea. It reads as "the save didn't work" and she retypes
    it. The draft is what she last typed -- it wins."""
    import sqlite3
    import db_supabase
    from dashboard import practitioner_drafts as _pd

    live = {"bio": "SCRAPED live bio", "photo_url": "", "specialties": ["Old"],
            "city": "Kona", "state": "HI", "accepting_new_patients": True,
            "profile_self_authored_at": "2026-07-20T00:00:00Z", "show_contact": False}
    monkeypatch.setattr(db_supabase, "supabase_cursor", lambda: _FakeCtx(_FakeCur(live)))

    dbpath = str(tmp_path / "chat_log.db")
    monkeypatch.setattr(appmod, "LOG_DB", dbpath)
    with appmod.db.connect(dbpath) as cx:
        cx.row_factory = sqlite3.Row
        _pd.init_tables(cx)
        _pd.upsert_draft(cx, "pid-123", {
            "bio": "MY PENDING EDIT", "photo_url": "https://x/new.jpg",
            "services": ["New"], "city": "Hilo", "state": "HI",
            "accepting_clients": False})

    body = client.get("/api/practitioner/settings").get_json()
    assert body["profile"]["bio"] == "MY PENDING EDIT"
    assert body["profile"]["services"] == ["New"]
    assert body["profile"]["city"] == "Hilo"
    assert body["profile"]["accepting_clients"] is False
    # A draft is self-authored by definition, so the editor pre-fills it.
    assert body["profile"]["self_authored"] is True
    assert body["profile_status"] == "draft"


def test_get_settings_returns_the_rejection_note(client, monkeypatch, tmp_path):
    """C2: a rejection note that never reaches the practitioner just produces
    an identical resubmit."""
    import sqlite3
    import db_supabase
    from dashboard import practitioner_drafts as _pd

    monkeypatch.setattr(db_supabase, "supabase_cursor",
                        lambda: _FakeCtx(_FakeCur(None)))
    dbpath = str(tmp_path / "chat_log.db")
    monkeypatch.setattr(appmod, "LOG_DB", dbpath)
    with appmod.db.connect(dbpath) as cx:
        cx.row_factory = sqlite3.Row
        _pd.init_tables(cx)
        _pd.upsert_draft(cx, "pid-123", {"bio": "x"})
        _pd.submit(cx, "pid-123")
        _pd.reject(cx, "pid-123", "please remove the health claim")

    body = client.get("/api/practitioner/settings").get_json()
    assert body["profile_status"] == "draft"
    assert body["review_note"] == "please remove the health claim"


def test_get_settings_falls_back_to_the_live_row_with_no_draft(client, monkeypatch, tmp_path):
    """No draft: unchanged behavior, the live self-authored row is returned."""
    import db_supabase

    live = {"bio": "I heal", "photo_url": "", "specialties": ["Acupuncture"],
            "city": "Hilo", "state": "HI", "accepting_new_patients": True,
            "profile_self_authored_at": "2026-07-20T00:00:00Z", "show_contact": False}
    monkeypatch.setattr(db_supabase, "supabase_cursor", lambda: _FakeCtx(_FakeCur(live)))
    monkeypatch.setattr(appmod, "LOG_DB", str(tmp_path / "chat_log.db"))

    body = client.get("/api/practitioner/settings").get_json()
    assert body["profile"]["bio"] == "I heal"
    assert "profile_status" not in body


def test_get_settings_includes_the_new_fields_from_the_live_row(client, monkeypatch):
    """The editor's loader pre-fills the form from `data.profile` on load. If
    the GET route never reads tagline/how_i_work/logo_url off the live row,
    the client-side wiring is pointless: a saved value would still vanish on
    reload because the server never sent it back."""
    row = {"bio": "I heal", "photo_url": "https://x/p.jpg", "logo_url": "https://x/l.png",
           "specialties": ["Acupuncture"], "city": "Hilo", "state": "HI",
           "accepting_new_patients": True, "tagline": "Root-cause coaching",
           "how_i_work": "We start slowly.",
           "profile_self_authored_at": "2026-07-20T00:00:00Z", "show_contact": False}
    import db_supabase
    monkeypatch.setattr(db_supabase, "supabase_cursor", lambda: _FakeCtx(_FakeCur(row)))
    r = client.get("/api/practitioner/settings")
    prof = r.get_json()["profile"]
    assert prof["tagline"] == "Root-cause coaching"
    assert prof["how_i_work"] == "We start slowly."
    assert prof["logo_url"] == "https://x/l.png"


def test_get_settings_overlays_the_draft_new_fields(client, monkeypatch, tmp_path):
    """Same C2 pending-draft-wins rule, now covering the three new fields."""
    import sqlite3
    import db_supabase
    from dashboard import practitioner_drafts as _pd

    live = {"bio": "SCRAPED live bio", "photo_url": "", "logo_url": "", "specialties": ["Old"],
            "city": "Kona", "state": "HI", "accepting_new_patients": True,
            "tagline": "", "how_i_work": "",
            "profile_self_authored_at": "2026-07-20T00:00:00Z", "show_contact": False}
    monkeypatch.setattr(db_supabase, "supabase_cursor", lambda: _FakeCtx(_FakeCur(live)))

    dbpath = str(tmp_path / "chat_log.db")
    monkeypatch.setattr(appmod, "LOG_DB", dbpath)
    with appmod.db.connect(dbpath) as cx:
        cx.row_factory = sqlite3.Row
        _pd.init_tables(cx)
        _pd.upsert_draft(cx, "pid-123", {
            "bio": "MY PENDING EDIT", "photo_url": "https://x/new.jpg",
            "logo_url": "https://x/new-logo.png",
            "services": ["New"], "city": "Hilo", "state": "HI",
            "accepting_clients": False,
            "tagline": "MY PENDING TAGLINE", "how_i_work": "MY PENDING HOW"})

    body = client.get("/api/practitioner/settings").get_json()
    assert body["profile"]["tagline"] == "MY PENDING TAGLINE"
    assert body["profile"]["how_i_work"] == "MY PENDING HOW"
    assert body["profile"]["logo_url"] == "https://x/new-logo.png"


def test_settings_page_offers_the_new_profile_inputs():
    """A field the practitioner cannot type is a field that does not exist.
    Section 2a shipped a submit route with no caller; this is the same check
    one layer up."""
    html = pathlib.Path(appmod.STATIC, "practitioner-settings.html").read_text(encoding="utf-8")
    for ident in ("sf-tagline", "sf-how-i-work", "sf-logo-url"):
        assert ident in html, f"settings page has no input for {ident}"


def test_settings_page_sends_the_new_fields():
    html = pathlib.Path(appmod.STATIC, "practitioner-settings.html").read_text(encoding="utf-8")
    for key in ("tagline", "how_i_work", "logo_url"):
        assert key in html, f"settings page never sends {key} in its payload"
