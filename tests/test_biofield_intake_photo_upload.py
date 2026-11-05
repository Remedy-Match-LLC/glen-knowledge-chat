"""The Biofield Intake page has the Report tab's Upload photo button (Glen, 2026-11-04:
"Put that button on the intake page as well."). Shown only when the test has a client
email. The upload route files the photo under the test's SAVED email and name, so the page
sends what its fields show and the route refuses an unsaved edit. The button never saves
the header: that save can raise an invoice (review round 1)."""
import re
import shutil
import subprocess

import pytest

from dashboard.biofield_report_html import _AUTHOR_PHOTO_JS, render_author_html


def _report(email="marvin@example.com"):
    return {"test_id": "a59", "date": "2026-11-04",
            "client": {"name": "Kai Example", "email": email}, "layers": []}


def test_intake_page_has_upload_photo_with_an_email():
    html = render_author_html(_report())
    assert "Upload photo" in html
    assert "id=authorphotofile" in html
    assert "uploadAuthorPhoto('a59')" in html
    assert "async function uploadAuthorPhoto(tid)" in html
    # The preview and the shared-email picks are still there.
    assert "id=authorclientphoto" in html and "id=authorphotopicks" in html
    # It sits above the picks, inside the header.
    assert html.index("id=authorphotofile") < html.index("id=authorphotopicks")


@pytest.mark.parametrize("email", ["", "   "])
def test_intake_page_has_no_upload_without_an_email(email):
    html = render_author_html(_report(email))
    assert "Upload photo" not in html
    assert "authorphotofile" not in html
    assert "uploadAuthorPhoto" not in html


def test_upload_posts_to_the_report_tabs_route():
    assert "fetch('/test/'+tid+'/photo',{method:'POST',body:fd})" in _AUTHOR_PHOTO_JS


def _node():
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    return node


def _run_js(email_value, refresh="async function(){calls.push('refresh')}",
            scenario="once", fetch_ok="true"):
    """Run uploadAuthorPhoto with a stub DOM and fetch; return what it did."""
    body = re.search(r"<script>(.*)</script>", _AUTHOR_PHOTO_JS, re.S).group(1)
    js = """
var calls=[]; var els={
  authorphotofile:{files:[{name:'A.jpg'}],value:'x'},
  authorphotostat:{textContent:''},
  h_email:{value:%(email)r}, h_name:{value:'Kai Example'},
  authorclientphoto:{src:'',style:{}}};
var document={getElementById:function(i){return els[i]||null}};
var saveHeader=async function(){calls.push('SAVE')};
var window={refreshHeaderPhoto:%(refresh)s, saveHeader:saveHeader};
var release=null;
function FormData(){var self=this;self.parts=[];this.append=function(k,v){self.parts.push(k+'='+(v&&v.name||v))}}
async function fetch(u,o){calls.push('post '+u+' '+o.body.parts.join('&'));
  // A second file is picked while the upload is in flight; 'switch' also edits the name.
  els.authorphotofile.files=[{name:'B.jpg'}];
  if(%(scenario)r==='switch') els.h_name.value='Someone Else';
  if(%(scenario)r==='lost') throw new Error('network');
  if(%(scenario)r==='overlap'){ await new Promise(function(r){release=r}); }
  return {json:async function(){return {ok:%(fetch_ok)s,email:'marvin@example.com',prod_pushed:true,error:'nope'}}}}
%(body)s
async function main(){
  if(%(scenario)r==='overlap'){
    var first=uploadAuthorPhoto('a59');
    await new Promise(function(r){setTimeout(r,0)});
    await uploadAuthorPhoto('a59');
    var second=els.authorphotostat.textContent;
    release(); await first;
    return {second:second};
  }
  await uploadAuthorPhoto('a59'); return {};
}
main().then(function(extra){
  console.log(JSON.stringify(Object.assign({calls:calls, stat:els.authorphotostat.textContent,
    src:els.authorclientphoto.src, display:els.authorclientphoto.style.display||'',
    input:els.authorphotofile.value}, extra)))});
""" % {"email": email_value, "refresh": refresh, "scenario": scenario,
       "fetch_ok": fetch_ok, "body": body}
    out = subprocess.run([_node(), "-e", js], capture_output=True, text=True, timeout=20)
    assert out.returncode == 0, out.stderr[-1500:]
    import json
    return json.loads(out.stdout.strip().splitlines()[-1])


POST = ("post /test/a59/photo photo=A.jpg&expect_email=marvin@example.com"
        "&expect_name=Kai Example")


