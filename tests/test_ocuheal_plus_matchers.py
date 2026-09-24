"""Every name matcher tells "OcuHeal+ Eye Drops" from "OcuHeal Eye Drops".

The name cannot change: FileMaker 1200's exact name is the invoice key. Production's
review rounds (2026-09-24) found four matchers that dropped the "+" and so confused
the two, the new one carrying 10% DMSO at the same price. The test that counts is
the ORIGINAL name selecting the original: before the fix, a tie could go either way.
The cart matcher's tests are in test_chat_cart.py.
"""
import os
import sqlite3

import pytest

os.environ.setdefault("OPENAI_API_KEY", "sk-fake")
os.environ.setdefault("PINECONE_API_KEY", "pcsk_fake")

from dashboard import biofield_authoring as ba  # noqa: E402
from dashboard import biofield_narrative as bn  # noqa: E402

NAMES = ["OcuHeal Eye Drops", "OcuHeal+ Eye Drops", "Air & Surface PRO+", "Brain Boost"]


# ── dictation: resolve_remedy_name ───────────────────────────────────────────

@pytest.mark.parametrize("names", [NAMES, NAMES[::-1]], ids=["order", "reversed"])
@pytest.mark.parametrize("spoken, want", [
    ("ocuheal eye drops", "OcuHeal Eye Drops"),
    ("ocuheal plus eye drops", "OcuHeal+ Eye Drops"),
    ("OcuHeal+ Eye Drops", "OcuHeal+ Eye Drops"),
    ("ocuheal eye drop", "OcuHeal Eye Drops"),
    ("air and surface pro plus", "Air & Surface PRO+"),
])
def test_dictation_scores_plus_as_a_word(spoken, want, names):
    assert ba._best_match(spoken, names, 0.82) == want


def test_resolve_remedy_name_end_to_end(monkeypatch):
    """The real resolver, with the FileMaker snapshot as its pool."""
    cx = sqlite3.connect(":memory:")
    cx.execute("CREATE TABLE fmp_snap_products (product_name TEXT)")
    cx.executemany("INSERT INTO fmp_snap_products VALUES (?)", [(n,) for n in NAMES])
    monkeypatch.setattr(ba, "_catalog_names_for_pool", lambda: [])
    monkeypatch.setattr(ba, "_superseded_name_map", lambda: {})
    monkeypatch.setattr(ba, "_catalog_alias_map", lambda: {})
    monkeypatch.setattr(ba, "_sellable_names", lambda names: names)
    assert ba.resolve_remedy_name(cx, "ocuheal eye drops") == "OcuHeal Eye Drops"
    assert ba.resolve_remedy_name(cx, "ocuheal plus eye drops") == "OcuHeal+ Eye Drops"


# ── chat links and price hints: _alias_catalog_slug ──────────────────────────

@pytest.fixture
def appmod(monkeypatch):
    import app
    monkeypatch.setattr(app, "_ALIAS_SLUG_CACHE", None)
    return app


@pytest.mark.parametrize("name, slug", [
    ("OcuHeal Eye Drops", "ocuheal-eye-drops"),
    ("OcuHeal+ Eye Drops", "ocuheal-plus-eye-drops"),
    ("Air & Surface PRO", "air-surface-pro-plus"),
    ("Air & Surface PRO+", "air-surface-pro-plus"),
])
def test_alias_keys_keep_the_two_apart(appmod, name, slug):
    assert appmod._alias_catalog_slug(name, {})[0] == slug


def test_the_alias_key_keeps_plus(appmod):
    assert appmod._alias_key("OcuHeal+ Eye Drops") != appmod._alias_key("OcuHeal Eye Drops")


# ── Biofield narrative: _catalog_product ─────────────────────────────────────

@pytest.mark.parametrize("name", ["OcuHeal Eye Drops", "OcuHeal+ Eye Drops"])
def test_the_narrative_reads_the_right_product(name):
    assert bn._catalog_product(name).get("name") == name
