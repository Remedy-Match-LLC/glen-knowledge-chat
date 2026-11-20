"""One-time passes for the local tools over the private link (Glen, 2026-11-19).

The hosted console mints a pass for an owner token. The local tool on Glen's Mac
redeems it once for its own session cookie. Every check must fail closed, and the
private-link listener must accept nothing else: not the master key, and nothing at
all when the pass secret is unset.
"""
import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dashboard import local_tool_pass as ltp  # noqa: E402

SECRET = "p" * 40
MASTER = "test-master-console-secret"
TAILNET = "100.85.193.57"
BASE = f"http://{TAILNET}:8011"


# ── The pass itself ───────────────────────────────────────────────────────────

def test_a_minted_pass_redeems_once_for_its_owner():
    g = ltp.ReplayGuard(not_before=0)
    tok = ltp.mint_pass("owner-a", "biofield-intake", now=1000, secret=SECRET)
    assert ltp.redeem_pass(tok, "biofield-intake", g, now=1001, secret=SECRET) == "owner-a"


def test_a_replayed_pass_is_refused():
    g = ltp.ReplayGuard(not_before=0)
    tok = ltp.mint_pass("owner-a", "biofield-intake", now=1000, secret=SECRET)
    ltp.redeem_pass(tok, "biofield-intake", g, now=1001, secret=SECRET)
    with pytest.raises(ltp.PassError, match="already used"):
        ltp.redeem_pass(tok, "biofield-intake", g, now=1002, secret=SECRET)


def test_an_expired_pass_is_refused():
    g = ltp.ReplayGuard(not_before=0)
    tok = ltp.mint_pass("owner-a", "biofield-intake", now=1000, secret=SECRET)
    assert ltp.redeem_pass(tok, "biofield-intake", ltp.ReplayGuard(not_before=0),
                           now=1000 + ltp.PASS_TTL_S, secret=SECRET) == "owner-a"
    with pytest.raises(ltp.PassError, match="expired"):
        ltp.redeem_pass(tok, "biofield-intake", g, now=1001 + ltp.PASS_TTL_S, secret=SECRET)


def test_the_pass_lasts_two_minutes():
    assert ltp.PASS_TTL_S == 120


def test_a_pass_for_the_other_tool_is_refused_and_not_burned():
    g = ltp.ReplayGuard(not_before=0)
    tok = ltp.mint_pass("owner-a", "clinical-tags", now=1000, secret=SECRET)
    with pytest.raises(ltp.PassError, match="wrong tool"):
        ltp.redeem_pass(tok, "biofield-intake", g, now=1001, secret=SECRET)
    with pytest.raises(ltp.PassError, match="wrong tool"):
        ltp.redeem_pass(tok, None, g, now=1001, secret=SECRET)
    assert ltp.redeem_pass(tok, "clinical-tags", g, now=1001, secret=SECRET) == "owner-a"


def test_a_bad_signature_is_refused():
    tok = ltp.mint_pass("owner-a", "biofield-intake", now=1000, secret=SECRET)
    body, _, sig = tok.partition(".")
    for bad in (body + "." + sig[:-2] + ("AA" if sig[-2:] != "AA" else "BB"),
                body + ".", "." + sig, "garbage", ""):
        with pytest.raises(ltp.PassError):
            ltp.redeem_pass(bad, "biofield-intake", ltp.ReplayGuard(not_before=0), now=1001, secret=SECRET)
    with pytest.raises(ltp.PassError, match="bad signature"):
        ltp.redeem_pass(tok, "biofield-intake", ltp.ReplayGuard(not_before=0), now=1001,
                        secret="q" * 40)


def test_a_forged_payload_with_a_reused_signature_is_refused():
    import base64
    import json
    tok = ltp.mint_pass("owner-a", "clinical-tags", now=1000, secret=SECRET)
    _, _, sig = tok.partition(".")
    forged = base64.urlsafe_b64encode(json.dumps(
        {"v": 1, "owner": "x", "tool": "biofield-intake", "iat": 1000, "exp": 9999,
         "jti": "j"}).encode()).rstrip(b"=").decode()
    with pytest.raises(ltp.PassError, match="bad signature"):
        ltp.redeem_pass(forged + "." + sig, "biofield-intake", ltp.ReplayGuard(not_before=0),
                        now=1001, secret=SECRET)


