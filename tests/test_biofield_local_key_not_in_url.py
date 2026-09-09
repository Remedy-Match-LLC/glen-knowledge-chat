"""The console key must not stay in the local Biofield server's URL.

`/console/biofield-intake` redirects the browser to 127.0.0.1:8011 with
`?key=<CONSOLE_SECRET>` appended, and Flask's dev server logs full request
lines. So every visit wrote Glen's LIVE console secret into
~/Library/Logs/glen/com.glen.biofield-local-server.err, where it was found in
plaintext on 2026-09-08.

The cookie already existed: `_console_gate` accepts `rm_biofield_key` and
`_console_cookie` sets it. What was missing is that nothing ever took the key
back OUT of the URL, so it was re-logged on every navigation, and it sat in
browser history and in any Referer the page sent.

Two guards here. The key is stripped from the URL once cookied, and the request
log redacts it either way, because the very first request still carries it.
"""

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SECRET = "test-console-secret-value"


def _mod():
    spec = importlib.util.spec_from_file_location(
        "biofield_local_app", ROOT / "biofield_local_app.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


bl = _mod()


# ── The log must never carry the key, even on the first request ──────────────

def test_the_request_log_redacts_the_key():
    out = bl._redact_key_in_path(f"GET /clinical-tags?key={SECRET}&q=x HTTP/1.1")
    assert SECRET not in out, out
    assert "q=x" in out, "redaction ate an unrelated parameter"


def test_redaction_leaves_a_clean_path_alone():
    p = "GET /clinical-tags?q=hello HTTP/1.1"
    assert bl._redact_key_in_path(p) == p


# ── The key leaves the URL after one request ─────────────────────────────────

@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("CONSOLE_SECRET", SECRET)
    app = bl.create_app(db_path=str(tmp_path / "t.db"))
    app.config.update(TESTING=True)
    return app.test_client()


def test_a_key_in_the_url_redirects_to_a_url_without_it(client):
    r = client.get(f"/?key={SECRET}")
    assert r.status_code in (301, 302, 303, 307, 308), r.status_code
    assert "key=" not in r.headers.get("Location", ""), r.headers.get("Location")
    assert "rm_biofield_key" in r.headers.get("Set-Cookie", ""), r.headers


def test_the_cookie_alone_is_accepted_and_does_not_redirect(client):
    client.set_cookie("rm_biofield_key", SECRET)
    r = client.get("/")
    assert r.status_code == 200, r.status_code


def test_a_wrong_key_is_still_refused(client):
    assert client.get("/?key=nope").status_code == 401


def test_no_redirect_loop_once_the_cookie_is_set(client):
    """A redirect that keeps firing with the cookie present would hang a browser."""
    client.set_cookie("rm_biofield_key", SECRET)
    r = client.get(f"/?key={SECRET}")
    assert r.status_code in (200, 302), r.status_code
    if r.status_code == 302:
        assert "key=" not in r.headers.get("Location", "")
        assert client.get(r.headers["Location"]).status_code == 200


# ── Liveness, because a KeepAlive daemon has no meaningful exit code ─────────

def test_the_heartbeat_records_a_time_and_the_port(tmp_path):
    """job-health judges jobs by artifact freshness. A server that only exits
    when killed keeps a stale non-zero exit forever, so it reported red for days
    while serving perfectly. A heartbeat is the honest signal."""
    hb = tmp_path / "hb.json"
    bl._write_heartbeat(str(hb), 8011)
    import json
    d = json.loads(hb.read_text())
    assert d["port"] == 8011, d
    assert d["at"], d
    assert d["pid"] > 0, d


def test_the_heartbeat_never_raises_into_the_server(tmp_path):
    """A monitoring write must not be able to take down the thing it monitors."""
    bl._write_heartbeat(str(tmp_path / "no" / "such" / "dir" / "hb.json"), 8011)


# ── The redirect must not break programmatic callers ────────────────────────

# These probe a path that deliberately does NOT exist, so routing always gives a
# deterministic 404 and the ONLY thing that can produce a 302 is the gate. The
# first version hit real routes and measured their behaviour as well as the
# gate's, which passed locally and failed in CI for reasons that were never
# about the gate.
_PROBE = "/api/__gate_probe_does_not_exist__"


def test_an_api_get_with_the_key_is_not_redirected_by_the_gate(client):
    """Scripts GET /api/... ?key= and neither follow redirects nor send cookies.
    Redirecting them would break real callers to fix a logging problem they do
    not have."""
    r = client.get(f"{_PROBE}?key={SECRET}")
    assert r.status_code != 302, (
        f"the gate redirected an API GET to {r.headers.get('Location')!r}")
    assert r.status_code == 404, (
        f"expected the probe path to 404 past the gate, got {r.status_code}")


def test_a_post_with_the_key_is_not_redirected_by_the_gate(client):
    """A 302 on a POST drops the body."""
    r = client.post(f"{_PROBE}?key={SECRET}", json={"a": 1})
    assert r.status_code != 302, (
        f"the gate redirected a POST to {r.headers.get('Location')!r}, losing its body")
    assert r.status_code in (404, 405), (
        f"expected the probe path to 404/405 past the gate, got {r.status_code}")


def test_a_non_api_get_still_gets_the_strip_redirect(client):
    """The counterpart: browser navigation IS the case the redirect exists for."""
    r = client.get(f"/?key={SECRET}")
    assert r.status_code == 302, r.status_code
