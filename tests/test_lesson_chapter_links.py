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


def _run_handler(frames, href, link_at=None, yt_bound=None):
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
var calls=[], ytAt=%s;
global.window={};
if(ytAt!==null)window.__muYT={getIframe:function(){return frames[ytAt];},
  seekTo:function(n,a){calls.push(["seekTo",n,a]);}, playVideo:function(){calls.push(["play"]);}};
global.location={href:"https://mentorshipu.com/learn/x/y/z"};
global.document={addEventListener:function(t,f){handler=f;},
  querySelectorAll:function(sel){
    var want=[], re=/iframe\\[src\\*="([^"]+)"( i)?\\]/g, m;
    while((m=re.exec(sel)))want.push([m[1], !!m[2]]);
    return frames.filter(function(f){return want.some(function(w){
      return (w[1]?f.src.toLowerCase():f.src).indexOf(w[1]?w[0].toLowerCase():w[0])>=0;});});}};
%s
handler({target:{closest:function(s){return s==='a[href^="#t="]'?link:null;}},
  preventDefault:function(){prevented=true;}});
var out={srcs:frames.map(function(f){return f.src;}), scrolled:scrolled, prevented:prevented};
if(calls.length)out.calls=calls;
console.log(JSON.stringify(out));
""" % (json.dumps(frames), link_at, json.dumps(href), json.dumps(yt_bound), js)
    out = subprocess.run(["node", "-e", harness], capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


def test_a_click_restarts_rumble_at_that_second():
    r = _run_handler(["https://rumble.com/embed/v7ix1ww/?pub=5q5b3"], "#t=754")
    assert r == {"srcs": ["https://rumble.com/embed/v7ix1ww/?pub=5q5b3&start=754"],
                 "scrolled": 0, "prevented": True}


def test_an_existing_start_is_replaced_and_an_unbound_youtube_autoplays():
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


def test_a_bound_youtube_player_seeks_in_place_so_the_watched_hook_survives():
    # Review round 2 v2: reloading the iframe would detach the auto-mark-watched hook.
    src = "https://www.youtube.com/embed/abc?enablejsapi=1"
    r = _run_handler([src], "#t=90", yt_bound=0)
    assert r["srcs"] == [src]
    assert r["calls"] == [["seekTo", 90, True], ["play"]]
    # A player bound to a different video does not hijack this one.
    r = _run_handler([src, "https://rumble.com/embed/v1/"], "#t=7", yt_bound=0)
    assert r["srcs"] == [src, "https://rumble.com/embed/v1/?start=7"] and "calls" not in r


def test_a_mixed_case_embed_is_still_controlled():
    r = _run_handler(["https://RUMBLE.COM/embed/v1/"], "#t=3")
    assert r["srcs"] == ["https://rumble.com/embed/v1/?start=3"]


def test_the_watched_hook_exposes_its_player():
    import courses_blueprint as cb
    import inspect
    assert "window.__muYT=new YT.Player(" in inspect.getsource(cb.lesson_page)


# Glen, platform tab, 25 Nov 2026: "yes" to Rumble's own player on lesson pages, so one
# click jumps and plays. Teaching's hand test on the live lesson the same day: through
# rumble.com/embedJS/u4 the api has play, setCurrentTime and getPaused; on a cold player
# seek-and-play does nothing until the big play button is clicked; after that it works.
_RUMBLE_HARNESS = r"""
var now=0, timers=[], log=[], handler=null, appended=null, rumbleCalls=[];
global.setTimeout=function(f,ms){timers.push({f:f,at:now+ms,every:0});return timers.length;};
global.setInterval=function(f,ms){timers.push({f:f,at:now+ms,every:ms});return timers.length;};
global.clearInterval=function(id){if(timers[id-1])timers[id-1].dead=true;};
function advance(ms){var end=now+ms;for(;;){var due=timers.filter(function(t){return !t.dead&&t.at<=end;});
  if(!due.length)break;due.sort(function(a,b){return a.at-b.at;});var t=due[0];now=t.at;
  if(t.every)t.at+=t.every;else t.dead=true;t.f();}now=end;}
function node(src,width,pos){return {src:src,pos:pos,attrs:{width:width},style:{},
  getAttribute:function(k){return this.attrs[k];},setAttribute:function(k,v){this.attrs[k]=v;},
  scrollIntoView:function(){},compareDocumentPosition:function(o){return o.pos>this.pos?4:2;}};}
var parent={replaceChild:function(a,b){log.push(["replace",a.id||a.src,b.id||b.src]);
  a.parentNode=parent;a.pos=b.pos;b.parentNode=null;kids[kids.indexOf(b)]=a;}};
