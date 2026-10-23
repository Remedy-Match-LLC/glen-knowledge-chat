"""Voice journaling and dictation need a signed-in visitor (Glen, 2026-10-22).

"Signed in" means proven by an emailed link: the amg_auth session from
/auth/magic-link/verify, or the rm_portal_session from /portal/login-verify while
CLIENT_LOGIN_ENABLED is on. An email typed into a form plus a Terms tick (is_member)
does not count. A refused request gets 401 with a sign-in link, BEFORE any provider
call or upload handling.

Every provider below is replaced by a function that fails the test if it is reached.
"""
import importlib
import io
import json
import secrets
import shutil
import sqlite3
import subprocess
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

SECRET = "ci-fake-console-secret"
EMAIL = "Visitor@Example.com"
ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def appmod(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CONSOLE_SECRET", SECRET)
    monkeypatch.setenv("CLIENT_LOGIN_ENABLED", "1")
    import app as a
    importlib.reload(a)
    a.app.config["TESTING"] = True
    a._init_auth_tables()
    import journal_blueprint as jb
    monkeypatch.setattr(jb, "LOG_DB", a.LOG_DB)
    return a


def _boom(*a, **k):
    raise AssertionError("a provider was reached without sign-in")


@pytest.fixture
def providers_explode(monkeypatch):
    import journal_blueprint as jb
    for name in ("_whisper_transcribe", "_haiku_analyze", "_embed_ada002",
                 "_pinecone_index", "_haiku_affirmations"):
        monkeypatch.setattr(jb, name, _boom)
    # The upload must not be written to disk either.
    monkeypatch.setattr(tempfile, "NamedTemporaryFile", _boom)


@pytest.fixture
def providers_ok(monkeypatch):
    import journal_blueprint as jb
    monkeypatch.setattr(jb, "_whisper_transcribe", lambda p: {"text": "I feel calm today", "words": []})
    monkeypatch.setattr(jb, "_haiku_analyze", lambda t, lex: {
        "emotions": {"Calmness": 0.8}, "elements": {"Water": 0.6}, "treasures": {"Jing": 0.5}})
    monkeypatch.setattr(jb, "_embed_ada002", lambda t: [0.1, 0.2])
    monkeypatch.setattr(jb, "compare_haiku_to_mapper", lambda e, el: None)

    class _Idx:
        def query(self, **k):
            class R:
                matches = []
            return R()
    monkeypatch.setattr(jb, "_pinecone_index", lambda: _Idx())
    monkeypatch.setattr(jb, "_haiku_affirmations", lambda *a: ["I am calm."])


def _audio():
    return {"audio": (io.BytesIO(b"RIFFfake"), "clip.webm"), "duration_seconds": "4"}


ROUTES = [
    ("/transcribe", "multipart"),
    ("/journal/analyze", "multipart"),
    ("/journal/match", "json"),
    ("/journal/affirmations", "json"),
]

JSON_BODY = {
    "/journal/match": {"transcript": "I feel calm", "dominant_element": "Water"},
    "/journal/affirmations": {"remedies": [{"id": "r1", "metadata": {"name": "X"}}],
                              "transcript": "I feel calm"},
}


def _post(c, path, kind, **kw):
    if kind == "multipart":
        return c.post(path, data=_audio(), content_type="multipart/form-data", **kw)
    return c.post(path, json=JSON_BODY[path.split("?")[0]], **kw)


def _amg_client(a, email=EMAIL):
    """A browser that clicked a real emailed magic link (/auth/magic-link/verify)."""
    tok = secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc)
    with sqlite3.connect(a.LOG_DB) as cx:
        cx.execute(
            "INSERT INTO auth_tokens (token_hash, email, purpose, created_at, expires_at) "
            "VALUES (?,?,?,?,?)",
            (a._hash_token(tok), email.lower(), "magic_link", now.isoformat(),
             (now + timedelta(minutes=60)).isoformat()))
        cx.commit()
    c = a.app.test_client()
    r = c.post("/auth/magic-link/verify", data={"token": tok})
    assert "amg_auth=" in r.headers.get("Set-Cookie", ""), "fixture did not sign in"
    return c


def _portal_client(a, email=EMAIL):
    """A browser holding a client portal session (what /portal/login-verify sets)."""
    from dashboard import portal_identity as pi
    with sqlite3.connect(a.LOG_DB) as cx:
        pi._ensure_people_table(cx)
        pid = pi._get_or_create_person(cx, email.lower(), "Visitor")
        pid = pid[0] if isinstance(pid, tuple) else pid
        sess = pi.create_client_session(cx, pid, email.lower())
    c = a.app.test_client()
    c.set_cookie("rm_portal_session", sess)
    return c