def test_upload_sends_the_fields_it_shows_and_never_saves_the_header():
    r = _run_js("marvin@example.com")
    assert r["calls"] == [POST, "refresh"]
    assert "SAVE" not in r["calls"]
    assert r["stat"] == "Saved."
    assert r["src"].startswith("/client-photo/marvin%40example.com?name=Kai%20Example&t=")
    assert r["display"] == "block"
    assert r["input"] == ""


def test_upload_refuses_before_an_email_is_typed():
    r = _run_js("  ")
    assert r["calls"] == []
    assert "email first" in r["stat"]


def test_a_second_pick_during_an_upload_is_refused_and_the_first_file_is_sent():
    r = _run_js("marvin@example.com", scenario="overlap")
    assert r["second"] == "An upload is already running."
    assert r["calls"] == [POST, "refresh"]


def test_a_failed_display_refresh_still_reports_the_upload_as_saved():
    r = _run_js("marvin@example.com", refresh="async function(){throw new Error('x')}")
    assert r["stat"] == "Saved."


def test_a_refused_upload_says_so_and_changes_no_preview():
    r = _run_js("marvin@example.com", fetch_ok="false")
    assert r["stat"] == "Error: nope"
    assert r["src"] == ""
    assert "refresh" not in r["calls"]


# --- the route: an unsaved header edit is refused, nothing is filed ------------------------

@pytest.fixture
def _no_console_gate(monkeypatch):
    # The local app's console gate; no prod push from a test.
    monkeypatch.delenv("CONSOLE_SECRET", raising=False)
    monkeypatch.delenv("PORTAL_PUBLISH_BASE_URL", raising=False)
    import dashboard as _d
    monkeypatch.setattr(_d, "CONSOLE_SECRET", "", raising=False)


def _app(tmp_path, name, email):
    import sqlite3
    from biofield_local_app import create_app
    from tests.test_client_photo_per_person import _db, _intake, _no_fetch
    db = _db(tmp_path)
    tid = _intake(db, name, email)
    return db, tid, create_app(db, fetch_client_photo=_no_fetch).test_client()


def _post(c, tid, **form):
    import io
    data = {"photo": (io.BytesIO(b"NEW"), "p.png"), **form}
    return c.post(f"/test/{tid}/photo", data=data, content_type="multipart/form-data")


@pytest.mark.parametrize("form", [
    {"expect_email": "other@example.com", "expect_name": "Sam Solo"},
    {"expect_email": "solo@example.com", "expect_name": "Someone Else"},
    {"expect_email": "", "expect_name": "Sam Solo"},
])
def test_route_refuses_an_unsaved_header_and_files_nothing(_no_console_gate, tmp_path, form):
    import sqlite3
    from dashboard import client_photos as cph
    db, tid, c = _app(tmp_path, "Sam Solo", "solo@example.com")
    r = _post(c, tid, **form)
    assert r.status_code == 409 and r.get_json()["unsaved_header"] is True
    with sqlite3.connect(db) as cx:
        assert cph.get_for_client(cx, "900") is None and cph.get(cx, "solo@example.com") is None


def test_route_accepts_matching_fields_case_and_space_insensitive_on_email(_no_console_gate, tmp_path):
    import sqlite3
    from dashboard import client_photos as cph
    db, tid, c = _app(tmp_path, "Sam Solo", "solo@example.com")
    r = _post(c, tid, expect_email=" Solo@Example.com ", expect_name=" Sam Solo ")
    assert r.get_json()["ok"] is True
    with sqlite3.connect(db) as cx:
        assert cph.get_for_client(cx, "900")["blob"] == b"NEW"


def test_route_without_expect_fields_works_as_before(_no_console_gate, tmp_path):
    db, tid, c = _app(tmp_path, "Sam Solo", "solo@example.com")
    assert _post(c, tid).get_json()["ok"] is True


def test_a_header_changed_during_upload_shows_no_photo_under_the_new_name():
    r = _run_js("marvin@example.com", scenario="switch")
    assert r["stat"] == "Saved for Kai Example. The header now shows someone else."
    assert r["src"] == ""
    assert r["calls"][-1] == "refresh"


def test_a_lost_answer_is_reported_as_unknown_not_as_a_failure():
    r = _run_js("marvin@example.com", scenario="lost")
    assert r["stat"].startswith("No answer from the server, so the photo may or may not have saved.")
    assert "Reload the page" in r["stat"]
    assert r["src"] == ""


def test_the_real_page_defines_the_refresh_it_calls():
    html = render_author_html(_report())
    assert "async function refreshHeaderPhoto()" in html
    assert "window.refreshHeaderPhoto" in html
