"""The Clinical Theory of Everything hub tile (dark: PORTAL_BRAIN_TILE_ENABLED).

The tile links out to Glen's published TheBrain view. It carries no client data, so
the whole surface is a flag plus a URL: what needs pinning is that the flag actually
gates it, and that a missing URL cannot ship an empty link.
"""
import re

from dashboard import portal_view as pv

PORTAL = "static/client-portal.html"


def test_block_ships_dark_by_default():
    """The flag defaults to off in the view signature, so a deploy alone never
    turns the tile on."""
    import inspect
    sig = inspect.signature(pv.get_portal_view)
    assert sig.parameters["brain_enabled"].default is False
    assert sig.parameters["brain_url"].default == ""


def test_block_needs_both_the_flag_and_a_url():
    """A flag flipped on with no URL configured must not ship an empty link."""
    assert pv._brain_block(True, "https://bra.in/6j852k") == {
        "enabled": True, "url": "https://bra.in/6j852k"}
    assert pv._brain_block(True, "") == {"enabled": False, "url": ""}
    assert pv._brain_block(True, "   ") == {"enabled": False, "url": ""}
    assert pv._brain_block(False, "https://bra.in/6j852k") == {
        "enabled": False, "url": "https://bra.in/6j852k"}


def test_tile_renders_only_when_enabled():
    s = open(PORTAL).read()
    assert "v.brain && v.brain.enabled" in s, "tile is not gated on the payload flag"
    # the gate must produce an EMPTY list when off, so the off-state grid is unchanged
    m = re.search(r"\(v && v\.brain && v\.brain\.enabled\)\s*\?\s*\[(.+?)\]\s*:\s*\[\]",
                  s, re.S)
    assert m, "the off-state branch must contribute no tile"


def test_external_tile_escapes_and_is_safe():
    s = open(PORTAL).read()
    m = re.search(r"const extTile = .*?;", s, re.S)
    assert m, "extTile helper missing"
    frag = m.group(0)
    assert "esc(href)" in frag, "href must be escaped"
    assert "esc(name)" in frag and "esc(desc)" in frag
    # opening a third-party site in a new tab without noopener leaks window.opener
    assert 'target="_blank"' in frag and 'rel="noopener"' in frag


def test_anchor_tile_is_not_underlined():
    """The tile shares .hub-tile with <button> siblings; an <a> would otherwise
    render underlined and read as a different kind of thing in the grid."""
    s = open(PORTAL).read()
    css = re.search(r"\.hub-tile \{[^}]+\}", s).group(0)
    assert "text-decoration:none" in css
