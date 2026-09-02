"""The Ask & Guide pill must not cover the Justus launcher.

Both pin themselves to the bottom-right: #jw-open-btn at bottom:20 right:20
(z-index 8999) and .ag-pill at bottom:16 right:16 (z-index 99998). Measured live
on Sell > New Order 2026-09-02: they overlapped by 118x29px and the pill, sitting
90,000 higher in the stack, hid the purple button entirely.

ask-guide.js is injected on EVERY console page, so the offset is applied only on
pages that actually carry the Justus widget -- and keyed off its script tag rather
than the button, since the widget is deferred and the button may not exist yet.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AG = (ROOT / "static" / "ask-guide.js").read_text()
JW = (ROOT / "static" / "justus-widget.js").read_text()


def _px(pattern, text):
    m = re.search(pattern, text)
    return int(m.group(1)) if m else None


def test_the_pill_clears_the_justus_button_when_that_widget_is_present():
    assert "justus-widget" in AG, "ask-guide never checks for the Justus widget"
    raised = _px(r"\.ag-pill\{bottom:(\d+)px\}", AG)
    assert raised is not None, "no raised bottom for the pill"
    jw_bottom = _px(r"#jw-open-btn\{[^}]*bottom:(\d+)px", JW)
    assert jw_bottom is not None, "justus-widget no longer pins bottom in px"
    # The Justus button is ~35px tall; the pill must start above its top edge.
    assert raised >= jw_bottom + 35, (
        "pill at %dpx does not clear a button at %dpx + its height" % (raised, jw_bottom))


def test_the_drawer_clears_the_raised_pill():
    pill = _px(r"\.ag-pill\{bottom:(\d+)px\}", AG)
    drawer = _px(r"\.ag-drawer\{bottom:(\d+)px\}", AG)
    assert drawer and drawer > pill, "the drawer would open behind the raised pill"


def test_it_keys_off_the_script_tag_not_the_button():
    """justus-widget.js is deferred; querying for #jw-open-btn would race it.

    Checks the guard EXPRESSION, not merely that the name appears somewhere -- the
    first mention of "justus-widget" in this file is a comment, which an
    occurrence-based check happily accepted.
    """
    guard = re.search(r"document\.querySelector\((['\"])script\[src\*=[\"']justus-widget",
                      AG)
    assert guard, "no script-tag guard; a deferred button lookup would race"
    code = re.sub(r"//[^\n]*", "", AG)          # strip comments, then re-check
    assert "justus-widget" in code, "the only mention is in a comment"
    assert "getElementById('jw-open-btn')" not in code
    assert 'getElementById("jw-open-btn")' not in code


def test_pages_without_justus_keep_the_original_corner():
    """The pill is injected on every console page; only Justus pages shift."""
    assert ".ag-pill{position:fixed;right:16px;bottom:16px" in AG
    assert "if (document.querySelector('script[src*=\"justus-widget\"]'))" in AG
