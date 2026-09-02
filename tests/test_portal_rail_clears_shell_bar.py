"""The portal rail must start below the fixed shell bar.

The bar is fixed at top:0, 52px tall, z-index 9999. The rail was also top:0 at
z-index 40, so its entire first item (Home, "Where you are and what is next")
sat underneath it -- measured live 2026-09-02: first item spanned y=8..48 against
a 52px bar. Opening the sidebar showed the description's tail and nothing else.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PAGE = (ROOT / "static" / "client-portal.html").read_text()
SHELL = (ROOT / "static" / "shell.css").read_text()


def _rail_block():
    i = PAGE.index(".portal-rail{")
    return PAGE[i:PAGE.index("}", i)]


def test_the_rail_starts_below_the_bar_not_at_zero():
    block = _rail_block()
    assert "top:0" not in block.replace(" ", ""), "the rail still starts at top:0"
    assert "var(--jshell-h" in block, "read the bar height from its variable"


def test_it_reads_the_bars_own_height_rather_than_repeating_it():
    """A hardcoded 52px silently desyncs if the bar is ever resized."""
    m = re.search(r"--jshell-h:\s*([0-9]+)px", SHELL)
    assert m, "shell.css no longer defines --jshell-h"
    block = _rail_block()
    fallback = re.search(r"var\(--jshell-h,\s*([0-9]+)px\)", block)
    assert fallback, "keep a literal fallback for a page that loads no shell.css"
    assert fallback.group(1) == m.group(1), (
        "the fallback (%spx) disagrees with shell.css (%spx)" % (fallback.group(1), m.group(1)))


def test_the_mobile_drawer_inherits_the_offset():
    """The narrow-screen rule overrides position/width/transform only; if it ever
    sets its own top:0 the drawer would slide back under the bar."""
    i = PAGE.index(".portal-rail{", PAGE.index("@media"))
    mobile = PAGE[i:PAGE.index("}", i)]
    assert "top:0" not in mobile.replace(" ", "")
