"""Hovering a collapsed rail icon must name its topic.

The rail collapses to 52px and hides .rail-text (opacity:0), leaving seven
unlabelled glyphs. Each button now carries title="<topic>" so a mouse-over says
"Scans & Reports". Executed, not grepped: a comment naming the topics would
satisfy a source regex.
"""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
JS = ROOT / "static" / "js" / "portal-shell.js"
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="node not available")


def _render(open_=False):
    src = ("const m={exports:{}};const module=m;const window=undefined;"
           + JS.read_text()
           + ";console.log(JSON.stringify(m.exports.renderRail('home',{open:%s})));"
           % ("true" if open_ else "false"))
    out = subprocess.run([NODE, "-e", src], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


def _doors():
    src = ("const m={exports:{}};const module=m;const window=undefined;"
           + JS.read_text() + ";console.log(JSON.stringify(m.exports.DOORS));")
    out = subprocess.run([NODE, "-e", src], capture_output=True, text=True, timeout=30)
    return json.loads(out.stdout.strip().splitlines()[-1])


def test_every_topic_icon_names_itself_on_hover():
    html = _render()
    titles = re.findall(r'class="rail-item[^"]*" data-door="([^"]+)" title="([^"]*)"', html)
    got = dict(titles)
    for d in _doors():
        assert d["key"] in got, "no hover title for the %r icon" % d["key"]
        assert got[d["key"]], "empty title for %r" % d["key"]


def test_the_title_is_the_topic_name_not_the_description():
    """Glen asked for the name, e.g. "Scans & Reports" -- not the long blurb."""
    html = _render()
    got = dict(re.findall(r'data-door="([^"]+)" title="([^"]*)"', html))
    for d in _doors():
        assert got[d["key"]] == d["label"].replace("&", "&amp;"), (
            "%r titled %r, expected the label %r" % (d["key"], got[d["key"]], d["label"]))
        assert d["desc"][:12].replace("&", "&amp;") not in got[d["key"]]


def test_an_ampersand_in_a_topic_name_is_escaped():
    """"Scans & Reports" and "Learn & Ask" must not break the attribute."""
    html = _render()
    assert 'title="Scans &amp; Reports"' in html
    assert 'title="Learn &amp; Ask"' in html
    assert 'title="Scans & Reports"' not in html
