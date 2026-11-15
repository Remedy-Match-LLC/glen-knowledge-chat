"""A chat sent from the embedded widget is logged with the page that holds it.

10 Nov 2026: Glen approved the widget on refreshingvision.com. Every embedded chat used to
log the iframe's own address (illtowell.com/embed), so no site could be told apart."""
import os
import re

import app

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EMBED = "https://illtowell.com/embed?page=https%3A%2F%2Frefreshingvision.com%2F"


def _read(name):
    with open(os.path.join(ROOT, "static", name), encoding="utf-8") as f:
        return f.read()


def test_an_embedded_chat_logs_its_host_page():
    assert (app._chat_referer(EMBED, "https://refreshingvision.com/contact")
            == "embed https://refreshingvision.com/contact")


def test_only_the_embed_page_may_claim_a_host():
    assert (app._chat_referer("https://illtowell.com/", "https://refreshingvision.com/")
            == "https://illtowell.com/")
    assert app._chat_referer("", "https://refreshingvision.com/") == ""


def test_a_malformed_page_falls_back_to_the_header():
    for bad in ("javascript:alert(1)", "https://x.com/a b", "https://x.com/?q=1",
                "https://x.com/#f", "refreshingvision.com", None, 42):
        assert app._chat_referer(EMBED, bad) == "https://illtowell.com/embed"


def test_the_page_is_capped():
    out = app._chat_referer(EMBED, "https://refreshingvision.com/" + "a" * 600)
    assert len(out) <= len("embed ") + 300


def test_chat_logs_through_the_tagger():
    import inspect
    src = inspect.getsource(app.chat)
    assert re.search(r'referer\s*=\s*_chat_referer\(request\.headers\.get\("Referer"', src)


def test_widget_passes_origin_and_path_to_both_iframes():
    js = _read("widget.js")
    assert "window.location.origin + window.location.pathname" in js
    assert js.count("src = embedSrc") == 2
    assert "serverOrigin + '/embed';" not in js


def test_embed_sends_the_page_with_each_chat():
    html = _read("embed.html")
    assert "get('page')" in html
    assert re.search(r"embed_page:\s*EMBED_PAGE", html)
