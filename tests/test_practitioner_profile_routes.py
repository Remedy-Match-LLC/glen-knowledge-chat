# tests/test_practitioner_profile_routes.py
import os
import pathlib
import re
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


def test_get_settings_includes_practice_name_from_the_live_row(client, monkeypatch):
    """The tenth field, same round-trip requirement: a saved practice_name
    must come back on GET or it vanishes from the editor on reload."""
    row = {"bio": "I heal", "photo_url": "https://x/p.jpg", "logo_url": "",
           "specialties": ["Acupuncture"], "city": "Hilo", "state": "HI",
           "accepting_new_patients": True, "tagline": "", "how_i_work": "",
           "practice_name": "Sunrise Wellness",
           "profile_self_authored_at": "2026-07-20T00:00:00Z", "show_contact": False}
    import db_supabase
    monkeypatch.setattr(db_supabase, "supabase_cursor", lambda: _FakeCtx(_FakeCur(row)))
    r = client.get("/api/practitioner/settings")
    prof = r.get_json()["profile"]
    assert prof["practice_name"] == "Sunrise Wellness"


def test_get_settings_overlays_the_draft_practice_name(client, monkeypatch, tmp_path):
    """Same C2 pending-draft-wins rule, for practice_name."""
    import sqlite3
    import db_supabase
    from dashboard import practitioner_drafts as _pd

    live = {"bio": "SCRAPED live bio", "photo_url": "", "logo_url": "", "specialties": ["Old"],
            "city": "Kona", "state": "HI", "accepting_new_patients": True,
            "tagline": "", "how_i_work": "", "practice_name": "Old Name",
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
            "tagline": "", "how_i_work": "",
            "practice_name": "MY PENDING PRACTICE NAME"})

    body = client.get("/api/practitioner/settings").get_json()
    assert body["profile"]["practice_name"] == "MY PENDING PRACTICE NAME"


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


# --- Fix wave: every profile failure mode is pre-validated ------------------


@pytest.mark.parametrize("profile,why", [
    ({"logo_url": "http://cdn.example.com/l.png"}, "plaintext http logo"),
    ({"logo_url": "javascript:alert(1)"}, "script-scheme logo"),
    ({"logo_url": "//cdn.example.com/l.png"}, "protocol-relative logo"),
    ({"photo_url": "javascript:alert(1)"}, "script-scheme photo"),
    ({"tagline": "x" * 121}, "tagline over the cap"),
    ({"how_i_work": "x" * 2001}, "how_i_work over the cap"),
    ({"logo_url": 123}, "non-string logo (was a 500, must be a 400)"),
    ({"tagline": 123}, "non-string tagline (was a 500, must be a 400)"),
    ({"practice_name": "x" * 121}, "practice_name over the cap"),
    ({"practice_name": 123}, "non-string practice_name (was a 500, must be a 400)"),
])
def test_post_bad_profile_field_does_not_partially_persist_branding(
        client, monkeypatch, profile, why):
    """Only the bio was pre-validated before any write, so the four newer
    failure modes 400'd AFTER branding/pricing had already been persisted.

    Reachable in the real UI: sf-logo-url is type="url", but the page saves via
    fetch(), so the browser never validates it -- a pasted http:// URL 400s
    with the practitioner's brand colour change already silently live.
    """
    from dashboard import practitioner_settings as _ps
    from dashboard import practitioner_profile as _pp

    calls = {"branding": 0, "pricing": 0, "save_draft": 0}
    monkeypatch.setattr(_ps, "set_branding",
                        lambda cx, pid, b, *, chat_enabled=None:
                        calls.__setitem__("branding", calls["branding"] + 1))
    monkeypatch.setattr(_ps, "set_pricing",
                        lambda cx, pid, p:
                        calls.__setitem__("pricing", calls["pricing"] + 1))
    monkeypatch.setattr(_pp, "save_draft",
                        lambda cx, pid, prof:
                        calls.__setitem__("save_draft", calls["save_draft"] + 1))

    full = {"bio": "fine", "tagline": "", "how_i_work": "",
            "photo_url": "", "logo_url": ""}
    full.update(profile)
    r = client.post("/api/practitioner/settings", json={
        "branding": {"practice_name": "New Name", "brand_color_1": "#ff0000"},
        "profile": full,
    })

    assert r.status_code == 400, f"{why}: expected 400, got {r.status_code}"
    assert calls["branding"] == 0, f"{why}: branding was written before the 400"
    assert calls["pricing"] == 0, f"{why}: pricing was written before the 400"
    assert calls["save_draft"] == 0