def test_no_secret_mints_nothing_and_verifies_nothing(monkeypatch):
    monkeypatch.delenv(ltp.SECRET_ENV, raising=False)
    assert ltp.pass_secret() == ""
    with pytest.raises(ltp.PassError):
        ltp.mint_pass("owner-a", "biofield-intake")
    tok = ltp.mint_pass("owner-a", "biofield-intake", secret=SECRET)
    with pytest.raises(ltp.PassError, match="no secret"):
        ltp.redeem_pass(tok, "biofield-intake", ltp.ReplayGuard(not_before=0))
    assert ltp.session_owner(ltp.mint_session("owner-a", secret=SECRET)) is None


def test_a_short_secret_counts_as_no_secret(monkeypatch):
    monkeypatch.setenv(ltp.SECRET_ENV, "short")
    assert ltp.pass_secret() == ""
    monkeypatch.setenv(ltp.SECRET_ENV, SECRET)
    assert ltp.pass_secret() == SECRET


def test_a_pass_is_not_a_session_and_a_session_is_not_a_pass():
    tok = ltp.mint_pass("owner-a", "biofield-intake", now=1000, secret=SECRET)
    assert ltp.session_owner(tok, now=1001, secret=SECRET) is None
    sess = ltp.mint_session("owner-a", now=1000, secret=SECRET)
    with pytest.raises(ltp.PassError):
        ltp.redeem_pass(sess, "biofield-intake", ltp.ReplayGuard(not_before=0), now=1001, secret=SECRET)
    assert ltp.session_owner(sess, now=1001, secret=SECRET) == "owner-a"
    assert ltp.session_owner(sess, now=1001 + ltp.SESSION_TTL_S, secret=SECRET) is None


@pytest.mark.parametrize("host", ["0.0.0.0", "127.0.0.1", "192.168.1.20", "10.0.0.5",
                                  "mac-studio", "", "::", "100.128.0.1"])
def test_the_listener_refuses_anything_but_a_tailscale_address(host):
    with pytest.raises(ValueError):
        ltp.check_tailnet_host(host)


def test_the_listener_accepts_the_tailscale_address():
    assert ltp.check_tailnet_host(TAILNET) == TAILNET


# ── The local tool's gate on the private-link listener ────────────────────────

