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