def _member_only_client(a):
    """Typed an email and ticked Terms: is_member() is True, but nothing was emailed."""
    import begin_funnel
    sid = "sess-member-only"
    with sqlite3.connect(a.LOG_DB) as cx:
        begin_funnel.init_journey_tables(cx)
        begin_funnel.record_unlock(cx, session_id=sid, trigger="tos", email=EMAIL.lower(),
                                   tos=True, tos_version="test")
        cx.commit()
    assert a.is_member(sid, EMAIL.lower()), "fixture did not make a Terms member"
    c = a.app.test_client()
    c.set_cookie("amg_session", sid)
    return c


# ── refused ──────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("path,kind", ROUTES)
def test_anonymous_is_refused_before_any_provider(appmod, providers_explode, path, kind):
    r = _post(appmod.app.test_client(), path, kind)
    assert r.status_code == 401
    d = r.get_json()
    assert d["ok"] is False
    assert d["sign_in"] == "/auth/magic-link/request"
    assert d["error"] == "Please sign in to use voice. We will email you a link."


@pytest.mark.parametrize("path,kind", ROUTES)
def test_a_terms_member_with_a_typed_email_is_refused(appmod, providers_explode, path, kind):
    c = _member_only_client(appmod)
    data_email = {"email": EMAIL}
    if kind == "multipart":
        body = dict(_audio(), **data_email)
        r = c.post(path, data=body, content_type="multipart/form-data")
    else:
        r = c.post(path, json=dict(JSON_BODY[path], **data_email))
    assert r.status_code == 401


@pytest.mark.parametrize("path,kind", ROUTES)
def test_a_portal_session_does_not_count_while_client_login_is_off(
        appmod, providers_explode, monkeypatch, path, kind):
    c = _portal_client(appmod)
    monkeypatch.setenv("CLIENT_LOGIN_ENABLED", "")
    assert _post(c, path, kind).status_code == 401


@pytest.mark.parametrize("path,kind", ROUTES)
def test_a_forged_session_cookie_is_refused(appmod, providers_explode, path, kind):
    c = appmod.app.test_client()
    c.set_cookie("amg_auth", "not-a-real-session")
    c.set_cookie("rm_portal_session", "not-a-real-session")
    assert _post(c, path, kind).status_code == 401


@pytest.mark.parametrize("path,kind", ROUTES)
def test_a_wrong_console_key_is_refused(appmod, providers_explode, path, kind):
    c = appmod.app.test_client()
    assert _post(c, path, kind, headers={"X-Console-Key": "guessed-key"}).status_code == 401
    assert _post(c, path + "?key=guessed-key", kind).status_code == 401


def test_no_console_secret_does_not_open_the_staff_pass(appmod, providers_explode, monkeypatch):
    # An empty key against an empty secret is the one way compare_digest says yes,
    # and a token check that says yes to anything. Only the CONSOLE_SECRET guard stops it.
    monkeypatch.setattr(appmod, "CONSOLE_SECRET", "")
    monkeypatch.setattr(appmod, "_present_console_key", lambda *a, **k: "x")
    monkeypatch.setattr(appmod, "_owner_token_ok", lambda k: True)
    r = _post(appmod.app.test_client(), "/transcribe", "multipart")
    assert r.status_code == 401


def test_an_unwired_blueprint_refuses(providers_explode):
    """A bare app with no gate registered must refuse, never open."""
    import journal_blueprint as jb
    from flask import Flask
    bare = Flask(__name__)
    bare.register_blueprint(jb.journal_bp)
    r = bare.test_client().post("/journal/analyze", data=_audio(),
                                content_type="multipart/form-data")
    assert r.status_code == 401


# ── accepted ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("make", [_amg_client, _portal_client], ids=["amg_auth", "portal"])
@pytest.mark.parametrize("path,kind", ROUTES)
def test_signed_in_by_an_emailed_link_is_accepted(appmod, providers_ok, make, path, kind):
    r = _post(make(appmod), path, kind)
    assert r.status_code == 200, r.get_data(as_text=True)[:300]


