"""Rae sees every console page, as Glen does (Glen, 2026-10-16).

"Give Rae access to all pages in the console." Rae signs in as scope workspace:rae, and
rbac already makes her an OWNER. The workspace checks compared the scope to "admin" and
never asked rbac, so she was refused Glen's lanes (_can_access_owner), silently forced to
her own (_scoped_owner), and refused scoped routes (_auth). Shaira, a VA, stays narrowed.
"""
import importlib

import pytest


@pytest.fixture(scope="module")
def app():
    return importlib.import_module("app")


RAE = {"scope": "workspace:rae", "user_name": "rae"}
GLEN = {"scope": "workspace:glen", "user_name": "glen"}
SHAIRA = {"scope": "workspace:shaira", "user_name": "shaira"}
ADMIN = {"scope": "admin", "user_name": None}


def test_owner_scopes(app):
    assert app._is_owner_scope("admin")
    assert app._is_owner_scope("workspace:rae") and app._is_owner_scope("workspace:glen")
    for s in ("workspace:shaira", "workspace:newhire", "", None, "workspace:"):
        assert not app._is_owner_scope(s), s


@pytest.mark.parametrize("ctx", [RAE, GLEN, ADMIN])
def test_an_owner_reaches_every_lane(app, ctx):
    for owner in ("glen", "rae", "shaira"):
        assert app._can_access_owner(ctx, owner)


def test_shaira_reaches_only_her_own_lane(app):
    assert app._can_access_owner(SHAIRA, "shaira")
    assert not app._can_access_owner(SHAIRA, "glen") and not app._can_access_owner(SHAIRA, "rae")


def test_rae_gets_the_lane_she_asks_for_and_her_own_by_default(app):
    assert app._scoped_owner(RAE, "glen") == "glen"
    assert app._scoped_owner(RAE, "") == "rae"
    assert app._scoped_owner(ADMIN, "") == "glen"


def test_shaira_is_still_forced_to_her_own_lane(app):
    assert app._scoped_owner(SHAIRA, "glen") == "shaira"


def test_rae_passes_a_route_scoped_to_another_workspace(app, monkeypatch):
    import sqlite3

    def fake_connect(_path):
        cx = sqlite3.connect(":memory:")
        cx.executescript("""CREATE TABLE workspace_users(id INTEGER PRIMARY KEY, name TEXT, scope TEXT);
            CREATE TABLE access_tokens(token TEXT, user_id INTEGER, revoked_at TEXT, last_used_at TEXT);
            INSERT INTO workspace_users VALUES (1,'rae','workspace:rae'), (2,'shaira','workspace:shaira');
            INSERT INTO access_tokens VALUES ('tok-rae',1,NULL,NULL), ('tok-sh',2,NULL,NULL);""")
        return cx

    monkeypatch.setattr(app.db, "connect", fake_connect)
    with app.app.test_request_context(headers={"X-Console-Key": "tok-rae"}):
        ok, ctx, code = app._auth("workspace:shaira")
    assert ctx is not None and (ok, code) == (True, 200)
    with app.app.test_request_context(headers={"X-Console-Key": "tok-rae"}):
        ok, ctx, code = app._auth("admin")
    assert (ok, code) == (False, 403), "an owner never passes the literal admin scope"
    with app.app.test_request_context(headers={"X-Console-Key": "tok-sh"}):
        ok, ctx, code = app._auth("workspace:glen")
    assert (ok, code) == (False, 403)


def test_only_a_named_owner_scope_counts(app):
    for s in ("workspace:owner", "workspace:Rae", "workspace:rae ", "workspace:glen "):
        assert not app._is_owner_scope(s), s


def test_the_calendar_owners_check_asks_rbac(app):
    """Source check only: the calendar route needs a calendar database to run."""
    src = open(app.__file__).read()
    start = src.index("# Owners by scope: an owner (admin, or Rae's workspace:rae)")
    assert "if _is_owner_scope(ctx.get(\"scope\")):" in src[start:start + 300]


def test_an_unmapped_email_never_derives_an_owner(app):
    # "rae@newdomain.com" added to the allowlist without a map entry stays a VA scope.
    for e in ("rae@newdomain.com", "glen@example.org", "R.a.e@x.com"):
        scope, _name = app._console_owner_scope(e)
        assert not app._is_owner_scope(scope), (e, scope)
    assert app._is_owner_scope(app._console_owner_scope("suerae1111@gmail.com")[0])


def _post_as(app, monkeypatch, ctx, role):
    monkeypatch.setattr(app, "_auth", lambda *a, **k: (True, ctx, 200))
    with app.app.test_request_context(json={"role": role, "content": "hi"}):
        r = app.todo_messages_post(999999)
    return r[1] if isinstance(r, tuple) else 200


def test_the_message_guard_lets_each_person_post_only_as_themselves(app, monkeypatch):
    link_rae = {"scope": "workspace:rae", "user_name": "Rae"}   # sign-in-link session
    assert _post_as(app, monkeypatch, link_rae, "glen") == 403
    assert _post_as(app, monkeypatch, SHAIRA, "rae") == 403
    assert _post_as(app, monkeypatch, SHAIRA, "glen") == 403
    assert _post_as(app, monkeypatch, link_rae, "rae") != 403
