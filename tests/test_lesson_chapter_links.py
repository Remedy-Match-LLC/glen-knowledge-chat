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


@pytest.mark.parametrize("src", [
    "https://evil.example\\@www.youtube.com/embed/x",
    "https://a@www.youtube.com/embed/x",
    "https://rumble.com/embed/v1/\tx",
    "http://www.youtube.com/embed/x",
    "javascript://www.youtube.com/%0aalert(1)",
])
def test_an_iframe_src_a_browser_reads_differently_is_dropped(src):
    # Security review, 2026-11-25: urlparse saw youtube, a browser loads evil.example.
    assert "iframe" not in sanitize_html(f'<iframe src="{src}"></iframe>')


def test_every_course_iframe_still_passes():
    import glob
    import re
    srcs = [m for f in glob.glob(os.path.join(ROOT, "courses", "**", "*.md"), recursive=True)
            for m in re.findall(r'<iframe[^>]*src="([^"]+)"', open(f, encoding="utf-8").read())]
    assert len(srcs) > 50
    dropped = [s for s in srcs if "iframe" not in sanitize_html(f'<iframe src="{s}"></iframe>')]
    assert dropped == []


def test_the_script_skips_text_that_only_mentions_the_form():
    import courses_blueprint as cb
    assert '<a href="#t=' not in sanitize_html('<p>Write href="#t=90" in your link.</p>')


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


def _need_node():
    if shutil.which("node"):
        return
    if os.environ.get("CI"):
        pytest.fail("node is missing in CI, so the chapter-link script went untested")
    pytest.skip("node not installed")


def _run_handler(frames, href, link_at=None):
    """Run the shipped script in node against a small DOM.

    frames: iframe srcs in page order. link_at: how many frames come before the link
    (default all). The stubs apply the script's real selectors: querySelectorAll keeps
    only frames whose src holds one of the selector's src*= values, and closest matches
    only the exact chapter selector. Returns every frame's src after one click.
    """
    import courses_blueprint as cb
    _need_node()
    js = cb._CHAPTER_JS.replace("<script>", "").replace("</script>", "")
    link_at = len(frames) if link_at is None else link_at
    harness = """
var handler=null, scrolled=null, prevented=false, srcs=%s, linkAt=%s;
var frames=srcs.map(function(s,i){return {src:s, pos:i<linkAt?i:i+1,
  scrollIntoView:function(){scrolled=i;},
  compareDocumentPosition:function(o){return o.pos>this.pos?4:2;}};});
var link={pos:linkAt, getAttribute:function(){return %s;}};
global.location={href:"https://mentorshipu.com/learn/x/y/z"};
global.document={addEventListener:function(t,f){handler=f;},
  querySelectorAll:function(sel){
    var want=[], re=/iframe\[src\*="([^"]+)"\]/g, m;
    while((m=re.exec(sel)))want.push(m[1]);
    return frames.filter(function(f){return want.some(function(w){return f.src.indexOf(w)>=0;});});}};
%s
handler({target:{closest:function(s){return s==='a[href^="#t="]'?link:null;}},
  preventDefault:function(){prevented=true;}});
console.log(JSON.stringify({srcs:frames.map(function(f){return f.src;}), scrolled:scrolled, prevented:prevented}));
""" % (json.dumps(frames), link_at, json.dumps(href), js)
    out = subprocess.run(["node", "-e", harness], capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


def test_a_click_restarts_rumble_at_that_second():
    r = _run_handler(["https://rumble.com/embed/v7ix1ww/?pub=5q5b3"], "#t=754")
    assert r == {"srcs": ["https://rumble.com/embed/v7ix1ww/?pub=5q5b3&start=754"],
                 "scrolled": 0, "prevented": True}


def test_a_second_click_replaces_the_start_and_youtube_autoplays():
    r = _run_handler(["https://www.youtube.com/embed/abc?start=10&enablejsapi=1"], "#t=90")
    assert r["srcs"] == ["https://www.youtube.com/embed/abc?start=90&enablejsapi=1&autoplay=1"]


def test_the_link_controls_the_nearest_video_above_it():
    # Review round 2: an intro video, then the lesson video, then the chapter list.
    intro, main = "https://www.youtube.com/embed/intro", "https://rumble.com/embed/v1/"
    r = _run_handler([intro, main], "#t=60")
    assert r["srcs"] == [intro, main + "?start=60"] and r["scrolled"] == 1
    # Chapters placed between the two videos belong to the first.
    r = _run_handler([intro, main], "#t=60", link_at=1)
    assert r["srcs"][0].endswith("start=60&autoplay=1") and r["srcs"][1] == main
    # Chapters above every video fall back to the first.
    r = _run_handler([intro, main], "#t=60", link_at=0)
    assert r["srcs"][0].endswith("start=60&autoplay=1") and r["scrolled"] == 0


def test_a_non_video_iframe_is_never_the_target():
    r = _run_handler(["https://www.google.com/maps/embed", "https://rumble.com/embed/v1/"],
                     "#t=5")
    assert r["srcs"] == ["https://www.google.com/maps/embed", "https://rumble.com/embed/v1/?start=5"]


def test_a_malformed_link_or_no_video_leaves_the_page_alone():
    assert _run_handler(["https://rumble.com/embed/v1/"], "#t=9x") == {
        "srcs": ["https://rumble.com/embed/v1/"], "scrolled": None, "prevented": False}
    assert _run_handler([], "#t=90")["prevented"] is False