def test_post_bad_logo_url_does_not_write_show_contact(client, monkeypatch):
    """show_contact is the other write that lands before the profile block --
    it goes to Postgres, so a partial write there is not even rolled back with
    the sqlite ones."""
    import db_supabase
    supabase_calls = {"n": 0}

    def _counting_cursor():
        supabase_calls["n"] += 1
        return _FakeCtx(_FakeCur(None))

    monkeypatch.setattr(db_supabase, "supabase_cursor", _counting_cursor)
    r = client.post("/api/practitioner/settings", json={
        "show_contact": True,
        "profile": {"bio": "fine", "logo_url": "http://cdn.example.com/l.png"},
    })
    assert r.status_code == 400
    assert supabase_calls["n"] == 0


def test_post_good_profile_still_saves(client, monkeypatch):
    """The pre-check must not become a second, stricter gate that rejects
    values save_draft itself accepts."""
    from dashboard import practitioner_profile as _pp
    seen = {}

    def _fake_save_draft(cx, pid, profile):
        seen.update(profile)
        return dict(profile)

    monkeypatch.setattr(_pp, "save_draft", _fake_save_draft)
    monkeypatch.setattr(appmod, "_practitioner_profile_submit",
                        lambda cx, pid: ({"status": "submitted"}, 200))
    r = client.post("/api/practitioner/settings", json={"profile": {
        "bio": "I heal",
        "tagline": "Root-cause coaching",
        "how_i_work": "I start with a full intake.\n\n- sleep\n- digestion",
        "photo_url": "https://cdn.example.com/p.jpg",
        "logo_url": "https://cdn.example.com/l.png",
    }})
    assert r.status_code == 200, r.get_data(as_text=True)
    assert seen["logo_url"] == "https://cdn.example.com/l.png"
    assert "\n\n" in seen["how_i_work"]


# --- Fix wave: the settings page itself ------------------------------------


def _settings_html():
    return pathlib.Path(appmod.STATIC, "practitioner-settings.html").read_text(encoding="utf-8")


def test_settings_url_inputs_carry_a_maxlength_matching_the_server_cap():
    """MAX_URL is 500. Without maxlength a long paste fails as a server 400
    after the request, instead of at the keyboard."""
    from dashboard import practitioner_profile as _pp
    html = _settings_html()
    for ident in ("sf-logo-url", "sf-photo"):
        m = re.search(r'<input[^>]*id="%s"[^>]*>' % ident, html, re.S)
        assert m, f"no input for {ident}"
        assert 'maxlength="%d"' % _pp.MAX_URL in m.group(0), \
            f"{ident} has no maxlength matching MAX_URL: {m.group(0)}"


def test_settings_how_i_work_has_a_character_counter():
    """maxlength=2000 stops typing dead with no explanation, on the one field
    designed for long prose, right next to a Bio field that does show a count."""
    html = _settings_html()
    assert 'id="sf-how-i-work-count"' in html
    assert "sf-how-i-work-count').textContent" in html, \
        "the counter element exists but nothing ever updates it"


def test_settings_field_order_reads_short_to_long_and_keeps_the_urls_together():
    """Tagline, Bio, How I work, Photo URL, Logo URL. The 2000-char field must
    not sit above the 600-char one, and the two image URLs belong together."""
    html = _settings_html()
    order = ["sf-tagline", "sf-bio", "sf-how-i-work", "sf-photo", "sf-logo-url"]
    positions = [html.index('id="%s"' % i) for i in order]
    assert positions == sorted(positions), \
        "storefront fields are out of order: " + repr(
            sorted(zip(positions, order)))


# --- practitioner-profile.html: the single-purpose profile page -----------


def _profile_html():
    return pathlib.Path(appmod.STATIC, "practitioner-profile.html").read_text(encoding="utf-8")


def test_profile_page_route_returns_html():
    appmod.app.config["TESTING"] = True
    client = appmod.app.test_client()
    r = client.get("/practitioner/profile")
    assert r.status_code == 200
    assert "text/html" in r.content_type
    assert "<html" in r.get_data(as_text=True).lower()


