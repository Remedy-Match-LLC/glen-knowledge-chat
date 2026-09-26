"""GET/PUT /api/portal/<token>/folds. Spec:
docs/superpowers/specs/2026-09-25-portal-folding-design.md"""
import sqlite3

import pytest

_RAE = "rae-owner-token-folds"
_VA = "va-token-folds"
SECRET = "test-secret"


@pytest.fixture
def client(monkeypatch, tmp_path):
    import app as appmod
    monkeypatch.setattr(appmod, "LOG_DB", str(tmp_path / "chat_log.db"))
    appmod._init_auth_tables()
    monkeypatch.setattr(appmod, "CONSOLE_SECRET", SECRET)
    monkeypatch.setenv("PORTAL_FOLDS_V2", "1")
    monkeypatch.delenv("PORTAL_FOLDS_V2_EMAILS", raising=False)
    appmod.app.config["TESTING"] = True
    return appmod.app.test_client(), appmod


def _seed_portal(appmod, email="brooke@example.com"):
    from dashboard import client_portal as cp
    cx = sqlite3.connect(appmod.LOG_DB)
    cp.init_client_portal_table(cx)
    token, _ = cp.upsert_portal(cx, email, "Brooke Webb", {"greeting": "Aloha."})
    cx.close()
    return token


def _seed_user(appmod, name, scope, token):
    appmod._init_workspace_schema()
    with appmod.db.connect(appmod.LOG_DB) as cx:
        cx.execute("INSERT INTO workspace_users (name, display_name, scope) VALUES (?,?,?) "
                   "ON CONFLICT(name) DO UPDATE SET scope=excluded.scope", (name, name, scope))
        uid = cx.execute("SELECT id FROM workspace_users WHERE name=?", (name,)).fetchone()[0]
        cx.execute("INSERT INTO access_tokens (token, user_id) VALUES (?,?)", (token, uid))
        cx.commit()
    return uid


def _rows(appmod):
    from dashboard import portal_folds as pf
    cx = sqlite3.connect(appmod.LOG_DB)
    pf.init_table(cx)            # a refused request never creates the table
    try:
        return {(r[0], r[1]): r[2] for r in cx.execute(
            "SELECT portal_email, viewer, state_json FROM portal_fold_state")}
    finally:
        cx.close()


def _put(c, tok, state, key=None):
    h = {"X-Console-Key": key} if key else {}
    return c.put(f"/api/portal/{tok}/folds", json={"state": state}, headers=h)


def _get(c, tok, key=None, of=None):
    h = {"X-Console-Key": key} if key else {}
    q = f"?of={of}" if of is not None else ""
    return c.get(f"/api/portal/{tok}/folds{q}", headers=h)


def test_client_roundtrip(client):
    c, appmod = client
    tok = _seed_portal(appmod)
    assert _put(c, tok, {"cards": {"scans-history": True}}).status_code == 200
    r = _get(c, tok)
    assert r.status_code == 200
    assert r.get_json()["viewer"] == "client"
    assert r.get_json()["state"]["cards"] == {"scans-history": True}


def test_staff_put_never_touches_client_row(client):
    c, appmod = client
    tok = _seed_portal(appmod)
    _put(c, tok, {"cards": {"a": True}})
    before = _rows(appmod)[("brooke@example.com", "client")]
    assert _put(c, tok, {"cards": {"a": False}}, key=SECRET).status_code == 200
    rows = _rows(appmod)
    assert rows[("brooke@example.com", "client")] == before
    assert ("brooke@example.com", "staff:master") in rows


def test_rae_gets_her_own_row_keyed_by_account_id(client):
    c, appmod = client
    tok = _seed_portal(appmod)
    uid = _seed_user(appmod, "rae", "workspace:rae", _RAE)
    assert _put(c, tok, {"cards": {"a": True}}, key=_RAE).status_code == 200
    rows = _rows(appmod)
    assert ("brooke@example.com", f"staff:user:{uid}") in rows
    assert not any(_RAE in v for (_, v) in rows)
    assert _get(c, tok, key=_RAE).get_json()["viewer"] == "staff"


def test_staff_reads_client_view_with_of(client):
    c, appmod = client
    tok = _seed_portal(appmod)
    _put(c, tok, {"cards": {"a": True}})
    _put(c, tok, {"cards": {"a": False}}, key=SECRET)
    assert _get(c, tok, key=SECRET, of="client").get_json()["state"]["cards"] == {"a": True}
    assert _get(c, tok, key=SECRET).get_json()["state"]["cards"] == {"a": False}


def test_client_cannot_reach_a_staff_row(client):
    c, appmod = client
    tok = _seed_portal(appmod)
    _put(c, tok, {"cards": {"a": False}}, key=SECRET)
    assert _get(c, tok).get_json()["state"]["cards"] == {}
    assert _get(c, tok, of="client").get_json()["state"]["cards"] == {}
    for bad in ("staff", "staff:master", "x"):
        assert _get(c, tok, of=bad).status_code == 400
        assert _get(c, tok, key=SECRET, of=bad).status_code == 400


def test_va_and_unknown_keys_are_refused(client):
    c, appmod = client
    tok = _seed_portal(appmod)
    _seed_user(appmod, "shaira", "workspace:shaira", _VA)
    for key in (_VA, "not-a-key"):
        assert _put(c, tok, {"cards": {"a": True}}, key=key).status_code == 403
        assert _get(c, tok, key=key).status_code == 403
    assert _rows(appmod) == {}


def test_unknown_token_404(client):
    c, _ = client
    assert _get(c, "no-such-token").status_code == 404
    assert _put(c, "no-such-token", {"cards": {}}).status_code == 404


def test_setting_off_404(client, monkeypatch):
    c, appmod = client
    tok = _seed_portal(appmod)
    monkeypatch.delenv("PORTAL_FOLDS_V2")
    assert _get(c, tok).status_code == 404
    assert _put(c, tok, {"cards": {}}).status_code == 404


def test_email_allowlist_enables_one_portal(client, monkeypatch):
    c, appmod = client
    on = _seed_portal(appmod, "fold-test@example.com")
    off = _seed_portal(appmod, "brooke@example.com")
    monkeypatch.delenv("PORTAL_FOLDS_V2")
    monkeypatch.setenv("PORTAL_FOLDS_V2_EMAILS", " Fold-Test@example.com ,other@x.com")
    assert _get(c, on).status_code == 200
    assert _get(c, off).status_code == 404


def test_bad_body_400_and_too_large_413(client):
    c, appmod = client
    tok = _seed_portal(appmod)
    assert c.put(f"/api/portal/{tok}/folds", data="nope",
                 content_type="application/json").status_code == 400
    assert c.put(f"/api/portal/{tok}/folds", json={"state": [1]}).status_code == 400
    many = {("k" * 110) + str(i): True for i in range(500)}
    huge = {"cards": many, "before_fold_all": {d: many for d in ("a", "b", "c")}}
    assert _put(c, tok, huge).status_code == 413


def test_reissued_link_keeps_folds(client):
    c, appmod = client
    from dashboard import client_portal as cp
    tok = _seed_portal(appmod)
    _put(c, tok, {"cards": {"a": True}})
    cx = sqlite3.connect(appmod.LOG_DB)
    new_tok = cp.reissue_token(cx, "brooke@example.com")
    cx.close()
    assert new_tok and new_tok != tok
    assert _get(c, new_tok).get_json()["state"]["cards"] == {"a": True}
