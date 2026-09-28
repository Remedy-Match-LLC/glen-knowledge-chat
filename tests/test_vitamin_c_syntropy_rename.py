"""Synergy C is renamed Vitamin C Syntropy, with its 19-line label panel, 2026-09-28.

Production's store correction (production/05 Formulations/vitamin-c-syntropy/2026-09-27/
store-correction.json), approved by Glen ("approve") after a copy round and three review rounds.
The old name must keep resolving everywhere an exact-name matcher reads it: clinical.db holds
248 visit_remedy rows named "Synergy C", and the Intake app's invoice builder gets a catalog
with no aliases. pinecone_title stays "Synergy C" until knowledge re-titles the Pinecone copy,
so the positive tests also run with it flipped.
"""
import copy
import json
import pathlib
import re
import sqlite3

import pytest

from dashboard import biofield_authoring as ba
from dashboard import biofield_invoice as bi
from dashboard import clinical_glossary as cg
from dashboard import practitioner_portal as pp

SLUG, NEW, OLD = "vitamin-c-syntropy", "Vitamin C Syntropy", "Synergy C"
KEPT = json.loads('{"price_cents": 6997, "bottle_type": "30 Caps", "url": "https://myhealingoasis.com/begin/product/vitamin-c-syntropy", "legacy_store_url": "https://remedymatch.com/remedies/syntropy/112-vitamin-c-syntropy", "qty_pricing": true}')
PRODUCTS = json.load(open("data/products.json", encoding="utf-8"))["products"]


def _flipped():
    """The catalog after knowledge re-titles the Pinecone copy."""
    prods = copy.deepcopy(PRODUCTS)
    prods[SLUG]["pinecone_title"] = NEW
    return prods


def test_the_record_carries_the_label():
    p = PRODUCTS[SLUG]
    assert p["name"] == NEW and p["aliases"] == [OLD] and p["pinecone_title"] == OLD
    assert len(p["ingredients"]) == 19 and all(i["dose"] for i in p["ingredients"])
    assert p["ingredients"][0] == {"name": "Vitamin C (Mg Ascorbate & Ascorbyl Palmitate)",
                                   "dose": "200 mg"}
    assert p["ingredients"][-1] == {"name": "Bioavailability Blend", "dose": "34.5 mg"}
    assert p["directions"] == "1 to 4 capsules daily before food or as guided."
    assert set(p["copy_pinned"]) >= {"ingredients", "intro", "description", "research", "benefits"}
    assert p["how_it_works"] == ""
    for k in ("note", "notes", "rename_work", "apply_as"):
        assert k not in p
    # The live fields the spec leaves alone, exactly as they were (review round 2).
    assert {k: p.get(k) for k in KEPT} == KEPT
    corr = json.load(open("data/products-manual-corrections.json", encoding="utf-8"))[SLUG]
    assert corr["ingredients"] == p["ingredients"]


# ── (a) the old name appears nowhere public except where it keeps resolving ───
ALLOWED = {
    "data/products.json": 2,                   # aliases, and pinecone_title until the re-title
    "data/clinical_remedy_overrides.json": 1,  # "Synergy C": "vitamin-c-syntropy"
    "data/product-aliases.json": 1,            # the chat link, already both names
}
HISTORY = {"data/products-enrich-candidate.json", "data/products-enrich-clean.json"}


def test_no_public_data_file_still_says_synergy_c():
    counts = {}
    for f in sorted(pathlib.Path("data").rglob("*.json")):
        if str(f) in HISTORY:
            continue
        # Whole words: "Cistus Synergy Cistus" is another product.
        n = len(re.findall(r"\bSynergy C\b", f.read_text(encoding="utf-8")))
        if n:
            counts[str(f)] = n
    assert counts == ALLOWED


def test_the_atlas_keeps_its_id():
    atlas = pathlib.Path("data/atlas-concepts.json").read_text(encoding="utf-8")
    assert '"id": "synergy-c"' in atlas and '"label": "Vitamin C Syntropy"' in atlas


# ── (b) both names resolve through every matcher ─────────────────────────────
@pytest.mark.parametrize("name", [OLD, NEW, "synergy c", "VITAMIN C SYNTROPY"])
def test_invoice_lines_resolve_both_names_even_without_aliases(name):
    """The Intake app's catalog carries only slug, name and price: no aliases."""
    bare = [{"slug": s, "name": p["name"], "price_cents": p.get("price_cents")}
            for s, p in _flipped().items() if isinstance(p, dict)]
    assert bi.resolve_line_slug(name, bare) == SLUG


def test_the_old_invoice_name_needs_the_product_to_exist():
    assert bi.resolve_line_slug(OLD, [{"slug": "other", "name": "Other"}]) is None


@pytest.mark.parametrize("name", [OLD, NEW])
def test_the_portal_assistant_resolves_both_names(name):
    assert pp.name_to_slug(name, _flipped()) == SLUG


@pytest.mark.parametrize("name", [OLD, NEW])
def test_the_resolve_index_holds_both_names(name):
    import app
    idx = app._build_resolve_name_index(_flipped())
    assert idx[name.lower()] == SLUG
    assert app._RESOLVE_NAME_INDEX[name.lower()] == SLUG      # and as deployed today


@pytest.mark.parametrize("name", [OLD, NEW])
def test_dosing_finds_filemakers_row_for_both_names(name):
    """FileMaker 333 was renamed Vitamin C Syntropy on 3/17/2026."""
    cx = sqlite3.connect(":memory:")
    cx.execute("CREATE TABLE fmp_snap_products (product_name TEXT, dosage TEXT, "
               "dosage_freq TEXT, dosage_timing TEXT)")
    cx.execute("INSERT INTO fmp_snap_products VALUES ('Vitamin C Syntropy', '1 capsule', "
               "'daily', 'before food')")
    cx.execute("INSERT INTO fmp_snap_products VALUES ('Vitamin C', 'WRONG', 'x', 'x')")
    assert ba.remedy_dosing(cx, name)["dosage"] == "1 capsule"


@pytest.mark.parametrize("name", [OLD, NEW])
def test_the_glossary_links_both_names(name):
    idx = cg.product_name_index(PRODUCTS)
    assert cg.remedy_product_slug(name, idx, cg.load_overrides("data/clinical_remedy_overrides.json")) == SLUG


def test_the_upgrade_note_names_the_product():
    from dashboard import remedy_upgrades as ru
    assert "Vitamin C Syntropy" in ru._UPGRADE_MAP["vitamin c"]["reason"]



# ── Review rounds 1 and 2 ────────────────────────────────────────────────────
def test_an_inactive_product_is_never_billed_under_its_old_name():
    cat = [{"slug": SLUG, "name": NEW, "inactive": True}]
    assert bi.resolve_line_slug(OLD, cat) is None


@pytest.mark.parametrize("name", ["Magnesium Synergy Capsules", "Glutathione Synergy Complex"])
def test_an_alias_never_matches_part_of_a_longer_name(name):
    assert pp.name_to_slug(name, _flipped()) != SLUG      # an alias matches whole names only


def test_the_alias_pass_sends_sleep_synergy_to_its_renamed_product():
    """Round 3: the alias pass changed one existing answer. "Sleep Synergy" (QuickBooks item
    48) went to the plain Sleep product by substring; it is Sleep Syntropy's old name (renamed
    2026-09-15), so it now resolves there, for the assistant and the QuickBooks line import."""
    assert pp.name_to_slug("Sleep Synergy", PRODUCTS) == "sleep-syntropy"