@pytest.mark.parametrize("make", [_amg_client, _portal_client], ids=["amg_auth", "portal"])
def test_a_journal_entry_stores_the_signed_in_email(appmod, providers_ok, make):
    r = _post(make(appmod), "/journal/analyze", "multipart")
    assert r.status_code == 200
    with sqlite3.connect(appmod.LOG_DB) as cx:
        rows = cx.execute("SELECT user_id FROM journal_entries").fetchall()
    assert rows == [(EMAIL.lower(),)]


def test_glen_on_the_console_still_records_as_glen(appmod, providers_ok):
    r = _post(appmod.app.test_client(), "/journal/analyze", "multipart",
              headers={"X-Console-Key": SECRET})
    assert r.status_code == 200
    with sqlite3.connect(appmod.LOG_DB) as cx:
        assert cx.execute("SELECT user_id FROM journal_entries").fetchall() == [("glen",)]


def test_console_dictation_still_works(appmod, providers_ok):
    r = _post(appmod.app.test_client(), "/transcribe", "multipart",
              headers={"X-Console-Key": SECRET})
    assert r.status_code == 200
    assert r.get_json()["text"] == "I feel calm today"


def test_glens_journal_views_still_show_every_entry(appmod, providers_ok):
    """/journal/today and /journal/history read every row, whoever recorded it. So
    Glen's entries appear there whether they are labelled "glen" or his address."""
    _post(appmod.app.test_client(), "/journal/analyze", "multipart",
          headers={"X-Console-Key": SECRET})
    _post(_amg_client(appmod), "/journal/analyze", "multipart")
    h = appmod.app.test_client().get("/journal/history",
                                     headers={"X-Console-Key": SECRET}).get_json()
    assert sorted(e["user_id"] for e in h["entries"]) == sorted(["glen", EMAIL.lower()])


# ── the up-front status check ────────────────────────────────────────────────

def test_status_says_no_for_a_visitor_and_names_the_sign_in_page(appmod):
    d = appmod.app.test_client().get("/voice/sign-in-status").get_json()
    assert d == {"signed_in": False, "sign_in": "/auth/magic-link/request",
                 "message": "Please sign in to use voice. We will email you a link."}


def test_status_says_no_for_a_terms_member(appmod):
    d = _member_only_client(appmod).get("/voice/sign-in-status").get_json()
    assert d["signed_in"] is False


@pytest.mark.parametrize("make", [_amg_client, _portal_client], ids=["amg_auth", "portal"])
def test_status_says_yes_when_signed_in_and_returns_no_email(appmod, make):
    r = make(appmod).get("/voice/sign-in-status")
    d = r.get_json()
    assert d["signed_in"] is True
    assert "@" not in json.dumps(d)
    assert r.headers.get("Cache-Control") == "no-store"


# ── front end ────────────────────────────────────────────────────────────────

def test_every_voice_page_carries_the_sign_in_handling():
    pages = {
        "journal.html": "VoiceSignIn",
        "static/begin-doorway.html": "VoiceSignIn",
        "static/embed.html": "VoiceSignIn",
        "static/mic-input.js": "/voice/sign-in-status",
    }
    for rel, marker in pages.items():
        assert marker in (ROOT / rel).read_text(), rel
    for rel in ("static/begin.html", "static/begin-match.html", "static/begin-fireside.html"):
        assert "/static/mic-input.js" in (ROOT / rel).read_text(), rel


@pytest.mark.parametrize("rel", ["static/mic-input.js", "static/voice-signin.js"])
def test_the_copy_follows_the_rules(rel):
    src = (ROOT / rel).read_text()
    msg = "Please sign in to use voice. We will email you a link."
    assert msg in src
    assert "—" not in msg and "!" not in msg and msg != msg.upper()


