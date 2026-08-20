# tests/test_no_legacy_storefront_links.py
"""No client-facing surface may send a buyer to the retired remedymatch.com
storefront.

The chat system prompt has said this in words for a while ("do not substitute a
remedymatch.com URL for a table entry ... clients have been unable to complete
checkout there"), but the deterministic UI still emitted three of them: the
"Formulations Matched to You" card/chip, and the Remedy Match card's search
fallback. On 2026-08-20 the storefront was serving a maintenance page, so those
were live dead ends. These tests keep the rule in the code, not just the prompt.

Also locks the hero name placeholders: an un-personalised page must not render
a dangling separator ("Welcome, there.") and a personalised one must not run the
name straight into the headline ("GlenYour body is always speaking.").
"""

import importlib
import re
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
STATIC = REPO / "static"


def _load_app():
    if str(REPO) not in sys.path:
        sys.path.insert(0, str(REPO))
    try:
        return importlib.import_module("app")
    except Exception as e:  # pragma: no cover - env without app deps
        pytest.skip(f"app module not importable in this env: {e}")


# ---------------------------------------------------------------------------
# The card catalog
# ---------------------------------------------------------------------------

def test_no_card_points_at_the_legacy_storefront():
    import begin_funnel as bf
    offenders = [k for k, c in bf.CARD_CATALOG.items()
                 if "remedymatch.com" in c["base_url"]]
    assert offenders == [], f"card(s) still linking to the retired store: {offenders}"


def test_product_card_resolves_internally_for_every_ref():
    import begin_funnel as bf
    for ref in ("", "jane", "remedy-match"):
        href = bf.card_href("product", ref)
        assert href.startswith("/"), href
        assert "remedymatch.com" not in href


def test_product_mission_chip_stays_on_site():
    """The chip a client actually taps, not just the card behind it."""
    import begin_funnel as bf
    st = {"travel_style": "mission", "awareness_stage": "product",
          "current_rung": "inquire", "unlocked_gates": []}
    chips = bf.next_step_chips(st, ref="jane", query_texts=["does EVOX help"])
    hrefs = [c.get("href") or "" for c in chips]
    assert any(h for h in hrefs), "mission path handed back no destination"
    for h in hrefs:
        assert "remedymatch.com" not in h


# ---------------------------------------------------------------------------
# The Remedy Match result card
# ---------------------------------------------------------------------------

def test_storefront_url_classifier():
    app = _load_app()
    assert app._is_legacy_storefront_url("https://remedymatch.com/remedies/x")
    assert app._is_legacy_storefront_url("http://RemedyMatch.com?controller=search")
    assert not app._is_legacy_storefront_url("")
    assert not app._is_legacy_storefront_url(None)
    assert not app._is_legacy_storefront_url("https://illtowell.com/begin/product/x")
    assert not app._is_legacy_storefront_url("https://amzn.to/abc")


def test_store_search_fallback_is_gone():
    app = _load_app()
    assert not hasattr(app, "_store_search_url"), (
        "the storefront search fallback is back; it sends a matched client to a "
        "store that cannot complete checkout")


def test_match_card_never_renders_a_search_link():
    html = (STATIC / "begin-match.html").read_text(encoding="utf-8")
    assert "search_url" not in html
    assert "searchUrl" not in html
    assert "remedymatch.com" not in html


# ---------------------------------------------------------------------------
# Hero name placeholders
# ---------------------------------------------------------------------------

def test_hero_has_no_dangling_name_placeholder():
    html = (STATIC / "begin.html").read_text(encoding="utf-8")
    # The literal that produced "Welcome, there. Let's begin."
    assert "Welcome, <span data-name>" not in html
    assert re.search(r"Welcome<span data-name-prefix>, </span><span data-name></span>", html), \
        "the welcome line no longer uses the hidden-until-known separator"


def test_name_separator_is_hidden_globally_not_only_in_the_hero():
    html = (STATIC / "begin.html").read_text(encoding="utf-8")
    assert re.search(r"^\s*\[data-name-prefix\] \{ display: none; \}", html, re.M), \
        "the separator must default to hidden wherever it is used"
    assert ".hero-head [data-name-prefix]" not in html


def test_headline_separator_follows_the_name():
    """Prefix-then-name rendered 'GlenYour body is always speaking.'"""
    html = (STATIC / "begin.html").read_text(encoding="utf-8")
    assert "<span data-name-prefix></span><span data-name></span>Your body" not in html
    assert "<span data-name></span><span data-name-prefix>. </span>Your body" in html
