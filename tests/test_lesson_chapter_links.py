"""Chapter links in a lesson: <a href="#t=754">12:34 Topic</a> restarts the lesson's video
at that second. Glen, platform tab, 25 Nov 2026: "yes, build chapter links". Rumble's embed
honours ?start=N: Glen played rumble.com/embed/v7e986a/?start=90 and it began at 1:30.

The sanitizer keeps only the exact #t=N form. Every other fragment or relative href is still
dropped, so this opens no new way to put a link in a lesson.
"""
import importlib
import json
import os
import shutil
import subprocess

import pytest

from dashboard.courses_sanitize import sanitize_html

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_MHOST = "http://mentorshipu.test"
SLUG = "live-class-highlights"


@pytest.mark.parametrize("href", ["#t=0", "#t=754", "#t=99999", " #t=90 "])
def test_a_chapter_link_is_kept(href):
    out = sanitize_html(f'<p><a href="{href}">Topic</a></p>')
    assert f'href="{href.strip()}"' in out


@pytest.mark.parametrize("href", [
    "#t=", "#t=abc", "#t=100000", "#t=90&x=1", "#t=90#t=1", "#top", "#t=-5",
    "t=90", "/learn/x#t=90", "javascript:alert(1)//#t=90", "#t=90\njavascript:x",
    "#t=９０",
])
def test_anything_else_without_a_scheme_is_still_dropped(href):
    out = sanitize_html(f'<p><a href="{href}">Topic</a></p>')
    assert "href" not in out, (href, out)


def test_http_links_are_unchanged():
    assert 'href="https://example.com/#t=90"' in sanitize_html(
        '<a href="https://example.com/#t=90">x</a>')


@pytest.fixture
def lesson_client(monkeypatch, tmp_path):
    root = tmp_path / "courses"
    shutil.copytree(os.path.join(ROOT, "courses", SLUG), root / SLUG)
    lesson = sorted((root / SLUG).glob("*/*.md"))[0]
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("COURSES_ROOT", str(root))
    monkeypatch.setenv("MENTORSHIP_BASE_URL", _MHOST)
    import app as appmod
    importlib.reload(appmod)
    monkeypatch.setattr(appmod, "send_mentorship_setup_link", lambda *a, **k: ("test", None))
    appmod.app.config["TESTING"] = True
    url = f"/learn/{SLUG}/{lesson.parent.name}/{lesson.stem}"
    return appmod.app.test_client(), lesson, url


def test_the_script_ships_only_with_a_chapter_link(lesson_client):
    c, lesson, url = lesson_client
    plain = c.get(url, base_url=_MHOST).get_data(as_text=True)
    assert 'a[href^="#t="]' not in plain
    lesson.write_text(lesson.read_text() + '\n\n<p><a href="#t=754">12:34 Topic</a></p>\n')
    page = c.get(url, base_url=_MHOST).get_data(as_text=True)
    assert '<a href="#t=754">12:34 Topic</a>' in page
    assert 'a[href^="#t="]' in page
    assert "rumble.com/embed/" in page


def _run_handler(iframe_src, href):
    """Run the shipped script in node against a minimal DOM and report the iframe src."""
    import courses_blueprint as cb
    js = cb._CHAPTER_JS.replace("<script>", "").replace("</script>", "")
    harness = """
var handler=null, scrolled=false, prevented=false;
var frame={src:%s, scrollIntoView:function(){scrolled=true;}};
var link={getAttribute:function(){return %s;}};
global.location={href:"https://mentorshipu.com/learn/x/y/z"};
global.document={addEventListener:function(t,f){handler=f;},
  querySelector:function(sel){return frame.src?frame:null;}};
%s
handler({target:{closest:function(s){return s.indexOf('#t=')>=0?link:null;}},
  preventDefault:function(){prevented=true;}});
console.log(JSON.stringify({src:frame.src, scrolled:scrolled, prevented:prevented}));
""" % (json.dumps(iframe_src), json.dumps(href), js)
    out = subprocess.run(["node", "-e", harness], capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_a_click_restarts_rumble_at_that_second():
    r = _run_handler("https://rumble.com/embed/v7ix1ww/?pub=4", "#t=754")
    assert r == {"src": "https://rumble.com/embed/v7ix1ww/?pub=4&start=754",
                 "scrolled": True, "prevented": True}


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_a_second_click_replaces_the_start_and_youtube_autoplays():
    r = _run_handler("https://www.youtube.com/embed/abc?start=10&enablejsapi=1", "#t=90")
    assert r["src"] == "https://www.youtube.com/embed/abc?start=90&enablejsapi=1&autoplay=1"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_a_malformed_link_or_no_video_leaves_the_page_alone():
    assert _run_handler("https://rumble.com/embed/v1/", "#t=9x") == {
        "src": "https://rumble.com/embed/v1/", "scrolled": False, "prevented": False}
    assert _run_handler("", "#t=90")["prevented"] is False