var kids=%(frames)s.map(function(s,i){var f=node(s,%(width)s,i*10);f.parentNode=parent;return f;});
global.location={href:"https://courses.mentorshipu.com/learn/x/y/z"};
global.window={};
global.document={head:{appendChild:function(s){appended=s;}},
  createElement:function(t){var n=node("",null,0);n.tag=t;return n;},
  addEventListener:function(t,f){handler=f;},
  querySelectorAll:function(sel){return kids.filter(function(k){
    if(k.tag==="div")return sel.indexOf("[data-mu-rumble]")>=0;
    return /rumble\.com\/embed\//i.test(k.src)||(sel.indexOf("youtube")>=0&&/youtube/i.test(k.src));});}};
function mkapi(name){return {name:name,paused:true,time:0,drop:%(drop_seeks)s,calls:[],
  play:function(){this.calls.push("play");if(%(play_starts)s)this.paused=false;},
  setCurrentTime:function(n){this.calls.push(["seek",n]);if(this.drop>0)this.drop--;else this.time=n;},
  getCurrentTime:function(){return this.time;},
  getPaused:function(){return this.paused;}};}
var apis=[];
%(js)s
%(before_load)s
if(appended){window.Rumble=%(rumble)s;appended.onload();}
kids.forEach(function(d,i){if(d.tag!=="div")return;var big={offsetParent:%(big_visible)s,
  click:function(){var a=d.__muApi;a.calls.push("big");if(%(big_starts_after)s>=0)
    setTimeout(function(){a.paused=false;},%(big_starts_after)s);}};
  d.querySelector=function(s){return s===".bigPlayUI"?big:null;};});
function click(href,pos){handler({target:{closest:function(s){return s==='a[href^="#t="]'?
  {getAttribute:function(){return href;},pos:pos===undefined?999:pos}:null;}},preventDefault:function(){}});}
%(clicks)s
console.log(JSON.stringify({appended:appended&&appended.src, rumble:rumbleCalls,
  kids:kids.map(function(k){return k.tag==="div"?{div:k.attrs["data-mu-rumble"],width:k.style.width}:{src:k.src};}),
  api:apis.map(function(a){return a.calls;})}));
"""

_RUMBLE_READY = ("function(cmd,o){rumbleCalls.push([cmd,o.video,o.div]);"
                 "var a=mkapi(o.video);apis.push(a);o.api(a);}")
_RUMBLE_SILENT = "function(cmd,o){rumbleCalls.push([cmd,o.video,o.div]);}"
_RUMBLE_THROWS = "function(cmd,o){throw new Error('x');}"


def _run_rumble(frames, rumble=_RUMBLE_READY, clicks="", before_load="", big_visible="{}",
                play_starts="false", big_starts_after=300, drop_seeks=0, width='"640"'):
    import courses_blueprint as cb
    _need_node()
    js = cb._CHAPTER_JS.replace("<script>", "").replace("</script>", "")
    src = _RUMBLE_HARNESS % {"frames": json.dumps(frames), "js": js, "rumble": rumble,
                             "clicks": clicks, "before_load": before_load,
                             "big_visible": big_visible, "play_starts": play_starts,
                             "big_starts_after": big_starts_after, "drop_seeks": drop_seeks,
                             "width": width}
    out = subprocess.run(["node", "-e", src], capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


RUMBLE = "https://rumble.com/embed/v7gqoze/?pub=4"
RUMBLE2 = "https://rumble.com/embed/v1abc/"


def test_a_rumble_frame_becomes_rumbles_player():
    r = _run_rumble([RUMBLE])
    assert r["appended"] == "https://rumble.com/embedJS/u4"
    assert r["rumble"] == [["play", "v7gqoze", "mu-rplay-0"]]
    assert r["kids"] == [{"div": "v7gqoze", "width": "640px"}]


def test_a_cold_click_presses_play_then_seeks():
    r = _run_rumble([RUMBLE], clicks='click("#t=451");advance(2000);')
    assert r["api"] == [["big", ["seek", 451], "play"]]
    assert r["kids"] == [{"div": "v7gqoze", "width": "640px"}]


def test_a_hidden_play_button_falls_back_to_api_play():
    r = _run_rumble([RUMBLE], big_visible="null", play_starts="true",
                    clicks='click("#t=60");advance(2000);')
    assert r["api"] == [["play", ["seek", 60], "play"]]


def test_after_it_has_played_a_click_seeks_directly():
    r = _run_rumble([RUMBLE], clicks='click("#t=451");advance(2000);click("#t=830");advance(2000);')
    assert r["api"] == [["big", ["seek", 451], "play", ["seek", 830], "play"]]


def test_a_seek_an_ad_swallows_is_retried_until_it_lands():
    # Review round 1: a preroll may report playing and drop the first seeks.
    r = _run_rumble([RUMBLE], drop_seeks=2, clicks='click("#t=451");advance(5000);')
    assert r["api"] == [["big", ["seek", 451], "play", ["seek", 451], ["seek", 451]]]
    assert r["kids"] == [{"div": "v7gqoze", "width": "640px"}]


def test_a_seek_that_never_lands_keeps_the_playing_player():
    # Review round 3: a long preroll, or a chapter past the end, must not bring back the
    # cover. Landing gives up after 20 s and leaves the player playing.
    r = _run_rumble([RUMBLE], drop_seeks=999, clicks='click("#t=451");advance(60000);')
    assert r["kids"] == [{"div": "v7gqoze", "width": "640px"}]
    assert r["api"][0].count(["seek", 451]) == 40


def test_a_pause_during_landing_stops_it():
    # Review round 3: the member's pause is not overridden.
    r = _run_rumble([RUMBLE], drop_seeks=999, clicks='click("#t=451");advance(1200);'
                    'apis[0].paused=true;advance(10000);')
    assert r["api"][0].count(["seek", 451]) == 2
    assert r["api"][0].count("play") == 1


def test_a_later_click_restarts_the_landing_window():
    r = _run_rumble([RUMBLE], drop_seeks=999, clicks='click("#t=451");advance(18000);'
                    'click("#t=830");advance(15000);')
    assert r["api"][0].count(["seek", 830]) >= 30


def test_a_second_click_during_a_cold_start_keeps_one_poll_and_the_latest_second():
    # Review rounds 1 and 2: no second play press, and the last chapter wins.
    r = _run_rumble([RUMBLE], clicks='click("#t=451");advance(100);click("#t=830");advance(2000);')
    assert r["api"] == [["big", ["seek", 830], "play"]]


def test_a_second_click_on_a_player_that_never_starts_restores_the_latest_second():
    r = _run_rumble([RUMBLE], big_starts_after=-1,
                    clicks='click("#t=451");advance(5000);click("#t=830");advance(30000);')
    assert r["kids"] == [{"src": RUMBLE + "&start=830"}]


def test_a_player_that_never_starts_goes_back_to_the_frame_at_that_second():
    r = _run_rumble([RUMBLE], big_starts_after=-1, clicks='click("#t=90");advance(19000);')
    assert r["kids"] == [{"div": "v7gqoze", "width": "640px"}]
    r = _run_rumble([RUMBLE], big_starts_after=-1, clicks='click("#t=90");advance(21000);')
    assert r["kids"] == [{"src": RUMBLE + "&start=90"}]


def test_a_seek_that_throws_gives_back_the_frame():
    # Review round 2: an exception after playing must not leave a dead player.
    r = _run_rumble([RUMBLE], clicks='click("#t=451");advance(2000);'
                    'apis[0].setCurrentTime=function(){throw new Error("x");};'
                    'click("#t=830");')
    assert r["kids"] == [{"src": RUMBLE + "&start=830"}]


def test_a_player_that_never_loads_gives_back_the_frame():
    r = _run_rumble([RUMBLE], rumble=_RUMBLE_SILENT, clicks="advance(7000);")
    assert r["kids"] == [{"div": "v7gqoze", "width": "640px"}]
    r = _run_rumble([RUMBLE], rumble=_RUMBLE_SILENT, clicks='advance(8000);click("#t=5");')
    assert r["kids"] == [{"src": RUMBLE + "&start=5"}]


def test_a_click_before_the_player_is_ready_restores_the_frame_at_that_second():
    r = _run_rumble([RUMBLE], rumble=_RUMBLE_SILENT, clicks='click("#t=7");')
    assert r["kids"] == [{"src": RUMBLE + "&start=7"}]


def test_a_click_while_the_script_downloads_is_kept():
    # Review round 2: the frame reloaded at ?start= is not swapped when the script lands.
    r = _run_rumble([RUMBLE], before_load='click("#t=12");')
    assert r["kids"] == [{"src": RUMBLE + "&start=12"}]
    assert r["rumble"] == []


def test_rumble_throwing_leaves_the_frame():
    r = _run_rumble([RUMBLE], rumble=_RUMBLE_THROWS)
    assert r["kids"] == [{"src": RUMBLE}]


def test_no_rumble_frame_loads_no_rumble_script():
    r = _run_rumble(["https://www.youtube.com/embed/abc?enablejsapi=1"])
    assert r["appended"] is None and r["rumble"] == []


def test_a_rumble_failure_without_the_script_leaves_the_frame():
    r = _run_rumble([RUMBLE], rumble="undefined")
    assert r["kids"] == [{"src": RUMBLE}]


def test_two_rumble_videos_each_follow_their_own_chapters():
    # Review round 1: links control the nearest player above them.
    r = _run_rumble([RUMBLE, RUMBLE2], clicks='click("#t=30",5);advance(2000);'
                    'click("#t=40",15);advance(2000);')
    assert r["rumble"] == [["play", "v7gqoze", "mu-rplay-0"], ["play", "v1abc", "mu-rplay-1"]]
    assert r["api"] == [["big", ["seek", 30], "play"], ["big", ["seek", 40], "play"]]


@pytest.mark.parametrize("width,expect", [('"640"', "640px"), ('"100%"', "100%"),
                                          ("null", "100%")])
def test_the_player_width_follows_a_pixel_width_only(width, expect):
    assert _run_rumble([RUMBLE], width=width)["kids"][0]["width"] == expect


def test_an_embed_without_a_trailing_slash_still_swaps():
    r = _run_rumble(["https://rumble.com/embed/v7gqoze"])
    assert r["rumble"] == [["play", "v7gqoze", "mu-rplay-0"]]
