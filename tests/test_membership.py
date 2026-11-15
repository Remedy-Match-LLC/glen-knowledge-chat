import pytest
import sqlite3
from datetime import datetime, timedelta, timezone, date

from tests.test_begin_routes import _load_app

def _signed(name, email):
    # The sign-in email cookies are signed since 2026-10-15; a plain value is refused.
    import app
    return app._sign_email_cookie(name, email)



def test_init_membership_tables_idempotent_creates_three_tables(tmp_path):
    app_module = _load_app()
    db = str(tmp_path / "chat_log.db")
    cx = sqlite3.connect(db)
    # call twice; must not raise; must create all three tables
    app_module.init_membership_tables(cx)
    app_module.init_membership_tables(cx)
    names = {r[0] for r in cx.execute(
        "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    expected = {"memberships", "escalation_queue", "studio_credit_intents"}
    assert expected.issubset(names)


def test_init_membership_tables_creates_expected_columns(tmp_path):
    app_module = _load_app()
    db = str(tmp_path / "chat_log.db")
    cx = sqlite3.connect(db)
    app_module.init_membership_tables(cx)
    mem_cols = {r[1] for r in cx.execute("PRAGMA table_info(memberships)").fetchall()}
    assert {"id","email","granted_at","expires_at","granted_by","source",
            "truly_vip_ref","notes","last_reminder_at"}.issubset(mem_cols)
    esc_cols = {r[1] for r in cx.execute("PRAGMA table_info(escalation_queue)").fetchall()}
    assert {"id","created_at","email","query_text","ai_response","ai_confidence",
            "flag_reason","status","glen_reply_url","glen_reply_text","replied_at"}.issubset(esc_cols)
    sci_cols = {r[1] for r in cx.execute("PRAGMA table_info(studio_credit_intents)").fetchall()}
    assert {"id","created_at","email","studio_ref","notes"}.issubset(sci_cols)


@pytest.fixture
def app_module_with_db(monkeypatch, tmp_path):
    app_module = _load_app()
    db = str(tmp_path / "chat_log.db")
    monkeypatch.setattr(app_module, "LOG_DB", db)
    cx = sqlite3.connect(db)
    # auth_tokens exists from existing init; ensure it does
    try:
        import begin_funnel
        begin_funnel.init_journey_tables(cx)
    except Exception: pass
    # Ensure auth_tokens table exists in the test DB (mirror app.py:145 schema)
    cx.executescript("""
        CREATE TABLE IF NOT EXISTS auth_tokens (
          token_hash TEXT PRIMARY KEY,
          email      TEXT,
          purpose    TEXT NOT NULL,
          extra      TEXT,
          created_at TEXT NOT NULL,
          expires_at TEXT NOT NULL,
          consumed_at TEXT
        );
    """)
    app_module.init_membership_tables(cx)
    cx.close()
    return app_module, db


def test_mint_returns_plaintext_token_and_stores_hash(app_module_with_db):
    app_module, db = app_module_with_db
    plain = app_module._mint_membership_magic_link("user@example.com")
    assert isinstance(plain, str) and len(plain) > 20
    cx = sqlite3.connect(db)
    th = app_module._hash_token(plain)
    row = cx.execute(
        "SELECT email, purpose, consumed_at FROM auth_tokens WHERE token_hash=?",
        (th,)
    ).fetchone()
    assert row is not None
    assert row[0] == "user@example.com"
    assert row[1] == "membership_magic_link"
    assert row[2] is None


def test_validate_happy_path_returns_email(app_module_with_db):
    app_module, db = app_module_with_db
    plain = app_module._mint_membership_magic_link("happy@example.com")
    email = app_module._validate_membership_magic_link(plain)
    assert email == "happy@example.com"


def test_validate_returns_none_when_expired(app_module_with_db):
    app_module, db = app_module_with_db
    plain = app_module._mint_membership_magic_link("expired@example.com", ttl_min=-1)
    email = app_module._validate_membership_magic_link(plain)
    assert email is None


def test_validate_returns_none_when_consumed(app_module_with_db):
    app_module, db = app_module_with_db
    plain = app_module._mint_membership_magic_link("once@example.com")
    th = app_module._hash_token(plain)
    cx = sqlite3.connect(db)
    cx.execute("UPDATE auth_tokens SET consumed_at=? WHERE token_hash=?",
               (datetime.utcnow().isoformat()+"Z", th))
    cx.commit(); cx.close()
    email = app_module._validate_membership_magic_link(plain)
    assert email is None


def test_validate_returns_none_when_purpose_mismatch(app_module_with_db):
    app_module, db = app_module_with_db
    # Manually insert a token with a different purpose; the validator should reject.
    import secrets
    plain = secrets.token_urlsafe(32)
    th = app_module._hash_token(plain)
    now_iso = datetime.utcnow().isoformat()+"Z"
    exp_iso = (datetime.utcnow()+timedelta(minutes=15)).isoformat()+"Z"
    cx = sqlite3.connect(db)
    cx.execute(
        "INSERT INTO auth_tokens (token_hash, email, purpose, extra, created_at, expires_at) "
        "VALUES (?,?,?,?,?,?)",
        (th, "wrong@example.com", "other_purpose", "{}", now_iso, exp_iso))
    cx.commit(); cx.close()
    assert app_module._validate_membership_magic_link(plain) is None


# ── Slice 2: admin grant + admin escalations listing ─────────────────────────

import smtplib
import json as _json
from tests.test_inquiry import FakeSMTP


@pytest.fixture
def fake_smtp(monkeypatch):
    monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("SMTP_USER", "u@e.com")
    monkeypatch.setenv("SMTP_PASS", "p")
    monkeypatch.setenv("SMTP_FROM", "hello@remedymatch.com")
    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    FakeSMTP.instances = []
    yield FakeSMTP


@pytest.fixture
def app_client_mem(monkeypatch, tmp_path, fake_smtp):
    """Flask test client with mem-init done and CONSOLE_SECRET set."""
    app_module = _load_app()
    db = str(tmp_path / "chat_log.db")
    monkeypatch.setattr(app_module, "LOG_DB", db)
    # Patch both app.CONSOLE_SECRET and dashboard.CONSOLE_SECRET (the decorator
    # reads the dashboard module's copy, not app.py's).
    import dashboard as _dashboard
    monkeypatch.setattr(app_module, "CONSOLE_SECRET", "test-console-secret-xyz")
    monkeypatch.setattr(_dashboard, "CONSOLE_SECRET", "test-console-secret-xyz")
    cx = sqlite3.connect(db)
    # auth_tokens (mirror app.py:145 schema)
    cx.executescript("""
        CREATE TABLE IF NOT EXISTS auth_tokens (
          token_hash TEXT PRIMARY KEY,
          email      TEXT,
          purpose    TEXT NOT NULL,
          extra      TEXT,
          created_at TEXT NOT NULL,
          expires_at TEXT NOT NULL,
          consumed_at TEXT
        );
    """)
    # journey_events
    try:
        import begin_funnel
        begin_funnel.init_journey_tables(cx)
    except Exception: pass
    app_module.init_membership_tables(cx)
    cx.close()
    return app_module.app.test_client(), app_module, db


def _ckey():
    return {"X-Console-Key": "test-console-secret-xyz"}


def test_grant_requires_console_key(app_client_mem):
    client, _, _ = app_client_mem
    r = client.post("/admin/membership/grant",
                    json={"email":"x@e.com","source":"video"})
    assert r.status_code in (401, 403)


def test_grant_happy_path_inserts_row_sends_email_logs_event(app_client_mem, fake_smtp):
    client, app_module, db = app_client_mem
    r = client.post("/admin/membership/grant",
                    json={"email":"jane@example.com","source":"video",
                          "truly_vip_ref":"https://truly.vip/Results/abc"},
                    headers=_ckey())
    assert r.status_code == 200, r.get_json()
    body = r.get_json()
    assert "membership_id" in body
    assert "magic_link_url" in body
    assert body["magic_link_url"].endswith(  # ends with the /coaching/auth/<token>
        body["magic_link_url"].split("/coaching/auth/")[-1])
    assert "/coaching/auth/" in body["magic_link_url"]
    assert "expires_at" in body
    # memberships row
    cx = sqlite3.connect(db)
    mrow = cx.execute(
        "SELECT email, source, truly_vip_ref, granted_by FROM memberships WHERE id=?",
        (body["membership_id"],)
    ).fetchone()
    assert mrow == ("jane@example.com", "video", "https://truly.vip/Results/abc", "glen") or \
           (mrow[0] == "jane@example.com" and mrow[1] == "video" and mrow[2] == "https://truly.vip/Results/abc")
    # FakeSMTP captured the magic-link email TO the member
    sends = [s for inst in fake_smtp.instances for s in inst.sent]
    assert any("jane@example.com" in to for (_, to, _) in sends)
    # journey_events row
    je = cx.execute(
        "SELECT detail FROM journey_events WHERE trigger='membership_granted'"
    ).fetchall()
    assert len(je) == 1
    d = _json.loads(je[0][0])
    assert d.get("source") == "video"
    assert d.get("days") == 30
    cx.close()


def test_grant_rejects_unknown_source(app_client_mem):
    client, _, _ = app_client_mem
    r = client.post("/admin/membership/grant",
                    json={"email":"x@e.com","source":"bogus_source"},
                    headers=_ckey())
    assert r.status_code == 400


def test_grant_rejects_missing_email(app_client_mem):
    client, _, _ = app_client_mem
    r = client.post("/admin/membership/grant",
                    json={"source":"video"},
                    headers=_ckey())
    assert r.status_code == 400


def test_grant_default_30_days_when_days_unset(app_client_mem):
    client, app_module, db = app_client_mem
    r = client.post("/admin/membership/grant",
                    json={"email":"d30@example.com","source":"video"},
                    headers=_ckey())
    assert r.status_code == 200
    cx = sqlite3.connect(db)
    granted_at, expires_at = cx.execute(
        "SELECT granted_at, expires_at FROM memberships WHERE email=?",
        ("d30@example.com",)
    ).fetchone()
    g = datetime.fromisoformat(granted_at.rstrip("Z"))
    e = datetime.fromisoformat(expires_at.rstrip("Z"))
    # ~30 days within a few seconds
    delta = (e - g).total_seconds()
    assert 30*86400 - 60 <= delta <= 30*86400 + 60
    cx.close()


def test_grant_custom_days_when_specified(app_client_mem):
    client, app_module, db = app_client_mem
    r = client.post("/admin/membership/grant",
                    json={"email":"d90@example.com","source":"video","days":90},
                    headers=_ckey())
    assert r.status_code == 200
    cx = sqlite3.connect(db)
    granted_at, expires_at = cx.execute(
        "SELECT granted_at, expires_at FROM memberships WHERE email=?",
        ("d90@example.com",)
    ).fetchone()
    g = datetime.fromisoformat(granted_at.rstrip("Z"))
    e = datetime.fromisoformat(expires_at.rstrip("Z"))
    delta = (e - g).total_seconds()
    assert 90*86400 - 60 <= delta <= 90*86400 + 60
    cx.close()


def test_lifetime_grant_is_open_ended_and_audited(app_client_mem):
    client, app_module, db = app_client_mem
    r = client.post(
        "/admin/membership/grant",
        json={
            "email": "lifetime@example.com",
            "source": "owner_lifetime",
            "lifetime": True,
            "notes": "Founder-approved permanent access",
        },
        headers={**_ckey(), "X-Console-Granted-By": "glen"},
    )
    assert r.status_code == 200, r.get_json()
    assert r.get_json()["lifetime"] is True
    assert r.get_json()["expires_at"] is None
    cx = sqlite3.connect(db)
    expires_at, granted_by, notes = cx.execute(
        "SELECT expires_at, granted_by, notes FROM memberships "
        "WHERE email='lifetime@example.com'"
    ).fetchone()
    detail = cx.execute(
        "SELECT detail FROM journey_events WHERE email='lifetime@example.com' "
        "AND trigger='membership_granted'"
    ).fetchone()[0]
    cx.close()
    assert expires_at is None
    assert granted_by == "glen"
    assert notes == "Founder-approved permanent access"
    assert _json.loads(detail)["lifetime"] is True
    active = app_module._active_membership_for_email("lifetime@example.com")
    assert active["lifetime"] is True
    assert active["days_remaining"] is None


def test_lifetime_grant_requires_explicit_source_and_notes(app_client_mem):
    client, _, _ = app_client_mem
    no_notes = client.post(
        "/admin/membership/grant",
        json={"email": "x@example.com", "source": "owner_lifetime",
              "lifetime": True},
        headers=_ckey(),
    )
    wrong_source = client.post(
        "/admin/membership/grant",
        json={"email": "x@example.com", "source": "video",
              "lifetime": True, "notes": "approved"},
        headers=_ckey(),
    )
    assert no_notes.status_code == 400
    assert wrong_source.status_code == 400


def test_admin_escalations_requires_console_key(app_client_mem):
    client, _, _ = app_client_mem
    r = client.get("/admin/escalations")
    assert r.status_code in (401, 403)


def test_admin_escalations_returns_only_pending(app_client_mem):
    client, app_module, db = app_client_mem
    # Seed two rows: one pending, one replied
    import uuid
    now_iso = datetime.utcnow().isoformat()+"Z"
    cx = sqlite3.connect(db)
    cx.execute(
        "INSERT INTO escalation_queue "
        "(id, created_at, email, query_text, ai_response, ai_confidence, flag_reason, status) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (str(uuid.uuid4()), now_iso, "a@e.com", "pending q", "ai resp", 0.5, "member_request", "pending"))
    cx.execute(
        "INSERT INTO escalation_queue "
        "(id, created_at, email, query_text, ai_response, ai_confidence, flag_reason, status) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (str(uuid.uuid4()), now_iso, "b@e.com", "replied q", "ai resp", 0.5, "member_request", "replied"))
    cx.commit(); cx.close()
    r = client.get("/admin/escalations", headers=_ckey())
    assert r.status_code == 200
    arr = r.get_json()
    assert isinstance(arr, list)
    emails = [item["email"] for item in arr]
    assert "a@e.com" in emails
    assert "b@e.com" not in emails


# ── Slice 3: member auth + dashboard shell ───────────────────────────────────

import re


@pytest.fixture
def app_client_member(monkeypatch, tmp_path, fake_smtp):
    """Test client with a seeded active membership for jane@example.com."""
    app_module = _load_app()
    db = str(tmp_path / "chat_log.db")
    monkeypatch.setattr(app_module, "LOG_DB", db)
    import dashboard as _dashboard
    monkeypatch.setattr(app_module, "CONSOLE_SECRET", "test-console-secret-xyz")
    monkeypatch.setattr(_dashboard, "CONSOLE_SECRET", "test-console-secret-xyz")
    cx = sqlite3.connect(db)
    cx.executescript("""
        CREATE TABLE IF NOT EXISTS auth_tokens (
          token_hash TEXT PRIMARY KEY, email TEXT, purpose TEXT NOT NULL,
          extra TEXT, created_at TEXT NOT NULL, expires_at TEXT NOT NULL,
          consumed_at TEXT
        );
    """)
    try:
        import begin_funnel
        begin_funnel.init_journey_tables(cx)
    except Exception: pass
    app_module.init_membership_tables(cx)
    # seed an inbound_leads row for client_first lookup (used by /coaching)
    try:
        cx.execute("CREATE TABLE IF NOT EXISTS inbound_leads (id INTEGER PRIMARY KEY AUTOINCREMENT, received_at TEXT, source TEXT, email TEXT, first_name TEXT, last_name TEXT, phone TEXT, raw_json TEXT, ghl_contact_id TEXT, ghl_error TEXT, last_outbound_at TEXT, tags TEXT)")
        cx.execute("INSERT INTO inbound_leads (received_at, source, email, first_name) VALUES (?,?,?,?)",
                   (datetime.utcnow().isoformat()+"Z", "scoreapp", "jane@example.com", "Jane"))
    except Exception: pass
    # seed an active membership
    import uuid as _uuid
    cx.execute(
        "INSERT INTO memberships (id, email, granted_at, expires_at, granted_by, source) "
        "VALUES (?,?,?,?,?,?)",
        (str(_uuid.uuid4()), "jane@example.com",
         datetime.utcnow().isoformat()+"Z",
         (datetime.utcnow()+timedelta(days=27)).isoformat()+"Z",
         "glen", "video")
    )
    cx.commit(); cx.close()
    return app_module.app.test_client(), app_module, db


def test_active_membership_for_email_returns_row(app_client_member):
    _, app_module, _ = app_client_member
    row = app_module._active_membership_for_email("jane@example.com")
    assert row is not None
    assert row["email"] == "jane@example.com"
    assert "days_remaining" in row
    assert 26 <= row["days_remaining"] <= 28


def test_active_membership_for_email_returns_none_when_expired(app_client_member):
    _, app_module, db = app_client_member
    cx = sqlite3.connect(db)
    cx.execute("UPDATE memberships SET expires_at=? WHERE email='jane@example.com'",
               ((datetime.utcnow() - timedelta(days=1)).isoformat()+"Z",))
    cx.commit(); cx.close()
    assert app_module._active_membership_for_email("jane@example.com") is None


def test_auth_token_consumes_and_sets_cookie_redirects_to_coaching(app_client_member):
    client, app_module, db = app_client_member
    plain = app_module._mint_membership_magic_link("jane@example.com")
    # GET only confirms (mail scanners prefetch it); the POST signs in.
    assert client.get(f"/coaching/auth/{plain}").status_code == 200
    r = client.post(f"/coaching/auth/{plain}", follow_redirects=False)
    assert r.status_code in (302, 303)
    assert r.headers.get("Location", "").endswith("/coaching")
    set_cookie = r.headers.get("Set-Cookie", "")
    # multiple Set-Cookie headers can appear; use headers.getlist if available
    cookies = r.headers.getlist("Set-Cookie")
    blob = "\n".join(cookies)
    assert "rm_member_email" in blob
    # Signed since 2026-10-15: the plain address is not in the cookie, and the signed
    # value reads back as Jane.
    assert "jane%40example.com" not in blob and "jane@example.com" not in blob
    value = blob.split("rm_member_email=", 1)[1].split(";", 1)[0]
    with app_module.app.test_request_context("/", headers={"Cookie": f"rm_member_email={value}"}):
        assert app_module._email_from_cookie("rm_member_email") == "jane@example.com"
    # token consumed_at flipped
    cx = sqlite3.connect(db)
    th = app_module._hash_token(plain)
    consumed_at = cx.execute("SELECT consumed_at FROM auth_tokens WHERE token_hash=?", (th,)).fetchone()[0]
    cx.close()
    assert consumed_at is not None


def test_auth_token_bad_token_renders_error_410(app_client_member):
    client, _, _ = app_client_member
    r = client.get("/coaching/auth/not-a-real-token")
    assert r.status_code == 410
    assert b"<html" in r.data.lower() or b"<form" in r.data.lower() or b"invalid" in r.data.lower() or b"valid" in r.data.lower()


def test_login_request_for_unknown_email_returns_200_no_leak(app_client_member, fake_smtp):
    client, _, _ = app_client_member
    fake_smtp.instances = []
    r = client.post("/coaching/login-request", json={"email":"unknown@example.com"})
    assert r.status_code == 200
    j = r.get_json()
    assert "If an active membership" in j["message"]
    # We MUST still send the email so the response timing/content doesn't leak status
    sends = [s for inst in fake_smtp.instances for s in inst.sent]
    assert any("unknown@example.com" in to for (_, to, _) in sends)


def test_login_request_for_active_member_sends_magic_link(app_client_member, fake_smtp):
    client, _, _ = app_client_member
    fake_smtp.instances = []
    r = client.post("/coaching/login-request", json={"email":"jane@example.com"})
    assert r.status_code == 200
    sends = [s for inst in fake_smtp.instances for s in inst.sent]
    raw_all = b"".join(
        raw if isinstance(raw, bytes) else raw.encode("utf-8")
        for (_, _, raw) in sends)
    assert b"/coaching/auth/" in raw_all


def test_coaching_active_renders_iframe_and_days_remaining(app_client_member):
    client, app_module, db = app_client_member
    # Set the rm_member_email cookie
    client.set_cookie("rm_member_email", _signed("rm_member_email", "jane@example.com"))
    r = client.get("/coaching")
    assert r.status_code == 200
    # Iframe to /embed exists
    assert b"/embed" in r.data
    # Days remaining rendered
    assert re.search(rb"2[6-8]", r.data) is not None  # 26/27/28 days
    # Brand bar
    assert b"Remedy" in r.data
    # First name from inbound_leads or email local-part
    assert b"Jane" in r.data or b"jane" in r.data


def test_coaching_lifetime_membership_renders_without_renewal_copy(app_client_member):
    client, _, db = app_client_member
    cx = sqlite3.connect(db)
    cx.execute(
        "UPDATE memberships SET expires_at=NULL WHERE email='jane@example.com'"
    )
    cx.commit()
    cx.close()

    client.set_cookie("rm_member_email", _signed("rm_member_email", "jane@example.com"))
    r = client.get("/coaching")

    assert r.status_code == 200
    assert b"Lifetime access" in r.data
    assert b"Your membership does not expire." in r.data
    assert b"None days remaining" not in r.data
    assert b"When your access ends" not in r.data


def test_coaching_lapsed_renders_truly_vip_cta(app_client_member):
    client, app_module, db = app_client_member
    # No cookie, no active membership for the implicit visitor
    r = client.get("/coaching")
    assert r.status_code == 200
    # Lapsed state: CTA to Truly.VIP/Results
    body_lower = r.data.lower()
    assert b"truly.vip/results" in body_lower


# ── Slice 4: member-mode /chat overlay ───────────────────────────────────────

import json as _json4


def test_active_membership_for_email_returns_none_for_unknown(app_client_member):
    _, app_module, _ = app_client_member
    assert app_module._active_membership_for_email("nobody@example.com") is None


def test_member_context_aggregates_intake_inquiries_queries(app_client_member):
    _, app_module, db = app_client_member
    # Seed:
    #   - inbound_leads scoreapp row for jane (already partially seeded with first_name; add raw_json)
    #   - an inquiries row for jane
    #   - query_log rows for jane
    import uuid as _uuid
    now_iso = datetime.utcnow().isoformat()+"Z"
    cx = sqlite3.connect(db)
    cx.execute("UPDATE inbound_leads SET raw_json=? WHERE email=?",
               (_json4.dumps({"data":{"total_score":{"percent":67},
                              "quiz_questions":[{"question":"Which system?","answers":[{"answer":"Immune"}]}]}}),
                "jane@example.com"))
    # Ensure the inquiries table (and ip column) exist in the test DB.
    cx.execute("""
        CREATE TABLE IF NOT EXISTS inquiries (
          id TEXT PRIMARY KEY, created_at TEXT NOT NULL,
          session_id TEXT NOT NULL, client_email TEXT NOT NULL,
          client_name TEXT, client_phone TEXT, ref_slug TEXT,
          main_challenge TEXT NOT NULL, main_goal TEXT NOT NULL,
          practitioner_count INTEGER NOT NULL
        )
    """)
    try:
        cx.execute("ALTER TABLE inquiries ADD COLUMN ip TEXT")
    except Exception:
        pass  # column already exists
    cx.execute(
        "INSERT INTO inquiries (id, created_at, session_id, client_email, client_name, "
        "client_phone, ref_slug, main_challenge, main_goal, practitioner_count, ip) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (str(_uuid.uuid4()), now_iso, "s1", "jane@example.com", "Jane", "", "",
         "fatigue and brain fog", "stable energy", 1, "1.2.3.4"))
    try:
        cx.execute("CREATE TABLE IF NOT EXISTS query_log (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, email TEXT, question TEXT, answer TEXT, sources TEXT, session_id TEXT)")
    except Exception: pass
    cx.execute("INSERT INTO query_log (ts, email, question, answer) VALUES (?,?,?,?)",
               (now_iso, "jane@example.com", "How do I rebuild my microbiome?", "..."))
    cx.execute("INSERT INTO query_log (ts, email, question, answer) VALUES (?,?,?,?)",
               (now_iso, "jane@example.com", "Should I do a heavy metal detox?", "..."))
    cx.commit(); cx.close()
    ctx = app_module._member_context_for_email("jane@example.com")
    assert isinstance(ctx, dict)
    # Includes the intake fragments
    blob = _json4.dumps(ctx)
    assert "Immune" in blob or "67" in blob
    # Includes recent inquiry
    assert "fatigue" in blob.lower() or "stable" in blob.lower()
    # Includes recent queries
    assert "microbiome" in blob.lower() or "metal" in blob.lower()


def test_member_context_returns_empty_dict_for_unknown_email(app_client_member):
    _, app_module, _ = app_client_member
    ctx = app_module._member_context_for_email("nobody-here@example.com")
    assert isinstance(ctx, dict)
    # Empty/falsy expected fields
    assert not ctx.get("recent_inquiries")
    assert not ctx.get("recent_queries")


def _make_fake_match(text="Test snippet"):
    """Minimal Pinecone-style match object for /chat handler stubs."""
    class _M:
        def __init__(self, t):
            self.metadata = {"text": t, "source": "test", "url": ""}
            self.score = 0.9
            self.id = "t1"
    return _M(text)


def test_chat_member_mode_prepends_member_context_to_rag(app_client_member, monkeypatch):
    """The /chat SSE handler MUST inject a 'MEMBER CONTEXT' block when an active
    member cookie is present.  We stub embed + query_all_namespaces so the handler
    reaches the context-build + test-seam code without external API calls."""
    client, app_module, db = app_client_member
    monkeypatch.setattr(app_module, "_LAST_CONTEXT_STR_FOR_TEST", None, raising=False)
    # Stub RAG pipeline so handler reaches build_context and our seam.
    monkeypatch.setattr(app_module, "embed", lambda text: [0.0] * 1536)
    monkeypatch.setattr(app_module, "query_all_namespaces",
                        lambda vec: [_make_fake_match()])
    # Set the member cookie
    client.set_cookie("rm_member_email", _signed("rm_member_email", "jane@example.com"))
    try:
        r = client.post("/chat",
                        json={"query": "What should I do about my fatigue?",
                              "level": "executive"},
                        buffered=True)
    except Exception:
        pass
    captured_ctx = getattr(app_module, "_LAST_CONTEXT_STR_FOR_TEST", None) or ""
    assert "MEMBER CONTEXT" in captured_ctx or "member" in captured_ctx.lower()


def test_chat_free_tier_unchanged_when_no_member_cookie(app_client_member, monkeypatch):
    """No cookie = no member context block in the system prompt."""
    client, app_module, db = app_client_member
    monkeypatch.setattr(app_module, "_LAST_CONTEXT_STR_FOR_TEST", None, raising=False)
    monkeypatch.setattr(app_module, "embed", lambda text: [0.0] * 1536)
    monkeypatch.setattr(app_module, "query_all_namespaces",
                        lambda vec: [_make_fake_match()])
    # NO set_cookie call — visitor is anonymous
    try:
        r = client.post("/chat",
                        json={"query": "Hi", "level": "executive"},
                        buffered=True)
    except Exception:
        pass
    captured_ctx = getattr(app_module, "_LAST_CONTEXT_STR_FOR_TEST", None) or ""
    assert "MEMBER CONTEXT" not in captured_ctx


def test_chat_seam_not_written_on_non_pytest_traffic(app_client_member, monkeypatch):
    """SECURITY: the _LAST_CONTEXT_STR_FOR_TEST seam must stay None on real
    (non-pytest) traffic so member PII never lands in the module global. The
    write is gated behind PYTEST_CURRENT_TEST; deleting that env var simulates
    a production request, after which the global must remain None."""
    client, app_module, db = app_client_member
    monkeypatch.setattr(app_module, "_LAST_CONTEXT_STR_FOR_TEST", None, raising=False)
    monkeypatch.setattr(app_module, "embed", lambda text: [0.0] * 1536)
    monkeypatch.setattr(app_module, "query_all_namespaces",
                        lambda vec: [_make_fake_match()])
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    client.set_cookie("rm_member_email", _signed("rm_member_email", "jane@example.com"))
    try:
        client.post("/chat",
                    json={"query": "What should I do about my fatigue?",
                          "level": "executive"},
                    buffered=True)
    except Exception:
        pass
    assert getattr(app_module, "_LAST_CONTEXT_STR_FOR_TEST", None) is None


# ── Slice 5: escalation flow ────────────────────────────────────────────────

def test_escalate_requires_active_membership_else_403(app_client_member):
    """No member cookie -> 403; expired/no membership -> 403."""
    client, app_module, db = app_client_member
    # No cookie
    r = client.post("/coaching/escalate", json={"query_text": "hi"})
    assert r.status_code == 403
    # Cookie present but no row
    client.set_cookie("rm_member_email", _signed("rm_member_email", "nobody@example.com"))
    r = client.post("/coaching/escalate", json={"query_text": "hi"})
    assert r.status_code == 403


def test_escalate_inserts_pending_row_and_logs_event(app_client_member):
    client, app_module, db = app_client_member
    client.set_cookie("rm_member_email", _signed("rm_member_email", "jane@example.com"))
    r = client.post("/coaching/escalate",
                    json={"query_text":"What about my hashimoto situation?",
                          "ai_response":"AI said something"})
    assert r.status_code == 200
    assert "Glen has been notified" in r.get_json()["message"]
    cx = sqlite3.connect(db)
    row = cx.execute(
        "SELECT email, query_text, status, flag_reason FROM escalation_queue"
    ).fetchone()
    assert row == ("jane@example.com", "What about my hashimoto situation?",
                   "pending", "member_request")
    je = cx.execute(
        "SELECT trigger FROM journey_events WHERE trigger='membership_escalation_filed'"
    ).fetchone()
    assert je is not None
    cx.close()


def test_admin_escalation_detail_returns_member_context(app_client_member):
    client, app_module, db = app_client_member
    # seed a pending escalation
    import uuid as _uuid
    eid = str(_uuid.uuid4())
    now_iso = datetime.utcnow().isoformat()+"Z"
    cx = sqlite3.connect(db)
    cx.execute(
        "INSERT INTO escalation_queue "
        "(id, created_at, email, query_text, ai_response, flag_reason, status) "
        "VALUES (?,?,?,?,?,?,?)",
        (eid, now_iso, "jane@example.com", "test q", "ai r", "member_request", "pending"))
    cx.commit(); cx.close()
    r = client.get(f"/admin/escalations/{eid}", headers=_ckey())
    assert r.status_code == 200
    body = r.get_json()
    assert body["id"] == eid
    assert body["email"] == "jane@example.com"
    assert "member_context" in body
    assert isinstance(body["member_context"], dict)


def test_admin_escalation_detail_requires_console_key(app_client_member):
    client, _, _ = app_client_member
    r = client.get("/admin/escalations/nonexistent")
    assert r.status_code in (401, 403)


def test_admin_escalation_detail_404_for_unknown(app_client_member):
    client, _, _ = app_client_member
    r = client.get("/admin/escalations/does-not-exist", headers=_ckey())
    assert r.status_code == 404


def test_admin_reply_marks_replied_sends_email_logs_event(app_client_member, fake_smtp):
    client, app_module, db = app_client_member
    # seed pending
    import uuid as _uuid
    eid = str(_uuid.uuid4())
    now_iso = datetime.utcnow().isoformat()+"Z"
    cx = sqlite3.connect(db)
    cx.execute(
        "INSERT INTO escalation_queue "
        "(id, created_at, email, query_text, ai_response, flag_reason, status) "
        "VALUES (?,?,?,?,?,?,?)",
        (eid, now_iso, "jane@example.com", "what should I do?", "ai r", "member_request", "pending"))
    cx.commit(); cx.close()
    fake_smtp.instances = []
    r = client.post(f"/admin/escalations/{eid}/reply",
                    json={"video_url":"https://loom.com/abc",
                          "text":"Here is my take..."},
                    headers=_ckey())
    assert r.status_code == 200
    cx = sqlite3.connect(db)
    row = cx.execute(
        "SELECT status, glen_reply_url, glen_reply_text, replied_at FROM escalation_queue WHERE id=?",
        (eid,)
    ).fetchone()
    assert row[0] == "replied"
    assert row[1] == "https://loom.com/abc"
    assert row[2] == "Here is my take..."
    assert row[3] is not None
    je = cx.execute(
        "SELECT trigger FROM journey_events WHERE trigger='membership_escalation_replied'"
    ).fetchone()
    assert je is not None
    cx.close()
    sends = [s for inst in fake_smtp.instances for s in inst.sent]
    assert any("jane@example.com" in to for (_, to, _) in sends)


def test_admin_reply_rejects_non_pending(app_client_member, fake_smtp):
    client, app_module, db = app_client_member
    import uuid as _uuid
    eid = str(_uuid.uuid4())
    now_iso = datetime.utcnow().isoformat()+"Z"
    cx = sqlite3.connect(db)
    cx.execute(
        "INSERT INTO escalation_queue "
        "(id, created_at, email, query_text, ai_response, flag_reason, status) "
        "VALUES (?,?,?,?,?,?,?)",
        (eid, now_iso, "jane@example.com", "q", "ai r", "member_request", "replied"))
    cx.commit(); cx.close()
    fake_smtp.instances = []
    r = client.post(f"/admin/escalations/{eid}/reply",
                    json={"video_url":"https://loom.com/x","text":"again"},
                    headers=_ckey())
    assert r.status_code == 400


def test_embed_html_renders_talk_to_glen_hook(app_client_member):
    """Static embed.html must contain the JS hook that renders the Talk-to-Glen
    action when SSE done event carries member_mode: true."""
    client, _, _ = app_client_member
    r = client.get("/embed")
    assert r.status_code == 200
    body = r.data.decode("utf-8", errors="replace")
    assert "member_mode" in body
    assert "/coaching/escalate" in body


# ── Slice 6: studio credit + renewal cron ───────────────────────────────────

def test_studio_credit_get_renders_form(app_client_mem):
    client, _, _ = app_client_mem
    r = client.get("/coaching/studio-credit")
    assert r.status_code == 200
    body = r.data
    assert b"<form" in body
    assert b"studio" in body.lower() or b"Studio" in body
    assert b"email" in body.lower()


def test_studio_credit_post_inserts_intent_sends_glen_notification(app_client_mem, fake_smtp):
    client, app_module, db = app_client_mem
    fake_smtp.instances = []
    r = client.post("/coaching/studio-credit",
                    data={"email":"sc@example.com",
                          "studio_ref":"studio receipt #ABC"})
    assert r.status_code == 200
    cx = sqlite3.connect(db)
    row = cx.execute(
        "SELECT email, studio_ref FROM studio_credit_intents"
    ).fetchone()
    assert row == ("sc@example.com", "studio receipt #ABC")
    cx.close()
    # Notification email TO the inbound inquiry mailbox
    sends = [s for inst in fake_smtp.instances for s in inst.sent]
    assert any(app_module.RM_INBOUND_INQUIRY_EMAIL in to for (_, to, _) in sends)
    # Submitted-state HTML rendered
    assert b"Got it" in r.data or b"verify" in r.data.lower()


def test_renewal_cron_requires_console_key(app_client_mem):
    client, _, _ = app_client_mem
    r = client.post("/api/cron/membership-renewals")
    assert r.status_code in (401, 403)


def test_renewal_cron_prepay_sends_no_charge_copy(app_client_mem, fake_smtp, monkeypatch):
    """A prepaid term ending in-window gets the renewal-PROMPT copy (loyalty rate,
    'no automatic charge', /prepay link) — never the truly.vip video-renewal copy."""
    client, app_module, db = app_client_mem
    monkeypatch.setattr(app_module, "PREPAY_LADDER_ENABLED", True, raising=False)
    monkeypatch.setattr(app_module, "PUBLIC_BASE_URL", "https://test.local", raising=False)
    import uuid as _uuid
    now = datetime.utcnow()
    cx = sqlite3.connect(db)
    cx.execute(
        "INSERT INTO memberships (id, email, granted_at, expires_at, granted_by, source) "
        "VALUES (?,?,?,?,?,?)",
        (str(_uuid.uuid4()), "prepaid@example.com",
         (now - timedelta(days=180)).isoformat() + "Z",
         (now + timedelta(days=2)).isoformat() + "Z", "prepay_6mo", "prepay_6mo"))
    cx.commit(); cx.close()
    fake_smtp.instances = []
    r = client.post("/api/cron/membership-renewals", headers=_ckey())
    assert r.status_code == 200 and r.get_json().get("reminded") == 1
    msgs = [str(m) for inst in fake_smtp.instances for (_, to, m) in inst.sent
            if "prepaid@example.com" in to]
    assert msgs, "prepay member was not reminded"
    blob = " ".join(msgs).lower()
    assert "continuous care ends" in blob       # nothing renews by itself (review 2026-11-14)
    assert "renews in" not in blob
    assert "truly.vip" not in blob              # must NOT get the video-renewal body


@pytest.mark.parametrize("source", ["membership_year_prepay", "membership_month",
                                    "membership_year_monthly"])
def test_renewal_cron_membership_door_gets_the_membership_prompt(app_client_mem, fake_smtp,
                                                                 monkeypatch, source):
    """A term bought at /membership never renews by itself. Before 2026-11-13 it fell
    through to the coaching text that asks for a new video at truly.vip/Results."""
    client, app_module, db = app_client_mem
    monkeypatch.setattr(app_module, "PUBLIC_BASE_URL", "https://test.local", raising=False)
    import uuid as _uuid
    now = datetime.utcnow()
    cx = sqlite3.connect(db)
    cx.execute(
        "INSERT INTO memberships (id, email, granted_at, expires_at, granted_by, source) "
        "VALUES (?,?,?,?,?,?)",
        (str(_uuid.uuid4()), "annual@example.com",
         (now - timedelta(days=360)).isoformat() + "Z",
         (now + timedelta(days=2)).isoformat() + "Z", source, source))
    cx.commit(); cx.close()
    fake_smtp.instances = []
    r = client.post("/api/cron/membership-renewals", headers=_ckey())
    assert r.status_code == 200 and r.get_json().get("reminded") == 1
    msgs = [str(m) for inst in fake_smtp.instances for (_, to, m) in inst.sent
            if "annual@example.com" in to]
    assert msgs, "membership-door member was not reminded"
    blob = " ".join(msgs)
    assert "Your Remedy Match membership ends in" in blob
    assert "https://test.local/membership" in blob
    assert "truly.vip" not in blob and "video" not in blob.lower()


@pytest.mark.parametrize("case", ["later_grant", "still_billing"])
def test_renewal_cron_membership_door_says_nothing_while_it_continues(app_client_mem, fake_smtp,
                                                                     monkeypatch, case):
    """Annual monthly is billed 12 times and each charge adds a grant, so an old grant
    ending is not the membership ending (review round 1, 2026-11-13)."""
    client, app_module, db = app_client_mem
    import uuid as _uuid
    from dashboard import subscriptions as _subs
    now = datetime.utcnow()
    cx = sqlite3.connect(db)
    rows = [(now + timedelta(days=2))]
    if case == "later_grant":
        rows.append(now + timedelta(days=33))
    for exp in rows:
        cx.execute(
            "INSERT INTO memberships (id, email, granted_at, expires_at, granted_by, source) "
            "VALUES (?,?,?,?,?,?)",
            (str(_uuid.uuid4()), "billing@example.com", (now - timedelta(days=30)).isoformat() + "Z",
             exp.isoformat() + "Z", "membership_year_monthly", "membership_year_monthly"))
    cx.commit(); cx.close()
    if case == "still_billing":
        monkeypatch.setattr(_subs, "active_memberships_by_email",
                            lambda c, e: [{"id": 1}] if e == "billing@example.com" else [])
    fake_smtp.instances = []
    r = client.post("/api/cron/membership-renewals", headers=_ckey())
    assert r.status_code == 200
    assert not [m for inst in fake_smtp.instances for (_, to, m) in inst.sent
                if "billing@example.com" in to]


def test_renewal_cron_prepay_skipped_when_flag_off(app_client_mem, fake_smtp, monkeypatch):
    client, app_module, db = app_client_mem
    monkeypatch.setattr(app_module, "PREPAY_LADDER_ENABLED", False, raising=False)
    import uuid as _uuid
    now = datetime.utcnow()
    cx = sqlite3.connect(db)
    cx.execute(
        "INSERT INTO memberships (id, email, granted_at, expires_at, granted_by, source) "
        "VALUES (?,?,?,?,?,?)",
        (str(_uuid.uuid4()), "prepaid2@example.com",
         (now - timedelta(days=180)).isoformat() + "Z",
         (now + timedelta(days=2)).isoformat() + "Z", "prepay_6mo", "prepay_6mo"))
    cx.commit(); cx.close()
    fake_smtp.instances = []
    r = client.post("/api/cron/membership-renewals", headers=_ckey())
    assert r.status_code == 200 and r.get_json().get("reminded") == 0


def test_renewal_cron_care_taster_sends_continuous_care_invite(app_client_mem, fake_smtp, monkeypatch):
    """A 30-day care_taster window ending in-window gets the Continuous Care invite copy
    (no auto-charge, /prepay?renew=6mo link) — never the truly.vip video-renewal copy."""
    client, app_module, db = app_client_mem
    monkeypatch.setattr(app_module, "PROGRAM_CARE_TASTER_ENABLED", True, raising=False)
    monkeypatch.setattr(app_module, "PUBLIC_BASE_URL", "https://test.local", raising=False)
    import uuid as _uuid
    now = datetime.utcnow()
    cx = sqlite3.connect(db)
    cx.execute(
        "INSERT INTO memberships (id, email, granted_at, expires_at, granted_by, source) "
        "VALUES (?,?,?,?,?,?)",
        (str(_uuid.uuid4()), "taster@example.com",
         (now - timedelta(days=28)).isoformat() + "Z",
         (now + timedelta(days=2)).isoformat() + "Z", "care_taster", "care_taster"))
    cx.commit(); cx.close()
    fake_smtp.instances = []
    r = client.post("/api/cron/membership-renewals", headers=_ckey())
    assert r.status_code == 200 and r.get_json().get("reminded") == 1
    msgs = [str(m) for inst in fake_smtp.instances for (_, to, m) in inst.sent
            if "taster@example.com" in to]
    assert msgs, "care_taster member was not reminded"
    blob = " ".join(msgs).lower()
    assert "continuous care" in blob
    assert "/prepay" in blob and "renew=6mo" not in blob
    assert "truly.vip" not in blob              # must NOT get the video-renewal body


def test_renewal_cron_care_taster_skipped_when_flag_off(app_client_mem, fake_smtp, monkeypatch):
    client, app_module, db = app_client_mem
    monkeypatch.setattr(app_module, "PROGRAM_CARE_TASTER_ENABLED", False, raising=False)
    import uuid as _uuid
    now = datetime.utcnow()
    cx = sqlite3.connect(db)
    cx.execute(
        "INSERT INTO memberships (id, email, granted_at, expires_at, granted_by, source) "
        "VALUES (?,?,?,?,?,?)",
        (str(_uuid.uuid4()), "taster2@example.com",
         (now - timedelta(days=28)).isoformat() + "Z",
         (now + timedelta(days=2)).isoformat() + "Z", "care_taster", "care_taster"))
    cx.commit(); cx.close()
    fake_smtp.instances = []
    r = client.post("/api/cron/membership-renewals", headers=_ckey())
    assert r.status_code == 200 and r.get_json().get("reminded") == 0


def test_renewal_cron_emails_expiring_in_window(app_client_mem, fake_smtp, monkeypatch):
    """Members whose expires_at is in (now, now+3d) and not reminded in last 24h get reminded."""
    client, app_module, db = app_client_mem
    monkeypatch.setattr(app_module, "PREPAY_LADDER_ENABLED", True, raising=False)
    import uuid as _uuid
    now = datetime.utcnow()
    cx = sqlite3.connect(db)
    # In-window, never reminded
    cx.execute(
        "INSERT INTO memberships (id, email, granted_at, expires_at, granted_by, source) "
        "VALUES (?,?,?,?,?,?)",
        (str(_uuid.uuid4()), "soon@example.com",
         now.isoformat()+"Z",
         (now+timedelta(days=2)).isoformat()+"Z", "prepay_12mo", "prepay_12mo"))
    # Too far out (5d) - NOT reminded
    cx.execute(
        "INSERT INTO memberships (id, email, granted_at, expires_at, granted_by, source) "
        "VALUES (?,?,?,?,?,?)",
        (str(_uuid.uuid4()), "later@example.com",
         now.isoformat()+"Z",
         (now+timedelta(days=5)).isoformat()+"Z", "prepay_12mo", "prepay_12mo"))
    # Already expired - NOT reminded
    cx.execute(
        "INSERT INTO memberships (id, email, granted_at, expires_at, granted_by, source) "
        "VALUES (?,?,?,?,?,?)",
        (str(_uuid.uuid4()), "past@example.com",
         (now-timedelta(days=2)).isoformat()+"Z",
         (now-timedelta(days=1)).isoformat()+"Z", "prepay_12mo", "prepay_12mo"))
    cx.commit(); cx.close()
    fake_smtp.instances = []
    r = client.post("/api/cron/membership-renewals", headers=_ckey())
    assert r.status_code == 200
    assert r.get_json().get("reminded") == 1
    sends_all = [s for inst in fake_smtp.instances for s in inst.sent]
    to_addrs = [to for (_, to, _) in sends_all]
    assert any("soon@example.com" in to for to in to_addrs)
    assert not any("later@example.com" in to for to in to_addrs)
    assert not any("past@example.com" in to for to in to_addrs)


def test_renewal_cron_skips_already_reminded_within_24h(app_client_mem, fake_smtp, monkeypatch):
    client, app_module, db = app_client_mem
    monkeypatch.setattr(app_module, "PREPAY_LADDER_ENABLED", True, raising=False)
    import uuid as _uuid
    now = datetime.utcnow()
    cx = sqlite3.connect(db)
    cx.execute(
        "INSERT INTO memberships (id, email, granted_at, expires_at, granted_by, source, last_reminder_at) "
        "VALUES (?,?,?,?,?,?,?)",
        (str(_uuid.uuid4()), "already@example.com",
         now.isoformat()+"Z",
         (now+timedelta(days=2)).isoformat()+"Z", "prepay_12mo", "prepay_12mo",
         (now-timedelta(minutes=1)).isoformat()+"Z"))
    cx.commit(); cx.close()
    fake_smtp.instances = []
    r = client.post("/api/cron/membership-renewals", headers=_ckey())
    assert r.status_code == 200
    assert r.get_json().get("reminded") == 0
    sends_all = [s for inst in fake_smtp.instances for s in inst.sent]
    assert not any("already@example.com" in to for (_, to, _) in sends_all)


@pytest.mark.parametrize("source", ["video", "biofield_month_delivery", "supply_months",
                                    "comp", "", "something_new"])
def test_renewal_cron_other_grants_get_no_email(app_client_mem, fake_smtp, monkeypatch, source):
    """Glen 2026-11-14: only prepay, the care taster, /membership terms and household
    annuals are reminded. Every other grant gets nothing, never the retired
    truly.vip/Results coaching text, and is not marked as reminded."""
    client, app_module, db = app_client_mem
    monkeypatch.setattr(app_module, "PREPAY_LADDER_ENABLED", True, raising=False)
    monkeypatch.setattr(app_module, "PROGRAM_CARE_TASTER_ENABLED", True, raising=False)
    import uuid as _uuid
    now = datetime.utcnow()
    mid = str(_uuid.uuid4())
    cx = sqlite3.connect(db)
    cx.execute(
        "INSERT INTO memberships (id, email, granted_at, expires_at, granted_by, source) "
        "VALUES (?,?,?,?,?,?)",
        (mid, "other@example.com", now.isoformat()+"Z",
         (now+timedelta(days=2)).isoformat()+"Z", "glen", source))
    cx.commit(); cx.close()
    fake_smtp.instances = []
    r = client.post("/api/cron/membership-renewals", headers=_ckey())
    assert r.status_code == 200 and r.get_json().get("reminded") == 0
    sends_all = [s for inst in fake_smtp.instances for s in inst.sent]
    assert not any("other@example.com" in to for (_, to, _) in sends_all)
    cx = sqlite3.connect(db)
    assert cx.execute("SELECT last_reminder_at FROM memberships WHERE id=?", (mid,)).fetchone()[0] is None
    cx.close()


def _add_membership(db, email, expires_at, source, last=None, mid=None):
    import uuid as _uuid
    mid = mid or str(_uuid.uuid4())
    cx = sqlite3.connect(db)
    cx.execute(
        "INSERT INTO memberships (id, email, granted_at, expires_at, granted_by, source, last_reminder_at) "
        "VALUES (?,?,?,?,?,?,?)",
        (mid, email, datetime.utcnow().isoformat()+"Z", expires_at, source, source, last))
    cx.commit(); cx.close()
    return mid


def _sent_to(fake_smtp, email):
    return [str(m) for inst in fake_smtp.instances for (_, to, m) in inst.sent if email in to]


def test_hawaii_date_uses_hawaii_not_utc(app_client_mem):
    """2026-11-17T08:00Z is 22:00 HST on 16 Nov. Expected values are written out,
    not taken from the helper under test."""
    _, app_module, _ = app_client_mem
    assert app_module._hawaii_date("2026-11-17T08:00:00Z") == date(2026, 11, 16)
    assert app_module._hawaii_date("2026-11-17T11:00:00Z") == date(2026, 11, 17)
    assert "16 November 2026" in app_module._family_long_date("2026-11-17T08:00:00Z")


@pytest.mark.parametrize("hours,phrase", [(6, "today"), (30, "tomorrow")])
def test_renewal_cron_last_days_say_today_and_tomorrow(app_client_mem, fake_smtp, monkeypatch,
                                                       hours, phrase):
    """Never "ends in 0 days"."""
    client, app_module, db = app_client_mem
    monkeypatch.setattr(app_module, "PREPAY_LADDER_ENABLED", True, raising=False)
    from zoneinfo import ZoneInfo
    now_h = datetime.now(ZoneInfo("Pacific/Honolulu"))
    # an expiry on a known Hawaii date: today 23:00 HST, or tomorrow 23:00 HST
    day = now_h.date() + timedelta(days=0 if phrase == "today" else 1)
    exp_h = datetime(day.year, day.month, day.day, 23, 0, tzinfo=ZoneInfo("Pacific/Honolulu"))
    exp = exp_h.astimezone(timezone.utc).replace(tzinfo=None).isoformat() + "Z"
    if exp_h <= now_h:
        pytest.skip("too late in the Hawaii day to build a future 'today' expiry")
    _add_membership(db, "last@example.com", exp, "prepay_12mo")
    fake_smtp.instances = []
    client.post("/api/cron/membership-renewals", headers=_ckey())
    raw = " ".join(_sent_to(fake_smtp, "last@example.com"))
    msgs = raw.lower()
    assert f"ends {phrase}" in msgs
    assert "0 days" not in msgs
    assert f"{day.day} {day.strftime('%B')} {day.year}" in raw      # the Hawaii date, written out
    assert exp[:10] not in raw or exp[:10] == day.isoformat()


def test_renewal_cron_yesterdays_reminder_does_not_block_today(app_client_mem, fake_smtp, monkeypatch):
    """Daily, by Hawaii date: a reminder stamped yesterday in Hawaii, even under 24
    hours ago, does not suppress today's."""
    client, app_module, db = app_client_mem
    monkeypatch.setattr(app_module, "PREPAY_LADDER_ENABLED", True, raising=False)
    from zoneinfo import ZoneInfo
    now_h = datetime.now(ZoneInfo("Pacific/Honolulu"))
    y = now_h.date() - timedelta(days=1)
    last_h = datetime(y.year, y.month, y.day, 23, 59, tzinfo=ZoneInfo("Pacific/Honolulu"))
    last = last_h.astimezone(timezone.utc).replace(tzinfo=None).isoformat() + "Z"
    _add_membership(db, "daily@example.com", (datetime.utcnow()+timedelta(days=2)).isoformat()+"Z",
                    "prepay_12mo", last=last)
    fake_smtp.instances = []
    r = client.post("/api/cron/membership-renewals", headers=_ckey())
    assert r.get_json().get("reminded") == 1
    assert _sent_to(fake_smtp, "daily@example.com")


@pytest.mark.parametrize("source", ["prepay_12mo", "care_taster_30d"])
def test_renewal_cron_renewed_prepay_or_taster_is_silent(app_client_mem, fake_smtp, monkeypatch, source):
    """A later grant (a renewal, or a taster buyer who moved to prepay) means this row
    ending is not their care ending: no "renew here" email."""
    client, app_module, db = app_client_mem
    monkeypatch.setattr(app_module, "PREPAY_LADDER_ENABLED", True, raising=False)
    monkeypatch.setattr(app_module, "PROGRAM_CARE_TASTER_ENABLED", True, raising=False)
    src = app_module.CARE_TASTER_SOURCE if source.startswith("care") else source
    now = datetime.utcnow()
    _add_membership(db, "renewed@example.com", (now+timedelta(days=2)).isoformat()+"Z", src)
    _add_membership(db, "renewed@example.com", (now+timedelta(days=370)).isoformat()+"Z", "prepay_12mo")
    fake_smtp.instances = []
    r = client.post("/api/cron/membership-renewals", headers=_ckey())
    assert r.get_json().get("reminded") == 0
    assert not _sent_to(fake_smtp, "renewed@example.com")


def test_renewal_cron_household_covered_member_is_silent(app_client_mem, fake_smtp, monkeypatch):
    """A holder who upgraded to a running household plan keeps the old membership row;
    it ending must not tell them their membership ends."""
    client, app_module, db = app_client_mem
    monkeypatch.setenv("FAMILY_PLAN_ENABLED", "true")
    from dashboard import family_plan as _fp
    from dashboard import db as _db
    now = datetime.utcnow()
    src = sorted(app_module._membership_product_sources())[0]
    _add_membership(db, "household@example.com", (now+timedelta(days=2)).isoformat()+"Z", src)
    with _db.connect(db) as cx:
        _fp.init_family_plan_table(cx)
        _fp.activate(cx, "household@example.com",
                     next_charge_at=(now+timedelta(days=20)).date().isoformat())
        cx.commit()
    fake_smtp.instances = []
    r = client.post("/api/cron/membership-renewals", headers=_ckey())
    assert not _sent_to(fake_smtp, "household@example.com"), r.get_json()


def test_renewal_cron_household_ending_first_still_reminds(app_client_mem, fake_smtp, monkeypatch):
    """A cancelled household plan that ends before the membership row is no cover."""
    client, app_module, db = app_client_mem
    monkeypatch.setenv("FAMILY_PLAN_ENABLED", "true")
    monkeypatch.setattr(app_module, "PUBLIC_BASE_URL", "https://test.local", raising=False)
    from dashboard import family_plan as _fp
    from dashboard import db as _db
    now = datetime.utcnow()
    src = sorted(app_module._membership_product_sources())[0]
    _add_membership(db, "lapsing@example.com", (now+timedelta(days=2)).isoformat()+"Z", src)
    with _db.connect(db) as cx:
        _fp.init_family_plan_table(cx)
        _fp.activate(cx, "lapsing@example.com",
                     next_charge_at=(now+timedelta(days=1)).date().isoformat())
        _fp.set_status(cx, "lapsing@example.com", _fp.CANCELLING)
        cx.commit()
    fake_smtp.instances = []
    client.post("/api/cron/membership-renewals", headers=_ckey())
    assert _sent_to(fake_smtp, "lapsing@example.com")


def test_renewal_cron_two_rows_one_email(app_client_mem, fake_smtp, monkeypatch):
    """One person with two eligible rows ending in the window gets one email per run."""
    client, app_module, db = app_client_mem
    monkeypatch.setattr(app_module, "PREPAY_LADDER_ENABLED", True, raising=False)
    monkeypatch.setattr(app_module, "PROGRAM_CARE_TASTER_ENABLED", True, raising=False)
    monkeypatch.setattr(app_module, "_cover_continues", lambda r: False)
    now = datetime.utcnow()
    _add_membership(db, "twice@example.com", (now+timedelta(days=1)).isoformat()+"Z", "prepay_12mo")
    _add_membership(db, "twice@example.com", (now+timedelta(days=2)).isoformat()+"Z",
                    app_module.CARE_TASTER_SOURCE)
    fake_smtp.instances = []
    client.post("/api/cron/membership-renewals", headers=_ckey())
    assert len(_sent_to(fake_smtp, "twice@example.com")) == 1


def test_daily_cron_runs_membership_renewals_with_console_key(monkeypatch):
    """Run the daily piggybacks for real with HTTP stubbed: the renewal POST happens,
    with X-Console-Key, and main's finally block calls the piggybacks."""
    import importlib.util, pathlib, sys as _sys, ast
    scripts = pathlib.Path(__file__).resolve().parent.parent / "scripts"
    monkeypatch.setenv("CRON_SECRET", "fake-cron")
    monkeypatch.setenv("CONSOLE_SECRET", "fake-console")
    monkeypatch.syspath_prepend(str(scripts))
    spec = importlib.util.spec_from_file_location("_rpec", scripts / "run_personal_email_cron.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    calls = []
    monkeypatch.setattr(mod, "_piggyback_post", lambda label, path, header, secret, **k: calls.append((label, path, header, secret)))
    import urllib.request as _ur
    monkeypatch.setattr(_ur, "urlopen", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no network")))
    mod.run_daily_piggybacks()
    assert ("membership-renewals", "/api/cron/membership-renewals", "X-Console-Key", "fake-console") in calls
    tree = ast.parse((scripts / "run_personal_email_cron.py").read_text())
    called = {n.func.id for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    assert "run_daily_piggybacks" in called


# ── magic-link lifetimes ─────────────────────────────────────────────────────
# A membership magic link arrives by email. The window it stays valid for has to
# match how the recipient got it: a link they just asked for vs. one we pushed at
# them unprompted. These pin both, because the old shared 15-minute default made
# every grant email dead on arrival.

def _token_window(app_module, db, plain):
    """Return (expires_at - created_at) for a minted token."""
    cx = sqlite3.connect(db)
    row = cx.execute(
        "SELECT created_at, expires_at FROM auth_tokens WHERE token_hash=?",
        (app_module._hash_token(plain),)
    ).fetchone()
    cx.close()
    assert row is not None
    parse = lambda s: datetime.fromisoformat(s.rstrip("Z"))
    return parse(row[1]) - parse(row[0])


def test_requested_signin_link_survives_a_delayed_inbox_check(app_module_with_db):
    """15 minutes was shorter than the gap between sending mail and reading it."""
    app_module, db = app_module_with_db
    plain = app_module._mint_membership_magic_link("slowreader@example.com")
    window = _token_window(app_module, db, plain)
    assert window >= timedelta(hours=1)
    # the mint reads the clock twice, so allow a hair of drift
    assert abs(window - timedelta(minutes=app_module.AUTH_TOKEN_TTL_MIN)) < timedelta(seconds=1)


def test_unprompted_grant_email_link_lives_for_weeks(app_module_with_db, monkeypatch):
    """The studio-credit grant email is not a response to any action the member
    took, so it must still work when they get round to it. Asserts the call site
    overrides the interactive default -- that override is the whole fix."""
    app_module, db = app_module_with_db
    seen = {}

    def _spy_mint(email, ttl_min=app_module.MEMBERSHIP_MAGIC_TTL_MIN, *, cx=None):
        seen["email"], seen["ttl_min"], seen["cx"] = email, ttl_min, cx
        return "fake-token"

    monkeypatch.setattr(app_module, "_mint_membership_magic_link", _spy_mint)
    monkeypatch.setattr(app_module, "_send_inquiry_email", lambda **kw: True)
    monkeypatch.setattr(app_module, "_grant_membership", lambda cx, e, d, s: "mid-1")
    cx = sqlite3.connect(db)
    app_module._studio_credit_grant_and_notify(cx, "gifted@example.com", 30)
    cx.close()
    assert seen["email"] == "gifted@example.com"
    assert timedelta(minutes=seen["ttl_min"]) >= timedelta(days=29), (
        "an unprompted grant email must not carry the interactive sign-in TTL")
    assert seen["cx"] is cx, (
        "the mint must reuse the caller's connection -- a second connection "
        "against its open write transaction is 'database is locked'")


def test_signin_email_copy_states_the_real_window(app_module_with_db):
    """The body used to promise '15 minutes'. Copy and constant must not drift."""
    app_module, _ = app_module_with_db
    assert app_module.AUTH_TOKEN_TTL_LABEL == "a week"
    assert timedelta(minutes=app_module.AUTH_TOKEN_TTL_MIN) == timedelta(days=7)


def _household_tables(db):
    from dashboard import family_plan as _fp, household as _hh, db as _db
    with _db.connect(db) as cx:
        _fp.init_family_plan_table(cx)
        for name in ("init_household_tables",):
            if hasattr(_hh, name):
                getattr(_hh, name)(cx)
                break
        cx.commit()


def test_linked_member_warned_when_caregivers_plan_ends_first(app_client_mem, fake_smtp, monkeypatch):
    """Round 3: Bob's prepay ends in 2 days; his caregiver's cancelled household plan
    ends tomorrow. That plan is no cover past Bob's date, so Bob is reminded."""
    client, app_module, db = app_client_mem
    monkeypatch.setenv("FAMILY_PLAN_ENABLED", "true")
    monkeypatch.setattr(app_module, "PREPAY_LADDER_ENABLED", True, raising=False)
    from dashboard import family_plan as _fp, db as _db
    _household_tables(db)
    now = datetime.utcnow()
    _add_membership(db, "bob@example.com", (now+timedelta(days=2)).isoformat()+"Z", "prepay_12mo")
    with _db.connect(db) as cx:
        _fp.activate(cx, "alice@example.com", next_charge_at=(now+timedelta(days=1)).date().isoformat())
        _fp.set_status(cx, "alice@example.com", _fp.CANCELLING)
        cx.execute("INSERT INTO household_members (primary_email, member_email, created_at) VALUES (?,?,?)",
                   ("alice@example.com", "bob@example.com", now.isoformat()))
        cx.commit()
    from dashboard import household as _hh
    with _db.connect(db) as cx:
        assert _hh.caregivers_for(cx, "bob@example.com"), "link not seeded"
    fake_smtp.instances = []
    client.post("/api/cron/membership-renewals", headers=_ckey())
    assert _sent_to(fake_smtp, "bob@example.com")


def test_linked_member_silent_under_an_ongoing_household_plan(app_client_mem, fake_smtp, monkeypatch):
    client, app_module, db = app_client_mem
    monkeypatch.setenv("FAMILY_PLAN_ENABLED", "true")
    monkeypatch.setattr(app_module, "PREPAY_LADDER_ENABLED", True, raising=False)
    from dashboard import family_plan as _fp, db as _db
    _household_tables(db)
    now = datetime.utcnow()
    _add_membership(db, "carol@example.com", (now+timedelta(days=2)).isoformat()+"Z", "prepay_12mo")
    with _db.connect(db) as cx:
        _fp.activate(cx, "dan@example.com", next_charge_at=(now+timedelta(days=20)).date().isoformat())
        cx.execute("INSERT INTO household_members (primary_email, member_email, created_at) VALUES (?,?,?)",
                   ("dan@example.com", "carol@example.com", now.isoformat()))
        cx.commit()
    fake_smtp.instances = []
    client.post("/api/cron/membership-renewals", headers=_ckey())
    assert not _sent_to(fake_smtp, "carol@example.com")


def test_a_later_unreminded_grant_does_not_silence_the_prompt(app_client_mem, fake_smtp, monkeypatch):
    """Round 3: a later biofield_month_delivery row is never reminded itself, so it must
    not silence the prepay prompt."""
    client, app_module, db = app_client_mem
    monkeypatch.setattr(app_module, "PREPAY_LADDER_ENABLED", True, raising=False)
    now = datetime.utcnow()
    _add_membership(db, "carl@example.com", (now+timedelta(days=1)).isoformat()+"Z", "prepay_12mo")
    _add_membership(db, "carl@example.com", (now+timedelta(days=2, hours=12)).isoformat()+"Z",
                    "biofield_month_delivery")
    fake_smtp.instances = []
    client.post("/api/cron/membership-renewals", headers=_ckey())
    assert len(_sent_to(fake_smtp, "carl@example.com")) == 1


def test_three_hawaii_days_out_is_not_yet_reminded(app_client_mem, fake_smtp, monkeypatch):
    """Only the last three Hawaii days (2, 1, today) are reminded: no fourth email."""
    client, app_module, db = app_client_mem
    monkeypatch.setattr(app_module, "PREPAY_LADDER_ENABLED", True, raising=False)
    from zoneinfo import ZoneInfo
    now_h = datetime.now(ZoneInfo("Pacific/Honolulu"))
    d = now_h.date() + timedelta(days=3)
    late_h = datetime(d.year, d.month, d.day, 7, 30, tzinfo=ZoneInfo("Pacific/Honolulu"))
    early_h = datetime(d.year, d.month, d.day, 0, 30, tzinfo=ZoneInfo("Pacific/Honolulu"))
    now_utc = datetime.now(timezone.utc)
    if late_h.astimezone(timezone.utc) - now_utc >= timedelta(days=3):
        pytest.skip("outside the 3-day SQL window at this hour")
    for who, at in (("late@example.com", late_h), ("early@example.com", early_h)):
        _add_membership(db, who, at.astimezone(timezone.utc).replace(tzinfo=None).isoformat()+"Z",
                        "prepay_12mo")
    fake_smtp.instances = []
    client.post("/api/cron/membership-renewals", headers=_ckey())
    assert not _sent_to(fake_smtp, "late@example.com")
    # Ends before the 7 AM run on its own date: its three emails start a day sooner.
    assert "ends in 3 days" in " ".join(_sent_to(fake_smtp, "early@example.com")).lower()


def test_household_reminder_skips_someone_already_reminded_this_run(app_client_mem, monkeypatch):
    _, app_module, _ = app_client_mem
    monkeypatch.setattr(app_module, "_family_plan_enabled", lambda: True)
    from dashboard import family_plan as _fp
    monkeypatch.setattr(_fp, "terms_ending", lambda cx, today: [
        {"caregiver_email": "holder@example.com", "next_charge_at": "2099-01-02"}])
    claimed = []
    monkeypatch.setattr(_fp, "claim_reminder", lambda *a: claimed.append(a) or None)
    sent = []
    monkeypatch.setattr(app_module, "_send_inquiry_email", lambda **k: sent.append(k) or True)
    assert app_module._remind_household_terms_ending("x", sent_to={"holder@example.com"}) == 0
    assert not sent and not claimed


def test_a_retry_the_same_day_sends_nothing_more(app_client_mem, fake_smtp, monkeypatch):
    """Round 4: two rows for one person, then a second run the same day. One email."""
    client, app_module, db = app_client_mem
    monkeypatch.setattr(app_module, "PREPAY_LADDER_ENABLED", True, raising=False)
    monkeypatch.setattr(app_module, "PROGRAM_CARE_TASTER_ENABLED", True, raising=False)
    monkeypatch.setattr(app_module, "_cover_continues", lambda r: False)
    exp = (datetime.utcnow()+timedelta(days=1)).isoformat()+"Z"
    _add_membership(db, "retry@example.com", exp, "prepay_12mo")
    _add_membership(db, "retry@example.com", exp, app_module.CARE_TASTER_SOURCE)
    fake_smtp.instances = []
    client.post("/api/cron/membership-renewals", headers=_ckey())
    client.post("/api/cron/membership-renewals", headers=_ckey())
    assert len(_sent_to(fake_smtp, "retry@example.com")) == 1


def test_a_failed_send_is_released_for_the_next_run(app_client_mem, fake_smtp, monkeypatch):
    client, app_module, db = app_client_mem
    monkeypatch.setattr(app_module, "PREPAY_LADDER_ENABLED", True, raising=False)
    mid = _add_membership(db, "fail@example.com", (datetime.utcnow()+timedelta(days=1)).isoformat()+"Z",
                          "prepay_12mo")
    monkeypatch.setattr(app_module, "_send_inquiry_email", lambda **k: False)
    r = client.post("/api/cron/membership-renewals", headers=_ckey())
    assert r.get_json().get("reminded") == 0
    cx = sqlite3.connect(db)
    assert cx.execute("SELECT last_reminder_at FROM memberships WHERE id=?", (mid,)).fetchone()[0] is None
    cx.close()


def test_unknown_cover_sends_nothing(app_client_mem, fake_smtp, monkeypatch):
    """Round 4: if billing cannot be read, say nothing rather than 'it ends'."""
    client, app_module, db = app_client_mem
    monkeypatch.setattr(app_module, "PREPAY_LADDER_ENABLED", True, raising=False)
    from dashboard import subscriptions as _subs
    def boom(*a, **k):
        raise RuntimeError("db down")
    monkeypatch.setattr(_subs, "active_memberships_by_email", boom)
    _add_membership(db, "unknown@example.com", (datetime.utcnow()+timedelta(days=1)).isoformat()+"Z",
                    "prepay_12mo")
    fake_smtp.instances = []
    client.post("/api/cron/membership-renewals", headers=_ckey())
    assert not _sent_to(fake_smtp, "unknown@example.com")


def test_household_retry_after_member_reminder_sends_nothing(app_client_mem, monkeypatch):
    """Round 4: a holder reminded from their membership row today is not mailed again
    by the household path on a later run."""
    _, app_module, db = app_client_mem
    monkeypatch.setattr(app_module, "_family_plan_enabled", lambda: True)
    from dashboard import family_plan as _fp
    _add_membership(db, "holder2@example.com", "2099-01-01T00:00:00Z", "prepay_12mo",
                    last=datetime.utcnow().isoformat()+"Z")
    monkeypatch.setattr(_fp, "terms_ending", lambda cx, today: [
        {"caregiver_email": "holder2@example.com", "next_charge_at": "2099-01-02"}])
    sent = []
    monkeypatch.setattr(app_module, "_send_inquiry_email", lambda **k: sent.append(k) or True)
    monkeypatch.setattr(_fp, "claim_reminder", lambda *a: None)
    assert app_module._remind_household_terms_ending("x", sent_to=set()) == 0
    assert not sent


def test_early_morning_expiry_gets_its_third_email_a_day_sooner(app_client_mem, monkeypatch):
    """An expiry before the 7 AM run is over on its own date: 3 Hawaii days out counts."""
    _, app_module, _ = app_client_mem
    assert app_module._hawaii_hour("2026-11-19T10:30:00Z") == 0      # 00:30 HST
    assert app_module._hawaii_hour("2026-11-19T20:00:00Z") == 10
