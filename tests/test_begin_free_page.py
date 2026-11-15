# tests/test_begin_free_page.py
"""The free-membership page at /begin/free.

The copy was approved word for word by Glen in the marketing tab on 13 Nov 2026:
marketing/04 Copy/free-membership-page-2026-11/page-copy-v4.md. Marketing pinned the
text below its '---' line with SHA-256 ef5ce92f...fed9. This test rebuilds that
markdown from the served page, element by element, and checks the hash. Any changed,
added, dropped or merged sentence, heading or button fails it.
"""
import hashlib
import re
from html.parser import HTMLParser
from pathlib import Path

import pytest

APPROVED_SHA256 = "ef5ce92f780de84af77cd634d41d9b29b23d1c8020887f170a054d0eefb4fed9"
PAGE = Path(__file__).resolve().parent.parent / "static" / "begin-free.html"


class _Copy(HTMLParser):
    """Collects the visible copy inside <main id="free-copy"> as markdown blocks."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.depth = 0          # >0 while inside main#free-copy
        self.tag = None         # the text-bearing tag currently open
        self.buf = []
        self.blocks = []        # (kind, text)
        self.stray = []         # visible text outside a copy block, or hidden markup

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "main" and a.get("id") == "free-copy":
            self.depth = 1
            return
        if not self.depth:
            return
        if "hidden" in a or "style" in a or "aria-hidden" in a:
            self.stray.append(f"<{tag}> hides or restyles copy")
        if tag in ("h1", "h2", "h3", "p"):
            self.tag, self.buf = tag, []
        elif tag == "button" and "join-btn" in (a.get("class") or ""):
            self.tag, self.buf = "button", []
        elif tag in ("script", "style"):
            raise AssertionError(f"<{tag}> inside the copy block")

    def handle_endtag(self, tag):
        if tag == "main" and self.depth:
            self.depth = 0
        if self.depth and self.tag == tag:
            self.blocks.append((tag, "".join(self.buf)))
            self.tag = None

    def handle_data(self, data):
        if self.depth and self.tag:
            self.buf.append(data)
        elif self.depth and data.strip():
            self.stray.append(data.strip())


def _blocks(html):
    p = _Copy()
    p.feed(html)
    assert not p.stray, p.stray
    return p.blocks


def _as_markdown(blocks):
    """The copy file's own layout: '## Page title' then the h1, '## Opening' before
    the first paragraph, a [Button: ...] line per join button."""
    out = []
    for i, (kind, text) in enumerate(blocks):
        if kind == "h1":
            out += ["## Page title", text, "## Opening"]
        elif kind == "h2":
            out.append("## " + text)
        elif kind == "h3":
            out.append("### " + text)
        elif kind == "button":
            out.append(f"[Button: {text}]")
        else:
            out.append(text)
    return "\n" + "\n\n".join(out) + "\n"


@pytest.fixture(scope="module")
def html():
    """The page as the route serves it. The app adds its theme scripts to the head of
    every page, so the served page is the file on disk plus that one line."""
    import app as appmod
    r = appmod.app.test_client().get("/begin/free")
    assert r.status_code == 200
    served = r.get_data(as_text=True)
    without_theme = re.sub(r'<script id="theme-assets".*?</script><script src="/static/theme-mode.js"></script>\n?',
                           "", served, count=1)
    assert without_theme == PAGE.read_text(encoding="utf-8"), "the route does not serve static/begin-free.html"
    return served


def test_served_page_matches_the_approved_copy_word_for_word(html):
    md = _as_markdown(_blocks(html))
    assert hashlib.sha256(md.encode("utf-8")).hexdigest() == APPROVED_SHA256, md


def test_the_hash_check_catches_a_one_word_change(html):
    changed = html.replace("about ten seconds", "about 10 seconds", 1)
    assert changed != html
    md = _as_markdown(_blocks(changed))
    assert hashlib.sha256(md.encode("utf-8")).hexdigest() != APPROVED_SHA256


def test_two_sentences_in_one_paragraph_fail_the_check(html):
    merged = html.replace("<p>It takes a few minutes.</p>\n        <p>", "<p>It takes a few minutes. ", 1)
    assert merged != html
    md = _as_markdown(_blocks(merged))
    assert hashlib.sha256(md.encode("utf-8")).hexdigest() != APPROVED_SHA256


def test_unapproved_or_hidden_text_in_the_copy_block_fails(html):
    for bad in (html.replace('<main id="free-copy">', '<main id="free-copy"><div>Extra terms</div>', 1),
                html.replace("<p>Each email address", "<p hidden>Each email address", 1)):
        assert bad != html
        with pytest.raises(AssertionError):
            _blocks(bad)


def test_both_join_buttons_exist_and_open_the_send_my_link_form(html):
    buttons = [t for k, t in _blocks(html) if k == "button"]
    assert buttons == ["Become a free member", "Become a free member"]
    assert html.count('class="join-btn"') == 2
    assert 'id="oasis-form"' in html and ">Send my link<" in html
    assert "querySelectorAll('.join-btn')" in html


def test_the_only_endpoint_is_the_send_my_link_request(html):
    """'How to join' describes the emailed-link path only. Other sign-up paths take
    the terms with the email, which would make that copy false."""
    script = html.split("<script>", 1)[1]
    # One read of the on/off switch, and one POST, to the Send my link endpoint.
    assert sorted(re.findall(r"fetch\(([^,)]*)", script)) == [
        "'/api/healing-oasis/request'", "'/api/healing-oasis/status'"]
    assert re.search(r"fetch\('/api/healing-oasis/status'\)", script)
    assert "XMLHttpRequest" not in html and "sendBeacon" not in html
    forms = re.findall(r"<form\b[^>]*>", html)
    assert forms == ['<form id="oasis-form" novalidate>'], forms     # no action, no method
    for other in ("/begin/doorway/opt-in", "/begin/quiz/opt-in", "/begin/fireside/opt-in",
                  "/api/begin/", "/checkout", "/family-plan"):
        assert other not in html


def test_house_style(html):
    assert "—" not in html and "&mdash;" not in html
    assert "uppercase" not in html.lower()
    assert re.search(r"\$\s?\d", html) is None      # no prices on this page


def test_route_serves_the_page_uncached():
    import app as appmod
    r = appmod.app.test_client().get("/begin/free")
    assert r.status_code == 200
    assert r.mimetype == "text/html"
    assert "no-store" in r.headers.get("Cache-Control", "")
    assert b"Become a free member of Remedy Match" in r.data
    assert appmod.app.test_client().get("/begin").status_code == 200


class _Outside(HTMLParser):
    """Visible text in <body> outside the copy block and the sign-up dialog."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack = []
        self.text = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        skip = (tag in ("script", "style") or a.get("id") in ("free-copy", "oasis-modal")
                or (self.stack and self.stack[-1]))
        if tag not in ("meta", "link", "input", "br", "img"):
            self.stack.append(bool(skip))

    def handle_endtag(self, tag):
        if tag not in ("meta", "link", "input", "br", "img") and self.stack:
            self.stack.pop()

    def handle_data(self, data):
        if data.strip() and not (self.stack and self.stack[-1]):
            self.text.append(" ".join(data.split()))