def test_profile_page_has_an_input_for_every_field():
    html = _profile_html()
    for ident in ("pf-practice-name", "pf-tagline", "pf-bio", "pf-how-i-work",
                  "pf-photo", "pf-logo-url", "pf-city", "pf-state", "pf-accepting"):
        assert ('id="%s"' % ident) in html, f"profile page has no input for {ident}"


def test_profile_page_has_a_practice_name_field(monkeypatch):
    """practice_name is now a tenth field on the same review-gated path as
    bio/tagline/etc (dashboard/practitioner_profile.py: sanitize_practice_name,
    save_draft, _write_live_profile). This inverts the earlier
    test_profile_page_has_no_practice_name_field, which pinned the field's
    ABSENCE from back when the column existed but nothing wrote it through
    the draft/publish path. That gap is now closed."""
    html = _profile_html()
    assert 'id="pf-practice-name"' in html


def test_profile_page_practice_name_sits_above_tagline():
    """Glen: it's the most basic thing about a practice, so it goes first,
    above Tagline."""
    html = _profile_html()
    assert html.index('id="pf-practice-name"') < html.index('id="pf-tagline"')


def test_profile_page_practice_name_is_marked_optional():
    """Many coaches practise under their own name -- the label or placeholder
    must say the field is optional."""
    html = _profile_html()
    m = re.search(r'id="pf-practice-name"[^>]*>', html)
    assert m, "no pf-practice-name input tag found"
    start = html.index('id="pf-practice-name"')
    surrounding = html[max(0, start - 400):start + 200].lower()
    assert "optional" in surrounding


def test_profile_page_loader_prefills_practice_name():
    html = _profile_html()
    assert "pf-practice-name').value = pf.practice_name" in html


def test_profile_page_save_payload_sends_practice_name():
    html = _profile_html()
    assert "practice_name:" in html


def test_profile_page_posts_to_settings_with_the_practitioner_token():
    html = _profile_html()
    assert "/api/practitioner/settings" in html
    assert "X-Practitioner-Token" in html


def test_profile_page_has_no_em_dash_or_flagged_honesty_phrases():
    html = _profile_html()
    assert "—" not in html, "no em dashes allowed"
    lowered = html.lower()
    for phrase in ("to be honest", "one honest thing", "i'll be candid", "honestly"):
        assert phrase not in lowered, f"flagged phrase present: {phrase!r}"


def test_settings_scraped_load_handler_documents_why_it_skips_the_new_fields():
    """The handler assigns bio/photo/services/city/state and NOT tagline,
    how_i_work or logo_url. That is correct -- a scraped directory row has
    neither of the first two and logo_url is never scraped -- but it reads as
    an omission, so it has to say so."""
    html = _settings_html()
    start = html.index("sf-load-scraped').addEventListener('click'")
    body = html[start:html.index("});", start)]
    # Assignments only. The handler legitimately READS sf-how-i-work (to leave
    # its character count alone), so a bare substring check would false-fire.
    code = re.sub(r"//.*", "", body)
    for ident in ("sf-tagline", "sf-how-i-work", "sf-logo-url"):
        assert not re.search(r"getElementById\('%s'\)\s*\.\s*value\s*=" % ident, code), \
            f"scraped data must never be loaded into {ident} (self-authored only)"
    comment = "\n".join(re.findall(r"//.*", body))
    assert "scraped" in comment.lower() and "logo_url" in comment, \
        "the scraped-load handler needs a comment saying why it skips them"


def test_magic_link_may_return_to_the_profile_page():
    """An emailed sign-in link must be able to land a practitioner directly on
    the page we asked them to fill in. Without this the allowlist silently
    redirects them to the wholesale portal instead, and the link in the email
    goes somewhere the email did not promise."""
    assert appmod._practitioner_return_to("/practitioner/profile") == "/practitioner/profile"


def test_return_to_allowlist_still_refuses_anything_else():
    """The allowlist is a redirect guard: an attacker-supplied return_to must
    not be honoured just because we widened it by one entry."""
    for bad in ("https://evil.com", "//evil.com", "/admin", "/practitioner/../admin", ""):
        assert appmod._practitioner_return_to(bad) == "/practitioner/portal"
