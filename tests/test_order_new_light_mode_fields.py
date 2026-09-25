"""Sell: New Order fields stay readable in light mode.

Glen, 2026-09-24: in light mode the fields were black with dark text. op-nav's light
theme sets --text to #1E2A2A; the page hardcoded field background #0d1117. The field
colour is now a token the light theme overrides.
"""
import re
from pathlib import Path

SRC = (Path(__file__).resolve().parent.parent / "static" / "order-new.html").read_text()
NAV = (Path(__file__).resolve().parent.parent / "static" / "op-nav.js").read_text()


def _lum(hex_):
    r, g, b = (int(hex_[i:i + 2], 16) / 255 for i in (1, 3, 5))
    f = lambda c: c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)


def _contrast(a, b):
    la, lb = sorted((_lum(a), _lum(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def test_fields_use_the_field_token_not_a_hardcoded_colour():
    rule = re.search(r"input, select \{([^}]*)\}", SRC).group(1)
    assert "background:var(--field)" in rule.replace(" ", "")


def test_light_mode_field_and_text_are_readable():
    light_field = re.search(r':root\[data-theme="light"\]\s*\{\s*--field:(#[0-9A-Fa-f]{6})', SRC).group(1)
    light_text = re.search(r"--text:(#[0-9A-Fa-f]{6})", NAV[NAV.index(':root[data-theme="light"]{'):]).group(1)
    assert _contrast(light_field, light_text) >= 4.5, (light_field, light_text)


def test_dark_mode_is_unchanged():
    root = re.search(r":root \{([^}]*)\}", SRC).group(1)
    assert "--field:#0d1117" in root.replace(" ", "")
    assert _contrast("#0d1117", re.search(r"--text:(#[0-9a-fA-F]{6})", root).group(1)) >= 4.5