def test_no_visible_text_outside_the_copy_and_the_form(html):
    body = html.split("<body>", 1)[1]
    p = _Outside()
    p.feed(body)
    # The brand line is the /begin page's own header, kept for the shared look.
    assert p.text == ["Remedy Match · Healing Oasis"], p.text


def test_no_stylesheet_rule_hides_copy(html):
    css = html.split("<style>", 1)[1].split("</style>", 1)[0]
    hiding = re.findall(r"([^{}]+)\{[^}]*(?:display\s*:\s*none|visibility\s*:\s*hidden|font-size\s*:\s*0)", css)
    assert [s.strip() for s in hiding] == [".oasis-overlay[hidden]"], hiding


def test_no_other_way_out_of_the_page(html):
    assert re.search(r"\son[a-z]+\s*=", html, re.I) is None          # no inline handlers
    assert re.findall(r"<script\b[^>]*\bsrc=\"([^\"]+)\"", html.split("<script id=\"theme-assets\"")[0]) \
        == ["/static/theme-toggle.js"]
    assert re.findall(r"<a\b", html) == []                              # no links at all
    assert "location" not in html.split("<script>", 1)[1]


def test_form_fields_are_validated_by_the_browser(html):
    assert re.search(r'<input id="oasis-name"[^>]*\brequired\b', html)
    assert re.search(r'<input id="oasis-email"[^>]*type="email"[^>]*\brequired\b', html)
    assert 'role="dialog" aria-modal="true" aria-labelledby="oasis-title"' in html


def test_route_is_get_only():
    import app as appmod
    assert appmod.app.test_client().post("/begin/free").status_code == 405
