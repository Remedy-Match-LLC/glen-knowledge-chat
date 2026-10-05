"""The Clinical Theory of Everything explainer in the portal (Glen, 2026-10-05).

It is the first line of Accelerate healing for every client, plays from Rumble in
place, and ticks once opened. The three device lines under it stay where they were.
"""
import shutil
import sqlite3
import subprocess
import textwrap

import pytest

from dashboard import portal_onboarding as ob
from tests.test_onboarding_route import _app, _seed
from tests.test_portal_onboarding import _cx


def _heal(status):
    return next(ph for ph in status["phases"] if ph["key"] == "heal")["steps"]


def test_the_video_is_first_in_accelerate_healing_and_the_devices_follow():
    keys = [st["key"] for st in _heal(ob.build_status(_cx(), "new@x.com"))]
    assert keys == ["ctoe", "light", "pemf", "h2water", "evox"]


def test_the_video_plays_from_rumble_not_youtube():
    st = _heal(ob.build_status(_cx(), "new@x.com"))[0]
    assert st["video"].startswith("https://rumble.com/embed/")
    assert st["href"].startswith("https://rumble.com/")
    assert "youtube" not in (st["video"] + st["href"]).lower()
    assert st["done"] is False
    assert not st.get("checkable")


def test_watching_is_remembered_per_client():
    cx = _cx()
    from dashboard import client_facts
    client_facts.set_fact(cx, "seen@x.com", "watched_ctoe", True)
    assert _heal(ob.build_status(cx, "seen@x.com"))[0]["done"] is True
    assert _heal(ob.build_status(cx, "other@x.com"))[0]["done"] is False


def test_opening_the_video_saves_watched_through_the_portal_route(tmp_path, monkeypatch):
    appmod = _app(tmp_path, monkeypatch)
    tok = _seed(appmod, "v@x.com")
    r = appmod.app.test_client().post(f"/api/portal/{tok}/onboarding/accelerator",
                                      json={"key": "ctoe", "value": True})
    assert r.status_code == 200
    assert _heal(r.get_json()["status"])[0]["done"] is True
    # Saving the video does not tick a device.
    assert all(st["done"] is False for st in _heal(r.get_json()["status"])[1:4])
    with sqlite3.connect(appmod.LOG_DB) as cx:
        assert ob.accelerator_status(cx, "v@x.com") == {
            "light": False, "pemf": False, "h2water": False}


@pytest.mark.skipif(not shutil.which("node"), reason="node not available")
def test_the_tile_renders_a_link_that_opens_in_place():
    js = textwrap.dedent('''
      const fs = require('fs');
      const mod = {exports:{}};
      const win = {};
      new Function('module','exports','window',
        fs.readFileSync('static/js/portal-conditions.js','utf8'))({exports:{}}, {}, win);
      new Function('module','exports','window',
        fs.readFileSync('static/js/portal-onboarding.js','utf8'))(mod, mod.exports, win);
      const html = mod.exports.renderOnboarding({phases:[{key:'heal', title:'Accelerate healing',
        steps:[{key:'ctoe', label:'Watch: The Clinical Theory of Everything (7 min)', done:false,
                href:'https://rumble.com/v7gfl3s-x.html', video:'https://rumble.com/embed/v7e986a/?pub=5q5b3'},
               {key:'light', label:'Light', done:false, href:'https://clinicalpraxis.com', checkable:true}]}]});
      const want = [/class="ob-video-link"/, /data-video="https:\\/\\/rumble\\.com\\/embed\\/v7e986a\\/\\?pub=5q5b3"/,
                    /data-step="ctoe"/, /data-done="0"/, /<div class="ob-video-frame" hidden><\\/div>/];
      for (const re of want) if (!re.test(html)) { console.error('missing ' + re); process.exit(1); }
      if ((html.match(/ob-accelerator-check/g) || []).length !== 1) { console.error('video got a checkbox'); process.exit(1); }
      if (html.indexOf('ob-video-link') > html.indexOf('ob-accelerator-check')) { console.error('video is not first'); process.exit(1); }
      console.log('ok');
    ''')
    out = subprocess.run(["node", "-e", js], cwd=".", capture_output=True, text=True)
    assert out.returncode == 0, out.stderr


def test_a_failed_watched_read_does_not_break_the_later_reads():
    """No client_facts table: the read fails inside its savepoint and the rest of
    the status still builds."""
    import sqlite3 as _sq
    cx = _sq.connect(":memory:")
    assert ob.ctoe_watched(cx, "x@x.com") is False
    cx.execute("CREATE TABLE t (a)")
    cx.execute("INSERT INTO t VALUES (1)")
    assert cx.execute("SELECT a FROM t").fetchone()[0] == 1


@pytest.mark.skipif(not shutil.which("node"), reason="node not available")
def test_an_unwatched_video_does_not_reopen_a_finished_phase_on_home():
    """Round 1: a client who had finished every heal step must still read
    "You have completed this phase." The video is offered, never required."""
    js = textwrap.dedent('''
      const shell = require('./static/js/portal-shell.js');
      const video = {key:'ctoe', label:'Watch: The Clinical Theory of Everything (7 min)',
                     done:false, href:'https://rumble.com/v7gfl3s-x.html', video:'https://rumble.com/embed/v7e986a/'};
      const done = k => ({key:k, label:k, done:true, href:'#'});
      const finished = shell.renderHome({journey:{phases:[{key:'heal', title:'Accelerate healing',
        steps:[video, done('light'), done('pemf'), done('h2water'), done('evox')]}]}});
      if (!/You have completed this phase/.test(finished)) { console.error('video reopened the phase'); process.exit(1); }
      if (!/aria-valuenow="100"/.test(finished)) { console.error('progress moved'); process.exit(1); }
      const open = shell.renderHome({journey:{phases:[{key:'heal', title:'Accelerate healing',
        steps:[video, {key:'light', label:'Light', done:false, href:'#'}]}]}});
      if (!/Next, Light\\./.test(open)) { console.error('video became Next'); process.exit(1); }
      if (!/href="https:\\/\\/rumble\\.com\\/v7gfl3s-x\\.html" target="_blank" rel="noopener" data-video-step="ctoe"/.test(open)) {
        console.error('home video link does not open in a new tab'); process.exit(1); }
      console.log('ok');
    ''')
    out = subprocess.run(["node", "-e", js], cwd=".", capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
