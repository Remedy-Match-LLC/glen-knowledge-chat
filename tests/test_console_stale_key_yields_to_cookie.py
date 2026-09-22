"""A stale console key must not outrank a valid login cookie.

Glen, 2026-09-22: "I can't get into my console at Sell: Orders - it wants a console key.
Rae still has the same problem."

THE CAUSE. The console master key was rotated on 2026-09-16. Browsers still hold the old one
in localStorage.console_key, and op-nav adds it to every internal link as ?key=, while the
Orders page sends it as X-Console-Key on every fetch. _present_console_key() returned any
presented key BEFORE it looked at the login cookie. So a dead key shadowed a valid session:
the owner signs in by magic link, clicks Sell, and still gets 401 and the key prompt.

THE FIX. A presented key that authenticates as nothing (not the master secret, not an active
access token) yields to a valid login cookie. The before_request bridge then rewrites the
header, so the routes that read X-Console-Key directly see the cookie's identity too.

WHAT MUST NOT CHANGE:
  - a stale key with NO valid cookie is still refused
  - a VALID key keeps its own identity, even beside an owner cookie. A VA token (Shaira)
    must never be swapped for a more powerful credential, and never swapped at all.
  - rotating CONSOLE_SECRET still invalidates every master-secret cookie
"""
import pytest

STALE = "old-master-key-from-before-the-rotation"


@pytest.fixture
def client(monkeypatch, tmp_path):
    import app as appmod
    monkeypatch.setattr(appmod, "LOG_DB", str(tmp_path / "chat_log.db"))
    monkeypatch.setattr(appmod, "CONSOLE_SECRET", "test-secret")
    appmod.app.config["TESTING"] = True
    return appmod.app.test_client(), appmod


def _sign_in_with_master(c):
    r = c.get("/console/pages?key=test-secret", headers={"Accept": "text/html"})
    assert r.status_code == 302, "setup: the master-key browser login must set the cookie"


def _sign_in_with_owner_token(c, appmod, monkeypatch, token="rae-owner-token"):
    """What the magic link leaves behind: a cookie holding an OWNER access token."""
    monkeypatch.setattr(appmod, "_role_for_token",
                        lambda t: appmod._bos_rbac.OWNER if t == token else None)
    c.set_cookie(appmod.CONSOLE_COOKIE, token)


# --- the reported bug -------------------------------------------------------------------

def test_stale_header_beside_a_master_cookie_is_accepted(client):
    c, _ = client
    _sign_in_with_master(c)
    r = c.get("/api/console/next-actions", headers={"X-Console-Key": STALE})
    assert r.status_code == 200, (
        "REGRESSION: a dead key in localStorage shadowed a valid login, which is the "
        "key prompt on Sell: Orders"
    )


def test_stale_query_key_beside_a_master_cookie_is_accepted(client):
    """op-nav writes the stored key into every link as ?key=."""
    c, _ = client
    _sign_in_with_master(c)
    r = c.get("/api/console/next-actions?key=" + STALE)
    assert r.status_code == 200


def test_the_orders_board_accepts_a_stale_header_beside_an_owner_cookie(client, monkeypatch):
    """Rae's exact shape: magic-link owner cookie, dead key in the header. The Orders
    board's API resolves its caller through _bos_actor(), so that is what is asked."""
    c, appmod = client
    _sign_in_with_owner_token(c, appmod, monkeypatch)
    with appmod.app.test_request_context(
            "/api/orders", headers={"X-Console-Key": STALE,
                                    "Cookie": appmod.CONSOLE_COOKIE + "=rae-owner-token"}):
        actor = appmod._bos_actor()
    assert actor is not None and actor.role == appmod._bos_rbac.OWNER, (
        "the Orders board refused a signed-in owner"
    )


def test_a_route_reading_the_header_directly_sees_the_cookie(client):
    """_auth() reads X-Console-Key itself, never _present_console_key(). The bridge
    must rewrite the header, or those routes keep the bug."""
    c, appmod = client
    from flask import request
    with appmod.app.test_request_context(
            "/api/console/next-actions",
            headers={"X-Console-Key": STALE,
                     "Cookie": appmod.CONSOLE_COOKIE + "=" + appmod._console_cookie_value()}):
        appmod._console_browser_login()
        assert request.headers.get("X-Console-Key") == "test-secret"
        ok, ctx, code = appmod._auth()
        assert ok and ctx["scope"] == "admin"


# --- what must not change ---------------------------------------------------------------

def test_a_stale_key_with_no_cookie_is_still_refused(client):
    c, _ = client
    r = c.get("/api/console/next-actions", headers={"X-Console-Key": STALE})
    assert r.status_code == 401


def test_a_valid_va_token_is_never_swapped_for_the_owner_cookie(client, monkeypatch):
    c, appmod = client
    _sign_in_with_master(c)
    monkeypatch.setattr(appmod, "_role_for_token",
                        lambda t: "va" if t == "shaira-va-token" else None)
    with appmod.app.test_request_context(
            "/api/console/next-actions",
            headers={"X-Console-Key": "shaira-va-token",
                     "Cookie": appmod.CONSOLE_COOKIE + "=" + appmod._console_cookie_value()}):
        assert appmod._present_console_key() == "shaira-va-token"


def test_rotation_still_invalidates_the_old_cookie(client, monkeypatch):
    c, appmod = client
    _sign_in_with_master(c)
    monkeypatch.setattr(appmod, "CONSOLE_SECRET", "rotated-secret")
    r = c.get("/api/console/next-actions", headers={"X-Console-Key": STALE})
    assert r.status_code == 401