# A fake DOM just big enough for mic-input.js: one [data-mic] field, a recorder
# that stops at once, and fetch answers chosen per test.
_MIC_HARNESS = r"""
var made = [], fetched = [], posted = [], gumCalls = 0;
function El(tag){ this.tagName=tag; this.children=[]; this.style={}; this.dataset={};
  this.attrs={}; this.textContent=''; this.value=''; this.parentNode=null;
  var cls={}; this.classList={add:function(c){cls[c]=1;}, remove:function(){for(var i=0;i<arguments.length;i++)delete cls[arguments[i]];},
    contains:function(c){return !!cls[c];}};
  this.listeners={}; made.push(this); }
El.prototype.appendChild=function(c){ if(c.parentNode){var p=c.parentNode.children;p.splice(p.indexOf(c),1);} c.parentNode=this; this.children.push(c); return c; };
El.prototype.insertBefore=function(c,ref){ c.parentNode=this; this.children.push(c); return c; };
El.prototype.setAttribute=function(k,v){ this.attrs[k]=v; };
El.prototype.addEventListener=function(t,f){ this.listeners[t]=f; };
El.prototype.dispatchEvent=function(){};
El.prototype.focus=function(){};
El.prototype.querySelector=function(sel){ var c=sel.replace('.','');
  for(var i=0;i<this.children.length;i++){ if(this.children[i].className===c) return this.children[i]; } return null; };
Object.defineProperty(El.prototype,'href',{get:function(){return this._href;},set:function(v){this._href=v;}});
var field = new El('textarea'); var parent = new El('div'); parent.appendChild(field);
var document = { readyState:'complete', head:new El('head'), body:new El('body'),
  createElement:function(t){return new El(t);},
  querySelectorAll:function(){ return [field]; } };
var window = { location: { pathname: '/begin/match', search: '?key=CONSOLE-SECRET' } };
function MutationObserver(){ this.observe=function(){}; }
function Event(){}
function Blob(){ this.size=1; }
function FormData(){ this.append=function(){}; }
var localStorage = { getItem:function(){ return null; } };
function MediaRecorder(){ var self=this; this.mimeType='audio/webm';
  this.start=function(){}; this.stop=function(){ self.onstop(); }; }
MediaRecorder.isTypeSupported=function(){ return true; };
window.MediaRecorder = MediaRecorder;
var navigator = { mediaDevices: { getUserMedia:function(){ gumCalls++;
  return Promise.resolve({ getTracks:function(){ return []; } }); } } };
window.navigator = navigator;
function resp(status, body){ return Promise.resolve({ status:status, ok: status>=200&&status<300,
  json:function(){ return Promise.resolve(body); } }); }
function fetch(url, opts){ fetched.push(url);
  if (opts && typeof opts.body === 'string') posted.push({ url: url, body: JSON.parse(opts.body) });
  if (url === '/auth/magic-link/request') return resp(200, { ok: true, note: 'Check your email for the sign-in link.' });
  if (url === '/voice/sign-in-status') return resp(200, STATUS);
  if (url === '/transcribe') return resp(TRANSCRIBE_STATUS, TRANSCRIBE_BODY);
  return resp(404, {}); }
"""

_MIC_DRIVE = r"""
function tick(){ return new Promise(function(r){ setTimeout(r, 0); }); }
(async function(){
  await tick(); await tick();
  var btn = made.filter(function(e){ return e.className==='mic-btn'; })[0];
  var click = function(){ btn.listeners.click({preventDefault:function(){}, stopPropagation:function(){}}); };
  click(); await tick(); await tick();
  if (btn.classList.contains('recording')) { click(); }
  for (var i=0;i<6;i++) await tick();
  var note = made.filter(function(e){ return e.className==='mic-signin'; })[0];
  var box = note ? note.children.filter(function(c){ return c.className==='mic-signin-box'; })[0] : null;
  var sent = null, said = null;
  if (box && TYPE_EMAIL) {
    var input = box.children.filter(function(c){ return c.tagName==='input'; })[0];
    var send = box.children.filter(function(c){ return c.tagName==='button'; })[0];
    input.value = TYPE_EMAIL;
    send.listeners.click({preventDefault:function(){}, stopPropagation:function(){}});
    for (var j=0;j<6;j++) await tick();
    sent = posted.filter(function(p){ return p.url==='/auth/magic-link/request'; });
    said = box.children.filter(function(c){ return c.className==='mic-signin-msg'; })[0].textContent;
  }
  console.log(JSON.stringify({ gum: gumCalls, fetched: fetched,
    note: note ? note.textContent : null, box: !!box, sent: sent, said: said,
    field: field.value }));
})();
"""


