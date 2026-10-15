"""Sign-in hardening, Glen's yes on 2026-10-15 (formulation's review, verified locally).

1. The reorder, cert and biofield sign-in cookies were a plain email. Anyone could set
   another customer's address and read their orders or cancel their autoship. They are
   now signed, and a plain, forged, swapped or expired value reads as nobody.
2. /auth/magic-link/verify accepted any emailed token, so a staff view pass bound to a
   client made a 30-day session as that client. Only a magic_link token signs in now.
3. With no mail path, the sign-in link was printed to the logs. Now it never is.
4. A JSON answer that sets no caching rule is private and never stored.
"""
import importlib
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

SECRET = "ci-fake-console-secret"


@pytest.fixture
def appmod(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CONSOLE_SECRET", SECRET)
    import app as a
    importlib.reload(a)
    a.app.config["TESTING"] = True
    a._init_auth_tables()
    return a


def _mint(a, purpose, email="buyer@example.com"):
    import secrets
    tok = secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc)
    with sqlite3.connect(a.LOG_DB) as cx:
        cx.execute(
            "INSERT INTO auth_tokens (token_hash, email, purpose, created_at, expires_at) "
            "VALUES (?,?,?,?,?)",
            (a._hash_token(tok), email, purpose, now.isoformat(),
             (now + timedelta(minutes=60)).isoformat()))
        cx.commit()
    return tok


def _read(a, name, value):
    with a.app.test_request_context("/", headers={"Cookie": f"{name}={value}"}):
        return a._email_from_cookie(name)


# ---- 1. signed email cookies ------------------------------------------------------

@pytest.mark.parametrize("name", ["rm_reorder_email", "rm_cert_email", "rm_biofield_email"])
def test_a_signed_cookie_reads_back_and_a_plain_one_reads_as_nobody(appmod, name):
    assert _read(appmod, name, appmod._sign_email_cookie(name, "Buyer@Example.com")) == "buyer@example.com"
    assert _read(appmod, name, "victim@example.com") == ""


def test_a_tampered_or_swapped_cookie_reads_as_nobody(appmod):
    good = appmod._sign_email_cookie("rm_reorder_email", "buyer@example.com")
    assert _read(appmod, "rm_reorder_email", good[:-2] + ("AA" if good[-2:] != "AA" else "BB")) == ""
    # A reorder cookie cannot be replayed as the biofield one.
    assert _read(appmod, "rm_biofield_email", good) == ""


def test_an_expired_cookie_reads_as_nobody(appmod, monkeypatch):
    good = appmod._sign_email_cookie("rm_reorder_email", "buyer@example.com")
    monkeypatch.setattr(appmod, "_EMAIL_COOKIE_MAX_AGE", -1)
    assert _read(appmod, "rm_reorder_email", good) == ""


def test_with_no_secret_nothing_is_signed_or_accepted(appmod, monkeypatch):
    good = appmod._sign_email_cookie("rm_reorder_email", "buyer@example.com")
    monkeypatch.delenv("CONSOLE_SECRET", raising=False)
    monkeypatch.delenv("WEBHOOK_SECRET", raising=False)
    assert appmod._sign_email_cookie("rm_reorder_email", "buyer@example.com") == ""
    assert _read(appmod, "rm_reorder_email", good) == ""


def test_the_reorder_link_sets_a_signed_cookie(appmod):
    tok = _mint(appmod, "reorder")
    r = appmod.app.test_client().post(f"/reorder/auth/{tok}")
    assert r.status_code == 302
    cookie = r.headers["Set-Cookie"]
    value = cookie.split("rm_reorder_email=", 1)[1].split(";", 1)[0]
    assert "buyer@example.com" not in cookie
    assert _read(appmod, "rm_reorder_email", value) == "buyer@example.com"


def test_a_forged_cookie_cannot_read_another_customers_autoship(appmod):
    c = appmod.app.test_client()
    c.set_cookie("rm_reorder_email", "victim@example.com")
    with appmod.app.test_request_context("/", headers={"Cookie": "rm_reorder_email=victim@example.com"}):
        assert appmod._reorder_email_from_cookie() == ""
    r = c.get("/api/subscription/details")
    assert "victim@example.com" not in r.get_data(as_text=True)


# ---- 2. only a sign-in token signs in ----------------------------------------------

@pytest.mark.parametrize("purpose", ["staff_view_pass", "reorder", "biofield", "membership_cancel"])
def test_another_purposes_token_does_not_sign_in(appmod, purpose):
    tok = _mint(appmod, purpose)
    c = appmod.app.test_client()
    assert c.get(f"/auth/magic-link/verify?token={tok}").status_code == 400
    r = c.post("/auth/magic-link/verify", data={"token": tok})
    assert r.status_code == 400
    assert "amg_auth=" not in r.headers.get("Set-Cookie", "")


def test_a_magic_link_token_still_signs_in(appmod):
    tok = _mint(appmod, "magic_link")
    r = appmod.app.test_client().post("/auth/magic-link/verify", data={"token": tok})
    assert r.status_code in (200, 302)
    assert "amg_auth=" in r.headers.get("Set-Cookie", "")


# ---- 3. no link in the logs --------------------------------------------------------

def test_an_unsent_magic_link_is_never_logged(appmod, monkeypatch, capsys):
    for k in ("GHL_MAGIC_LINK_WORKFLOW_ID", "SMTP_HOST", "SMTP_USER", "SMTP_PASS"):
        monkeypatch.delenv(k, raising=False)
    url = "https://example.test/auth/magic-link/verify?token=SECRET-TOKEN-XYZ"
    via, _ = appmod.send_magic_link_email("buyer@example.com", "B", url)
    assert via == "console-log"
    assert "SECRET-TOKEN-XYZ" not in capsys.readouterr().out


def test_an_unsent_affiliate_link_is_never_logged(appmod, monkeypatch, capsys):
    for k in ("SMTP_HOST", "SMTP_USER", "SMTP_PASS"):
        monkeypatch.delenv(k, raising=False)
    url = "https://example.test/affiliate/login-verify?token=SECRET-TOKEN-XYZ"
    appmod._send_affiliate_magic_link("aff@example.com", "A", url)
    assert "SECRET-TOKEN-XYZ" not in capsys.readouterr().out


# ---- 4. private JSON -----------------------------------------------------------------

def test_json_without_a_caching_rule_is_private(appmod):
    r = appmod.app.test_client().get("/api/subscription/details")
    assert r.mimetype == "application/json"
    assert r.headers["Cache-Control"] == "private, no-store"


def test_a_routes_own_caching_rule_is_kept(appmod):
    @appmod.app.route("/__test_cached_json")
    def _cached():
        resp = appmod.jsonify({"ok": True})
        resp.headers["Cache-Control"] = "public, max-age=60"
        return resp
    r = appmod.app.test_client().get("/__test_cached_json")
    assert r.headers["Cache-Control"] == "public, max-age=60"
