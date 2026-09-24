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


# ── glossary "What may help" links: clinical_glossary ────────────────────────

def test_the_glossary_links_each_name_to_its_own_product():
    import json as _json
    from dashboard import clinical_glossary as cg
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    products = _json.load(open(os.path.join(root, "data", "products.json")))["products"]
    idx = cg.product_name_index(products)
    assert cg.remedy_product_slug("OcuHeal Eye Drops", idx) == "ocuheal-eye-drops"
    assert cg.remedy_product_slug("OcuHeal+ Eye Drops", idx) == "ocuheal-plus-eye-drops"
    assert cg.remedy_product_slug("Air & Surface PRO", idx) == "air-surface-pro-plus"


def test_a_plus_title_whose_key_never_had_plus_still_links(appmod, monkeypatch):
    """Round 1: the key with "+" dropped is the fallback lookup."""
    monkeypatch.setattr(appmod, "_ALIAS_SLUG_CACHE", {"fooeyedrops": "foo-eye-drops"})
    monkeypatch.setattr(appmod, "_PRODUCTS", {"products": {"foo-eye-drops": {"name": "Foo Eye Drops"}}})
    assert appmod._alias_catalog_slug("Foo+ Eye Drops", {})[0] == "foo-eye-drops"


# ── chat link table backstop: _catalog_link_matches ──────────────────────────

@pytest.mark.parametrize("text, want", [
    ("Try OcuHeal+ Eye Drops twice a day", "ocuheal-plus-eye-drops"),
    ("Try OcuHeal Plus Eye Drops twice a day", "ocuheal-plus-eye-drops"),
])
def test_the_link_table_sends_plus_spelled_out_to_ocuheal_plus(appmod, text, want):
    """Round 2: "OcuHeal Plus Eye Drops" linked the original's page."""
    found = appmod._catalog_link_matches(text, {"OcuHeal Eye Drops": {}})
    assert list(found.values()) == [appmod._catalog_page_url(want)], found


def test_the_link_table_never_links_the_original_mention_to_ocuheal_plus(appmod):
    found = appmod._catalog_link_matches("Try OcuHeal Eye Drops twice a day",
                                         {"OcuHeal Eye Drops": {}})
    assert appmod._catalog_page_url("ocuheal-plus-eye-drops") not in found.values(), found


def test_the_word_plus_between_two_products_keeps_both(appmod):
    """Only a catalog "+" token is joined; "plus" as "and" links as "and" does."""
    # Round 3: "Harmony Laser" is matched with a gap, not by the squashed name, so
    # joining "laser plus" into "laser+" would lose it. The catalog check prevents that.
    t = "Take Harmony Soft Laser {} Brain Boost daily"
    with_plus = appmod._catalog_link_matches(t.format("plus"), {})
    assert with_plus == appmod._catalog_link_matches(t.format("and"), {})
    assert "Harmony Laser" in with_plus, with_plus


# ── saved remedies per condition: biofield_clinical_checklist ────────────────

def _checklist_db():
    from dashboard import biofield_clinical_checklist as ck
    cx = sqlite3.connect(":memory:")
    ck.ensure_catalog_schema(cx)
    return ck, cx


def _rows(cx):
    return sorted(cx.execute("SELECT remedy, hidden FROM biofield_clinical_catalog").fetchall())


def test_saving_ocuheal_plus_keeps_the_ocuheal_chip():
    """Round 3: the second save renamed the first row to OcuHeal+."""
    ck, cx = _checklist_db()
    ck.remember_remedies(cx, "Cataract", ["OcuHeal Eye Drops"])
    ck.remember_remedies(cx, "Cataract", ["OcuHeal+ Eye Drops"])
    assert _rows(cx) == [("OcuHeal Eye Drops", 0), ("OcuHeal+ Eye Drops", 0)]


def test_hiding_one_never_hides_the_other():
    ck, cx = _checklist_db()
    ck.remember_remedies(cx, "Cataract", ["OcuHeal Eye Drops", "OcuHeal+ Eye Drops"])
    ck.forget_remedy(cx, "Cataract", "OcuHeal Eye Drops")
    assert _rows(cx) == [("OcuHeal Eye Drops", 1), ("OcuHeal+ Eye Drops", 0)]


def test_a_row_saved_under_the_old_key_is_moved_once():
    ck, cx = _checklist_db()
    cx.execute("INSERT INTO biofield_clinical_catalog (item_key,label,remedy_key,remedy,hidden) "
               "VALUES ('pinpoint cataracts','Pinpoint cataracts',"
               "'clear lens eye drops aces cat eye drops',"
               "'Clear Lens Eye Drops ACES+CAT Eye Drops',1)")
    ck.ensure_catalog_schema(cx)
    ck.ensure_catalog_schema(cx)   # idempotent
    got = cx.execute("SELECT remedy_key, hidden FROM biofield_clinical_catalog").fetchall()
    assert got == [("clear lens eye drops aces plus cat eye drops", 1)]
    # the hidden flag still applies under the new key
    assert "clear lens eye drops aces plus cat eye drops" in ck.forgotten_remedies(cx, "Pinpoint cataracts")


def test_the_move_leaves_an_old_row_alone_when_the_new_key_is_taken():
    ck, cx = _checklist_db()
    cx.execute("INSERT INTO biofield_clinical_catalog (item_key,label,remedy_key,remedy,hidden) "
               "VALUES ('cataract','Cataract','ocuheal eye drops','OcuHeal+ Eye Drops',0)")
    cx.execute("INSERT INTO biofield_clinical_catalog (item_key,label,remedy_key,remedy,hidden) "
               "VALUES ('cataract','Cataract','ocuheal plus eye drops','OcuHeal+ Eye Drops',0)")
    ck.ensure_catalog_schema(cx)
    keys = sorted(r[0] for r in cx.execute("SELECT remedy_key FROM biofield_clinical_catalog"))
    assert keys == ["ocuheal eye drops", "ocuheal plus eye drops"]
