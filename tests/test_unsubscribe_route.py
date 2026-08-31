"""The /email/unsubscribe route.

GET must stay side-effect free. Mail scanners and link prefetchers follow GET
links, so a mutating GET would opt out people who never clicked. POST does the
work, and only with a signature that matches the address it claims.

Skips when app is not importable (the secretless CI run), matching the other
route tests in this suite. The signature gate itself is covered without the app
in tests/test_unsubscribe_token.py, so the security property is not skip-gated.
"""
import importlib
import sys
from pathlib import Path

import pytest

from dashboard import db, email_suppression as es, unsubscribe


def _client(tmp_path, monkeypatch):
    scratch = str(tmp_path / "log.db")
    monkeypatch.setattr(unsubscribe, "_SECRET", "test-secret-abc", raising=False)
    repo = Path(__file__).resolve().parent.parent
    if str(repo) not in sys.path:
        sys.path.insert(0, str(repo))
    try:
        import app as appmod
        importlib.reload(appmod)
    except Exception as e:  # noqa: BLE001 — matches the other route tests
        pytest.skip(f"app not importable: {e}")
    # Point the route at a scratch DB so a test never writes the real one.
    monkeypatch.setattr(appmod, "LOG_DB", scratch, raising=False)
    appmod.app.config["TESTING"] = True
    return appmod.app.test_client(), scratch


def _sig(email, scope="global"):
    return unsubscribe.sign(email, scope)


def _suppressed(path, email):
    with db.connect(path) as cx:
        es.init_table(cx)
        return es.is_suppressed(cx, email)


def test_get_shows_a_confirmation_and_does_not_opt_out(tmp_path, monkeypatch):
    c, scratch = _client(tmp_path, monkeypatch)
    e = "getter@example.com"
    r = c.get(f"/email/unsubscribe?e={e}&scope=global&s={_sig(e)}")
    assert r.status_code == 200
    assert b"unsubscribe" in r.data.lower()
    assert _suppressed(scratch, e) is False


def test_post_with_valid_signature_opts_out(tmp_path, monkeypatch):
    c, scratch = _client(tmp_path, monkeypatch)
    e = "poster@example.com"
    r = c.post("/email/unsubscribe", data={"e": e, "scope": "global", "s": _sig(e)})
    assert r.status_code == 200
    assert _suppressed(scratch, e) is True


def test_post_with_a_bad_signature_is_rejected(tmp_path, monkeypatch):
    c, scratch = _client(tmp_path, monkeypatch)
    e = "victim@example.com"
    r = c.post("/email/unsubscribe",
               data={"e": e, "scope": "global", "s": "not-a-signature"})
    assert r.status_code == 400
    assert _suppressed(scratch, e) is False


def test_a_signature_for_one_address_cannot_opt_out_another(tmp_path, monkeypatch):
    c, scratch = _client(tmp_path, monkeypatch)
    e = "other@example.com"
    r = c.post("/email/unsubscribe",
               data={"e": e, "scope": "global", "s": _sig("mine@example.com")})
    assert r.status_code == 400
    assert _suppressed(scratch, e) is False


def test_a_scoped_signature_records_the_scope(tmp_path, monkeypatch):
    c, scratch = _client(tmp_path, monkeypatch)
    e = "scoped@example.com"
    r = c.post("/email/unsubscribe",
               data={"e": e, "scope": "weekly-live", "s": _sig(e, "weekly-live")})
    assert r.status_code == 200
    with db.connect(scratch) as cx:
        row = cx.execute("SELECT source FROM email_suppression WHERE email=?",
                         (e,)).fetchone()
    assert "weekly-live" in row[0]