def _run_mic(status, transcribe_status=200, transcribe_body=None, type_email=""):
    if not shutil.which("node"):
        pytest.skip("node is not installed")
    src = (ROOT / "static" / "mic-input.js").read_text()
    js = (_MIC_HARNESS
          .replace("STATUS)", json.dumps(status) + ")")
          .replace("TRANSCRIBE_STATUS", str(transcribe_status))
          .replace("TRANSCRIBE_BODY", json.dumps(transcribe_body or {"text": "spoken words"}))
          + "\n" + src + _MIC_DRIVE.replace("TYPE_EMAIL", json.dumps(type_email)))
    # One function scope, so the fakes shadow node's own globals (node 22 has a
    # read-only `navigator` that a top-level var cannot replace).
    js = "(function(){\n" + js + "\n})();"
    out = subprocess.run(["node", "-e", js], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr[-800:]
    return json.loads(out.stdout.strip().splitlines()[-1])


def test_js_a_visitor_never_records_and_is_offered_an_email_box():
    r = _run_mic({"signed_in": False})
    assert r["gum"] == 0, "the microphone opened for a visitor who will be refused"
    assert "/transcribe" not in r["fetched"]
    assert r["note"].startswith("Please sign in to use voice. We will email you a link.")
    assert r["box"] is True


def test_js_the_email_box_asks_for_a_link_back_to_this_page_without_the_query():
    r = _run_mic({"signed_in": False}, type_email="new@example.com")
    assert r["sent"] == [{"url": "/auth/magic-link/request",
                          "body": {"email": "new@example.com", "next": "/begin/match"}}]
    assert "key" not in json.dumps(r["sent"]), "a console key would ride in the email"
    assert r["said"] == "Check your email for the sign-in link."


def test_js_a_401_from_transcribe_shows_the_email_box():
    r = _run_mic({"signed_in": True}, transcribe_status=401,
                 transcribe_body={"ok": False, "sign_in": "/auth/magic-link/request"})
    assert r["gum"] == 1 and "/transcribe" in r["fetched"]
    assert r["note"].startswith("Please sign in to use voice.")
    assert r["box"] is True
    assert r["field"] == ""


def test_js_a_signed_in_visitor_dictates_as_before():
    r = _run_mic({"signed_in": True})
    assert r["field"] == "spoken words"
    assert r["note"] is None


# ── journal reads are per person ─────────────────────────────────────────────

B_EMAIL = "other.client@example.com"


def _seed(a, user_id, transcript):
    from dashboard import journal_store
    with sqlite3.connect(a.LOG_DB) as cx:
        saved = journal_store.insert(cx, {
            "user_id": user_id, "recorded_at": datetime.now(timezone.utc).isoformat(),
            "duration_seconds": 5, "transcript": transcript, "metadata": None})
    return saved[0]["id"]


def _ids(resp):
    return sorted(e["transcript"] for e in resp.get_json()["entries"])


@pytest.mark.parametrize("path", ["/journal/today", "/journal/history"])
def test_journal_reads_refuse_an_anonymous_visitor(appmod, path):
    _seed(appmod, EMAIL.lower(), "a-private")
    r = appmod.app.test_client().get(path)
    assert r.status_code == 401
    assert "a-private" not in r.get_data(as_text=True)


@pytest.mark.parametrize("path", ["/journal/today", "/journal/history"])
def test_journal_reads_refuse_a_terms_member(appmod, path):
    _seed(appmod, EMAIL.lower(), "a-private")
    assert _member_only_client(appmod).get(path).status_code == 401


@pytest.mark.parametrize("make", [_amg_client, _portal_client], ids=["amg_auth", "portal"])
def test_a_client_sees_only_their_own_entries(appmod, make):
    _seed(appmod, EMAIL.lower(), "a-entry")
    _seed(appmod, B_EMAIL, "b-entry")
    _seed(appmod, "glen", "glen-legacy")
    c = make(appmod)
    assert _ids(c.get("/journal/history")) == ["a-entry"]
    assert c.get("/journal/today").get_json()["transcript"] == "a-entry"
    cb = make(appmod, B_EMAIL)
    assert _ids(cb.get("/journal/history")) == ["b-entry"]


def test_a_client_with_no_entries_today_gets_nothing_not_someone_else(appmod):
    _seed(appmod, B_EMAIL, "b-entry")
    assert _amg_client(appmod).get("/journal/today").get_json() == {}


@pytest.mark.parametrize("how", ["master", "cookie"])
def test_staff_see_every_entry_including_legacy_glen_rows(appmod, how):
    _seed(appmod, EMAIL.lower(), "a-entry")
    _seed(appmod, B_EMAIL, "b-entry")
    _seed(appmod, "glen", "glen-legacy")
    c = appmod.app.test_client()
    if how == "master":
        r = c.get("/journal/history", headers={"X-Console-Key": SECRET})
    else:
        c.set_cookie(appmod.CONSOLE_COOKIE, appmod._console_cookie_value())
        r = c.get("/journal/history")
    assert _ids(r) == ["a-entry", "b-entry", "glen-legacy"]


def test_a_signed_in_client_who_is_also_staff_still_sees_all(appmod):
    _seed(appmod, B_EMAIL, "b-entry")
    c = _amg_client(appmod)
    r = c.get("/journal/history", headers={"X-Console-Key": SECRET})
    assert _ids(r) == ["b-entry"]


def _count(a):
    with sqlite3.connect(a.LOG_DB) as cx:
        return cx.execute("SELECT COUNT(*) FROM journal_entries").fetchone()[0]


def test_delete_refuses_an_anonymous_visitor_even_with_a_valid_token(appmod):
    import journal_blueprint as jb
    rid = _seed(appmod, EMAIL.lower(), "a-entry")
    r = appmod.app.test_client().post(f"/journal/entry/{rid}/delete",
                                      json={"token": jb.delete_token(rid)})
    assert r.status_code == 401
    assert _count(appmod) == 1


def test_client_a_cannot_delete_client_bs_entry_even_holding_its_token(appmod):
    import journal_blueprint as jb
    rid_b = _seed(appmod, B_EMAIL, "b-entry")
    r = _amg_client(appmod).post(f"/journal/entry/{rid_b}/delete",
                                 json={"token": jb.delete_token(rid_b)})
    # Answers like a row already gone, so it confirms nothing about B.
    assert r.status_code == 200 and r.get_json() == {"ok": True, "deleted": False}
    assert _count(appmod) == 1


@pytest.mark.parametrize("make", [_amg_client, _portal_client], ids=["amg_auth", "portal"])
def test_a_client_deletes_their_own_entry(appmod, make):
    import journal_blueprint as jb
    rid = _seed(appmod, EMAIL.lower(), "a-entry")
    r = make(appmod).post(f"/journal/entry/{rid}/delete", json={"token": jb.delete_token(rid)})
    assert r.get_json() == {"ok": True, "deleted": True}
    assert _count(appmod) == 0


def test_staff_delete_any_entry_including_legacy_glen(appmod):
    import journal_blueprint as jb
    rid = _seed(appmod, "glen", "glen-legacy")
    r = appmod.app.test_client().post(f"/journal/entry/{rid}/delete",
                                      json={"token": jb.delete_token(rid)},
                                      headers={"X-Console-Key": SECRET})
    assert r.get_json() == {"ok": True, "deleted": True}


def test_delete_still_needs_its_token_when_signed_in(appmod):
    rid = _seed(appmod, EMAIL.lower(), "a-entry")
    r = _amg_client(appmod).post(f"/journal/entry/{rid}/delete", json={"token": "forged"})
    assert r.status_code == 403
    assert _count(appmod) == 1


def test_the_trends_page_handles_the_sign_in_refusal():
    src = (ROOT / "journal_trends.html").read_text()
    assert "/static/voice-signin.js" in src and "VoiceSignIn.handle(r, meta)" in src


# ── magic link returns to the page ───────────────────────────────────────────

HOSTILE_NEXT = ["https://evil.example", "//evil.example", "/\\evil.example",
                "%2F%2Fevil.example", "/journal@evil.example", "/%2F%2Fevil.example",
                "/journal/../../evil", "/journal/%2e%2e/x", "/journalevil", "javascript:alert(1)",
                "/journal?key=secret", "/begin/x#y", " /journal", "/JOURNAL", "",
                None, 7, ["/journal"], "/journal/" + "a" * 400, "/other",
                "/portal/me", "/portal/Zq3abcTOKEN", "/embed"]


@pytest.mark.parametrize("value", HOSTILE_NEXT)
def test_a_hostile_next_falls_back_to_home(appmod, value):
    assert appmod._safe_magic_next(value) == "/"


@pytest.mark.parametrize("value", ["/journal", "/journal/trends", "/begin", "/begin/match",
                                   "/begin/doorway", "/voice/x", "/begin/"])
def test_an_allowed_path_is_kept(appmod, value):
    assert appmod._safe_magic_next(value) == value


def _request_link(a, monkeypatch, next_value, email="new.visitor@example.com"):
    sent = {}

    def _capture(to, name, url):
        sent["to"], sent["url"] = to, url
        return "smtp", None
    monkeypatch.setattr(a, "send_magic_link_email", _capture)
    r = a.app.test_client().post("/auth/magic-link/request",
                                 json={"email": email, "next": next_value})
    assert r.status_code == 200
    return sent["url"]


def _verify(a, url):
    """Follow the emailed link the way a browser does: GET the confirm page, then POST
    the form it renders."""
    import re as _re
    from urllib.parse import urlparse
    u = urlparse(url)
    c = a.app.test_client()
    page = c.get(u.path + "?" + u.query).get_data(as_text=True)
    fields = dict(_re.findall(r"name='([^']+)' value='([^']*)'", page))
    import html as _h
    fields = {k: _h.unescape(v) for k, v in fields.items()}
    r = c.post(u.path, data=fields)
    m = _re.search(r'content="1;url=([^"]*)"', r.get_data(as_text=True))
    return c, r, (_h.unescape(m.group(1)) if m else None), fields


def test_a_brand_new_visitor_round_trips_back_to_the_page(appmod, monkeypatch, providers_ok):
    url = _request_link(appmod, monkeypatch, "/begin/match")
    assert "next=%2Fbegin%2Fmatch" in url
    c, r, dest, fields = _verify(appmod, url)
    assert fields.get("next") == "/begin/match"
    assert "amg_auth=" in r.headers.get("Set-Cookie", "")
    assert dest == "/begin/match"
    assert 'href="/begin/match"' in r.get_data(as_text=True)
    # and the new session now passes the voice gate
    assert c.get("/voice/sign-in-status").get_json()["signed_in"] is True


@pytest.mark.parametrize("value", ["https://evil.example", "//evil.example", "/\\evil.example",
                                   "%2F%2Fevil.example", "/journal@evil.example"])
def test_a_hostile_next_is_dropped_from_the_email_and_lands_home(appmod, monkeypatch, value):
    url = _request_link(appmod, monkeypatch, value)
    assert "next=" not in url
    _c, _r, dest, fields = _verify(appmod, url)
    assert "next" not in fields
    assert dest == "/"


@pytest.mark.parametrize("value", ["https://evil.example", "//evil.example", "/\\evil.example",
                                   "%2F%2Fevil.example", "/journal@evil.example"])
def test_a_hand_edited_link_is_checked_again_at_verify(appmod, monkeypatch, value):
    from urllib.parse import quote
    url = _request_link(appmod, monkeypatch, None) + "&next=" + quote(value, safe="")
    _c, _r, dest, _f = _verify(appmod, url)
    assert dest == "/"


def test_no_next_keeps_the_old_link_and_home_redirect(appmod, monkeypatch):
    url = _request_link(appmod, monkeypatch, None)
    assert url.endswith("/auth/magic-link/verify?token=" + url.split("token=")[1])
    assert "next=" not in url
    _c, _r, dest, _f = _verify(appmod, url)
    assert dest == "/"


def test_one_address_asked_again_and_again_gets_no_more_links(appmod, monkeypatch):
    # The voice pages put the link request in front of every visitor (2026-10-22).
    sent = []
    monkeypatch.setattr(appmod, "send_magic_link_email",
                        lambda e, n, u: sent.append(e) or ("smtp", None))
    c = appmod.app.test_client()
    for _ in range(8):
        r = c.post("/auth/magic-link/request", json={"email": "victim@example.com"})
        assert r.status_code == 200 and r.get_json()["note"] == "Check your email for the sign-in link."
    assert len(sent) == 5


def test_the_link_request_takes_the_auth_limiter(appmod, monkeypatch):
    seen = []
    monkeypatch.setattr(appmod, "_auth_rate_limited", lambda e: seen.append(e) or None)
    monkeypatch.setattr(appmod, "send_magic_link_email", lambda e, n, u: ("smtp", None))
    appmod.app.test_client().post("/auth/magic-link/request", json={"email": "a@example.com"})
    assert seen == ["a@example.com"]


def test_an_undelivered_link_does_not_use_up_the_address(appmod, monkeypatch):
    outcome = {"via": "console"}
    monkeypatch.setattr(appmod, "send_magic_link_email",
                        lambda e, n, u: (outcome["via"], None))
    c = appmod.app.test_client()
    for _ in range(6):
        c.post("/auth/magic-link/request", json={"email": "outage@example.com"})
    outcome["via"] = "smtp"
    r = c.post("/auth/magic-link/request", json={"email": "outage@example.com"}).get_json()
    assert r["sent_via"] == "smtp"


def test_enter_cannot_send_a_second_request_while_one_is_out():
    src = (ROOT / "static" / "voice-signin.js").read_text()
    assert "if (btn.disabled) return;" in src
    assert "if (send.disabled) return;" in (ROOT / "static" / "mic-input.js").read_text()


# ── which console tokens read client transcripts ─────────────────────────────

def _fake_token(a, monkeypatch, kind):
    """A console token that is an owner token, or a VA/role token, and nothing else."""
    monkeypatch.setattr(a, "_owner_token_ok", lambda k: kind == "owner" and k == "tok-x")
    monkeypatch.setattr(a, "_role_for_token", lambda k: "va" if kind == "role" and k == "tok-x" else None)
    return {"X-Console-Key": "tok-x"}


def test_an_owner_token_sees_every_entry(appmod, monkeypatch):
    _seed(appmod, B_EMAIL, "b-entry")
    _seed(appmod, "glen", "glen-legacy")
    h = _fake_token(appmod, monkeypatch, "owner")
    assert _ids(appmod.app.test_client().get("/journal/history", headers=h)) == ["b-entry", "glen-legacy"]


def test_a_va_token_records_but_reads_no_client_transcripts(appmod, providers_ok, monkeypatch):
    _seed(appmod, B_EMAIL, "b-entry")
    _seed(appmod, "glen", "glen-legacy")
    _seed(appmod, "staff", "staff-entry")
    h = _fake_token(appmod, monkeypatch, "role")
    c = appmod.app.test_client()
    assert _post(c, "/transcribe", "multipart", headers=h).status_code == 200
    assert _ids(c.get("/journal/history", headers=h)) == ["staff-entry"]


def test_glen_signed_in_and_on_the_console_still_records_as_glen(appmod, providers_ok):
    r = _post(_amg_client(appmod), "/journal/analyze", "multipart",
              headers={"X-Console-Key": SECRET})
    assert r.status_code == 200
    with sqlite3.connect(appmod.LOG_DB) as cx:
        assert cx.execute("SELECT user_id FROM journal_entries").fetchall() == [("glen",)]


def test_js_an_unanswered_status_check_still_lets_the_server_decide():
    r = _run_mic(None)
    assert r["gum"] == 1 and "/transcribe" in r["fetched"]


def test_the_embed_sends_other_sites_to_the_full_chat():
    src = (ROOT / "static" / "embed.html").read_text()
    assert "_inOtherSite()" in src and "Open the full chat to sign in and talk" in src


# ── a sign-in POST must come from our own page (login CSRF) ─────────────────

def _fresh_token(a, email="someone.new@example.com"):
    tok = secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc)
    with sqlite3.connect(a.LOG_DB) as cx:
        cx.execute("INSERT INTO auth_tokens (token_hash, email, purpose, created_at, expires_at) "
                   "VALUES (?,?,?,?,?)", (a._hash_token(tok), email, "magic_link",
                   now.isoformat(), (now + timedelta(minutes=60)).isoformat()))
        cx.commit()
    return tok