def _mod():
    spec = importlib.util.spec_from_file_location(
        "biofield_local_app", ROOT / "biofield_local_app.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


bl = _mod()


@pytest.fixture()
def local(tmp_path, monkeypatch):
    monkeypatch.setenv("CONSOLE_SECRET", MASTER)
    monkeypatch.setenv(ltp.SECRET_ENV, SECRET)
    app = bl.create_app(db_path=str(tmp_path / "t.db"), e4l_db=str(tmp_path / "e4l.db"))
    app.config.update(TESTING=True)
    return app


def _tail(client, path, **kw):
    env = {bl._TAILNET_ENVIRON: True, bl._TAILNET_HOST_ENVIRON: TAILNET}
    env.update(kw.pop("environ_overrides", {}))
    return client.get(path, base_url=kw.pop("base_url", BASE), environ_overrides=env, **kw)


def _session_cookie(resp):
    for h in resp.headers.getlist("Set-Cookie"):
        if h.startswith(ltp.SESSION_COOKIE + "="):
            return h
    return None


def test_a_valid_pass_sets_a_strict_httponly_session_and_cleans_the_url(local):
    tok = ltp.mint_pass("owner-a", "clinical-tags", secret=SECRET)
    with local.test_client() as c:
        r = _tail(c, f"/clinical-tags?pass={tok}&back=%2Fconsole")
        assert r.status_code == 200, r.get_data(as_text=True)[:300]
        ck = _session_cookie(r)
        assert ck and "HttpOnly" in ck and "SameSite=Strict" in ck
        body = r.get_data(as_text=True)
        assert tok not in body and "/clinical-tags?back=%2Fconsole" in body
        assert r.headers.get("Referrer-Policy") == "no-referrer"
        # The cookie now carries the session, with no pass.
        r2 = _tail(c, "/clinical-tags")
        assert r2.status_code not in (401, 421)
        assert "Open this from the console" not in r2.get_data(as_text=True)


def test_a_replayed_pass_is_refused_at_the_local_tool(local):
    tok = ltp.mint_pass("owner-a", "clinical-tags", secret=SECRET)
    with local.test_client() as c:
        assert _tail(c, f"/clinical-tags?pass={tok}").status_code == 200
    with local.test_client() as c2:     # a second browser with no cookie
        r = _tail(c2, f"/clinical-tags?pass={tok}")
        assert r.status_code == 401 and _session_cookie(r) is None


def test_a_pass_for_the_other_tool_is_refused_at_the_local_tool(local):
    tok = ltp.mint_pass("owner-a", "clinical-tags", secret=SECRET)
    with local.test_client() as c:
        r = _tail(c, f"/?pass={tok}")
        assert r.status_code == 401 and _session_cookie(r) is None
        r = _tail(c, f"/api/suggest?pass={tok}")
        assert r.status_code == 401 and _session_cookie(r) is None


def test_an_expired_pass_is_refused_at_the_local_tool(local):
    import time
    tok = ltp.mint_pass("owner-a", "biofield-intake", now=int(time.time()) - 600,
                        secret=SECRET)
    with local.test_client() as c:
        r = _tail(c, f"/?pass={tok}")
        assert r.status_code == 401 and _session_cookie(r) is None


def test_the_master_key_does_not_open_the_private_link(local):
    with local.test_client() as c:
        r = _tail(c, f"/clinical-tags?key={MASTER}")
        assert r.status_code == 401 and "rm_biofield_key" not in str(r.headers)
        r = _tail(c, "/clinical-tags", headers={"X-Console-Key": MASTER})
        assert r.status_code == 401
        c.set_cookie("rm_biofield_key", MASTER, domain=TAILNET)
        assert _tail(c, "/clinical-tags").status_code == 401


def test_no_pass_secret_closes_the_private_link(local, monkeypatch):
    sess = ltp.mint_session("owner-a", secret=SECRET)
    tok = ltp.mint_pass("owner-a", "clinical-tags", secret=SECRET)
    monkeypatch.delenv(ltp.SECRET_ENV)
    monkeypatch.delenv("CONSOLE_SECRET")
    with local.test_client() as c:
        assert _tail(c, f"/clinical-tags?pass={tok}").status_code == 401
        c.set_cookie(ltp.SESSION_COOKIE, sess, domain=TAILNET)
        assert _tail(c, "/clinical-tags").status_code == 401
        assert _tail(c, "/").status_code == 401


def test_the_gate_itself_refuses_with_no_secret_before_checking_anything(local, monkeypatch):
    # The gate must not lean on the verifiers alone to fail closed.
    monkeypatch.delenv(ltp.SECRET_ENV)
    monkeypatch.setattr(ltp, "redeem_pass", lambda *a, **k: "owner-a")
    monkeypatch.setattr(ltp, "session_owner", lambda *a, **k: "owner-a")
    with local.test_client() as c:
        assert _tail(c, "/clinical-tags?pass=anything").status_code == 401
        assert _tail(c, "/clinical-tags").status_code == 401


def test_a_forged_session_cookie_is_refused(local):
    with local.test_client() as c:
        c.set_cookie(ltp.SESSION_COOKIE, ltp.mint_session("x", secret="z" * 40),
                     domain=TAILNET)
        assert _tail(c, "/clinical-tags").status_code == 401


def test_another_host_name_is_refused_on_the_private_link(local):
    tok = ltp.mint_pass("owner-a", "clinical-tags", secret=SECRET)
    with local.test_client() as c:
        r = _tail(c, f"/clinical-tags?pass={tok}", base_url="http://rebind.example:8011")
        assert r.status_code == 421 and _session_cookie(r) is None


def test_the_loopback_listener_is_unchanged(local):
    with local.test_client() as c:
        r = c.get(f"/clinical-tags?key={MASTER}")
        assert r.status_code in (301, 302, 303, 307, 308)
        assert "rm_biofield_key" in str(r.headers)
        tok = ltp.mint_pass("owner-a", "clinical-tags", secret=SECRET)
    with local.test_client() as c:
        # A pass is for the private link only; loopback still wants the console key.
        assert c.get(f"/clinical-tags?pass={tok}").status_code == 401


def test_the_private_link_never_hands_out_the_long_lived_deepgram_key(tmp_path, monkeypatch):
    monkeypatch.setenv(ltp.SECRET_ENV, SECRET)
    monkeypatch.delenv("CONSOLE_SECRET", raising=False)
    monkeypatch.setenv("DEEPGRAM_API_KEY", "dg-long-lived")
    app = bl.create_app(db_path=str(tmp_path / "t.db"), e4l_db=str(tmp_path / "e4l.db"),
                        deepgram_token=lambda: "dg-long-lived")
    app.config.update(TESTING=True)
    with app.test_client() as c:
        c.set_cookie(ltp.SESSION_COOKIE, ltp.mint_session("owner-a", secret=SECRET),
                     domain=TAILNET)
        r = _tail(c, "/api/deepgram-token")
        assert "dg-long-lived" not in r.get_data(as_text=True)
        assert c.get("/api/deepgram-token").get_json()["key"] == "dg-long-lived"


def test_the_request_log_redacts_a_pass():
    out = bl._redact_key_in_path("GET /clinical-tags?pass=abc.def&q=x HTTP/1.1")
    assert "abc.def" not in out and "q=x" in out


def test_tailnet_url_drops_any_query_from_back():
    from dashboard.console_launcher import tailnet_tool_url
    url = tailnet_tool_url("NO_SUCH_ENV_FOR_TEST", "http://100.85.193.57:8011", "/",
                           "PASS", back="/console/sell?key=OWNERTOKEN123#x")
    assert "OWNERTOKEN123" not in url
    assert url.endswith("back=%2Fconsole%2Fsell")


def test_a_pass_issued_before_a_restart_is_refused():
    """The replay record is in memory. A fresh guard (a restarted server) must
    refuse a pass minted before it started, or a used pass would work again."""
    tok = ltp.mint_pass("rae", "biofield-intake", now=1000, secret=SECRET)
    ltp.redeem_pass(tok, "biofield-intake", ltp.ReplayGuard(not_before=1000),
                    now=1001, secret=SECRET)
    with pytest.raises(ltp.PassError):
        ltp.redeem_pass(tok, "biofield-intake", ltp.ReplayGuard(not_before=1001),
                        now=1002, secret=SECRET)


def test_tailnet_bind_retries_after_werkzeug_system_exit(monkeypatch):
    import biofield_local_app as bla
    import werkzeug.serving as ws
    calls = []

    class _Stop(Exception):
        pass

    class _Srv:
        def serve_forever(self):
            calls.append("served")
            raise _Stop

    def fake_make_server(*a, **k):
        calls.append("bind")
        if len(calls) == 1:
            raise SystemExit(1)
        return _Srv()
    monkeypatch.setattr(ws, "make_server", fake_make_server)
    monkeypatch.setattr(bla.time, "sleep", lambda s: None)
    started = []
    import threading
    monkeypatch.setattr(threading, "Thread",
                        lambda target, daemon: type("T", (), {"start": lambda self: started.append(target)})())
    bla._serve_tailnet(object(), "100.85.193.57", 8011, retry_s=0)
    with pytest.raises(_Stop):
        started[0]()
    assert calls[:3] == ["bind", "bind", "served"]


def _signed(label, payload):
    import json
    body = ltp._b64(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode())
    return body + "." + ltp._sign(label, body, SECRET)


@pytest.mark.parametrize("change", [
    {"iat": 1100, "exp": 1150},          # dated in the future
    {"iat": 1000, "exp": 1000 + 10000},  # lifetime longer than PASS_TTL_S
    {"v": 2},                            # unknown version
])
def test_a_correctly_signed_but_out_of_bounds_pass_is_refused(change):
    payload = {"v": 1, "owner": "owner-a", "tool": "biofield-intake", "iat": 1000,
               "exp": 1000 + ltp.PASS_TTL_S, "jti": "j1"}
    good = _signed(ltp._PASS_LABEL, payload)
    assert ltp.redeem_pass(good, "biofield-intake", ltp.ReplayGuard(not_before=0),
                           now=1001, secret=SECRET) == "owner-a"
    payload.update(change)
    with pytest.raises(ltp.PassError):
        ltp.redeem_pass(_signed(ltp._PASS_LABEL, payload), "biofield-intake",
                        ltp.ReplayGuard(not_before=0), now=1001, secret=SECRET)


def test_a_session_with_an_unknown_version_is_refused():
    ok = _signed(ltp._SESSION_LABEL, {"v": 1, "owner": "owner-a", "exp": 5000})
    assert ltp.session_owner(ok, now=1000, secret=SECRET) == "owner-a"
    bad = _signed(ltp._SESSION_LABEL, {"v": 2, "owner": "owner-a", "exp": 5000})
    assert ltp.session_owner(bad, now=1000, secret=SECRET) is None


def test_a_pass_on_a_post_is_refused_and_sets_no_session(local):
    tok = ltp.mint_pass("owner-a", "clinical-tags", secret=SECRET)
    env = {bl._TAILNET_ENVIRON: True, bl._TAILNET_HOST_ENVIRON: TAILNET}
    with local.test_client() as c:
        r = c.post(f"/clinical-tags?pass={tok}", base_url=BASE, environ_overrides=env)
        assert r.status_code == 405 and _session_cookie(r) is None