@pytest.mark.parametrize("headers", [{"Sec-Fetch-Site": "cross-site"},
                                     {"Sec-Fetch-Site": "same-site"},
                                     {"Origin": "https://evil.example"}])
def test_another_site_cannot_post_a_sign_in(appmod, headers):
    r = appmod.app.test_client().post("/auth/magic-link/verify",
                                      data={"token": _fresh_token(appmod)}, headers=headers)
    assert r.status_code == 403
    assert "amg_auth=" not in r.headers.get("Set-Cookie", "")


@pytest.mark.parametrize("headers", [{"Sec-Fetch-Site": "same-origin"},
                                     {"Origin": "http://localhost"}, {}])
def test_our_own_confirm_page_still_signs_in(appmod, headers):
    r = appmod.app.test_client().post("/auth/magic-link/verify",
                                      data={"token": _fresh_token(appmod)}, headers=headers)
    assert "amg_auth=" in r.headers.get("Set-Cookie", "")


def test_the_sign_in_email_escapes_the_name():
    src = (ROOT / "app.py").read_text()
    assert "<p>Hi {name or 'there'},</p>" not in src
    assert src.count("<p>Hi {html.escape(name or 'there')},</p>") == 3


@pytest.mark.parametrize("page", ["journal.html", "static/begin-doorway.html"])
def test_the_page_asks_before_the_mic_opens(page):
    src = (ROOT / page).read_text()
    body = src.split("async function record() {", 1)[1][:400]
    assert "voiceSignedIn === null && window.VoiceSignIn" in body
