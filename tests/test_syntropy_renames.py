"""Six Synergy names become Syntropy (Glen, 2026-09-30; spec in production's
`_catalog/2026-09-30-syntropy-renames/spec.md`, amended 2026-10-06).

Slugs stay. Each product's old name becomes an alias that opts in to reports, so every
consumer that turns a name into a product still finds it: dictation, dosing, the Intake
invoice, the clinical overrides, the portal cart and the app's title lookup. Old records
in clinical.db keep their names.

`pinecone_title` stays the old name until knowledge re-titles the vectors. Every case
here runs twice: with the title kept, and with it flipped to the new name.

A bare "Seacure" is the older name of both SeaAmino products (Glen, 2026-09-30: "bare is
older and can now mean both"), so every consumer leaves it unresolved.

Molybdenum Syntropy comes off sale (2026-10-06) with no replacement. A consumer that
refuses an inactive product (dictation, dosing, invoice) leaves its names unresolved.
"""
import json
import sqlite3
from pathlib import Path

import pytest

import dashboard.biofield_authoring as ba
from dashboard.biofield_invoice import resolve_line_slug
from dashboard.clinical_glossary import (load_overrides, product_name_index,
                                         remedy_product_slug)
from dashboard.practitioner_portal import name_to_slug

ROOT = Path(__file__).resolve().parent.parent
PRODUCTS = ROOT / "data" / "products.json"

RENAMES = {
    "magnesium-syntropy": ("Magnesium Synergy", "Magnesium Syntropy", "355"),
    "vitamin-a-syntropy": ("Vitamin A Synergy", "Vitamin A Syntropy", "539"),
    "zinc-syntropy": ("Zinc Synergy", "Zinc Syntropy", "337"),
    "vitamin-d-syntropy": ("Vitamin D Synergy", "Vitamin D Syntropy", "395"),
    "seaamino-syntropy": ("Seacure Synergy", "SeaAmino Syntropy", "381"),
    "molybdenum-syntropy": ("Molybdenum Synergy", "Molybdenum Syntropy", None),
}
LIVE = [s for s in RENAMES if s != "molybdenum-syntropy"]

# Acceptance table: input -> slug. Molybdenum is checked separately (it is inactive).
ACCEPT = [
    ("Magnesium Synergy", "magnesium-syntropy"), ("Magnesium Syntropy", "magnesium-syntropy"),
    ("Vitamin A Synergy", "vitamin-a-syntropy"), ("Vitamin A Syntropy", "vitamin-a-syntropy"),
    ("Zinc Synergy", "zinc-syntropy"), ("Zinc Syntropy", "zinc-syntropy"),
    ("Vitamin D Synergy", "vitamin-d-syntropy"), ("Vitamin D Syntropy", "vitamin-d-syntropy"),
    ("Vitamin D Synergy Powder", "vitamin-d-syntropy"),
    ("Seacure Synergy", "seaamino-syntropy"), ("SeaCure Synergy", "seaamino-syntropy"),
    ("SeaAmino Syntropy", "seaamino-syntropy"),
]
AMBIGUOUS = ["Seacure", "SeaCure", "seacure", " SEACURE "]
POWDER = ["SeaAmino", "SeaAmino Powder", "SeaAminos"]

_CACHED = ("_deprecated_catalog_names", "_active_catalog_names", "_superseded_name_map",
           "_catalog_alias_map", "_catalog_exact_aliases")


def _catalog():
    return json.loads(PRODUCTS.read_text())["products"]


def _flipped():
    cat = _catalog()
    for slug, (_, new, _) in RENAMES.items():
        cat[slug]["pinecone_title"] = new
    return cat


@pytest.fixture(params=["title kept", "title flipped"])
def catalog(request, tmp_path, monkeypatch):
    """The real catalog, or the real catalog with the six titles flipped. The products
    file the authoring and invoice modules read is pointed at the same one."""
    if request.param == "title kept":
        cat = _catalog()
    else:
        cat = _flipped()
        path = tmp_path / "products.json"
        doc = json.loads(PRODUCTS.read_text())
        doc["products"] = cat
        path.write_text(json.dumps(doc))
        monkeypatch.setattr(ba, "_PRODUCTS_JSON", str(path))
    for fn in _CACHED:
        f = getattr(ba, fn, None)
        if f is not None and hasattr(f, "cache_clear"):
            f.cache_clear()
    yield cat
    for fn in _CACHED:
        f = getattr(ba, fn, None)
        if f is not None and hasattr(f, "cache_clear"):
            f.cache_clear()


def _snap(vitamin_d_name="Vitamin D Syntropy"):
    """A FileMaker snapshot with the real names and ids, plus the neighbours that once
    stole these names (Iron, Vitamin C, a SeaCure powder). Each dose names its row."""
    c = sqlite3.connect(":memory:")
    c.execute("CREATE TABLE fmp_snap_products(id_pk TEXT, product_name TEXT, active TEXT, "
              "type TEXT, dosage TEXT, dosage_freq TEXT, dosage_timing TEXT)")
    rows = [("355", "Magnesium Syntropy"), ("539", "Vitamin A Syntropy"),
            ("337", "Zinc Syntropy"), ("395", vitamin_d_name), ("381", "SeaAmino Syntropy"),
            ("471", "SeaAmino Powder"), ("333", "Vitamin C Syntropy"),
            ("900", "Iron Syntropy"), ("901", "Seacure Powder and Synergy")]
    c.executemany("INSERT INTO fmp_snap_products VALUES(?,?,'Yes','Supplement',?,'daily','')",
                  [(i, n, f"dose-{i}") for i, n in rows])
    return c


# ── the catalog record ───────────────────────────────────────────────────────

@pytest.mark.parametrize("slug", list(RENAMES))
def test_each_record_carries_the_new_name_and_keeps_the_old(slug):
    old, new, _ = RENAMES[slug]
    p = _catalog()[slug]
    assert p["name"] == new
    assert p["pinecone_title"] == old          # flips later, after knowledge re-titles
    assert old in p["aliases"]
    assert p["report_aliases"] is True


def test_seaamino_capsules_gain_filemaker_381_and_no_one_else_has_it():
    cat = _catalog()
    assert cat["seaamino-syntropy"]["fmp_id"] == "381"
    assert [s for s, p in cat.items() if str(p.get("fmp_id")) == "381"] == ["seaamino-syntropy"]
    assert cat["seaaminos"]["fmp_id"] == "471"
    assert cat["seaaminos"]["name"] == "SeaAmino Powder"


def test_molybdenum_is_off_sale_and_the_others_are_not():
    cat = _catalog()
    assert cat["molybdenum-syntropy"]["inactive"] is True
    assert not cat["molybdenum-syntropy"].get("description")
    for slug in LIVE:
        assert not cat[slug].get("inactive"), slug


def test_vitamin_d_also_answers_to_its_powder_name():
    assert "Vitamin D Synergy Powder" in _catalog()["vitamin-d-syntropy"]["aliases"]


# SHA-256 of each `description` in production's store-correction.json (2026-09-30),
# which lives in the vault, not this repo. The whole string is compared.
APPROVED_DESCRIPTIONS = {
    "magnesium-syntropy": "025d37824c47b6efb7d955dbdc5ef0173bcbe7377e9764916f7c96ec4ca19d11",
    "zinc-syntropy": "63e5b1f6005a70472c1c172be5f889d7d4183d5865da25e2983ed36de22444a1",
    "vitamin-d-syntropy": "d8737ecacb97a0894acde78b3be5424e28ffc786f2e2816f3dbd99639938347d",
    "seaamino-syntropy": "b710d733ba590973f0e0c4565b622d9b975703a02999ba5649a61e460226c1d2",
}


def test_descriptions_are_the_approved_text_and_name_no_old_name():
    """Descriptions are served from products.json. Vitamin A's was replaced outright by
    #1874; the other four drop the scraped "<old> . <old> <new> " run."""
    import hashlib
    cat = _catalog()
    for slug, digest in APPROVED_DESCRIPTIONS.items():
        assert hashlib.sha256(cat[slug]["description"].encode()).hexdigest() == digest, slug
    for slug, (old, _, _) in RENAMES.items():
        assert old not in (cat[slug].get("description") or ""), slug


# ── dictation ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("spoken,slug", ACCEPT)
def test_dictation_finds_each_name(catalog, spoken, slug):
    new = RENAMES[slug][1]
    assert ba.resolve_remedy_name(_snap(), spoken) == new
    assert ba.resolve_remedy_name(_snap(), spoken.lower()) == new


@pytest.mark.parametrize("spoken", AMBIGUOUS)
def test_dictation_leaves_bare_seacure_for_a_person(catalog, spoken):
    """The spoken word comes back as typed, Title Cased: no product chosen."""
    out = ba.resolve_remedy_name(_snap(), spoken)
    assert out.strip().lower() == "seacure"


def test_dictation_keeps_seaamino_on_the_powder(catalog):
    assert ba.resolve_remedy_name(_snap(), "SeaAmino Powder") == "SeaAmino Powder"
    assert ba.resolve_remedy_name(_snap(), "SeaAmino") != "SeaAmino Syntropy"


@pytest.mark.parametrize("spoken", ["Molybdenum Synergy", "Molybdenum Syntropy"])
def test_dictation_never_moves_molybdenum_onto_a_neighbour(catalog, spoken):
    out = ba.resolve_remedy_name(_snap(), spoken)
    assert out not in {n for _, n, _ in RENAMES.values()} - {"Molybdenum Syntropy"}
    assert "Magnesium" not in out


# ── dosing ───────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("name,slug", ACCEPT)
@pytest.mark.parametrize("d_name", ["Vitamin D Syntropy", "Vitamin D Synergy"])
def test_dosing_comes_from_the_products_own_filemaker_row(catalog, name, slug, d_name):
    fmp = RENAMES[slug][2]
    assert ba.remedy_dosing(_snap(d_name), name)["dosage"] == f"dose-{fmp}"


def test_dose_row_says_which_filemaker_row_it_is():
    cx = _snap()
    cx.row_factory = sqlite3.Row
    assert ba._dose_row(cx, "Zinc Syntropy")["id_pk"] == "337"


@pytest.mark.parametrize("name", ["Molybdenum Synergy", "Molybdenum Syntropy"])
def test_molybdenum_has_no_dose_on_purpose(name):
    assert ba.remedy_dosing(_snap(), name) == {"dosage": "", "frequency": "", "timing": ""}


@pytest.mark.parametrize("name", AMBIGUOUS)
def test_dosing_leaves_bare_seacure_blank(name):
    """Without the guard the prefix match picks "Seacure Powder and Synergy"."""
    assert ba.remedy_dosing(_snap(), name) == {"dosage": "", "frequency": "", "timing": ""}


# ── Intake invoice ───────────────────────────────────────────────────────────

def _intake_catalog(cat):
    """The Intake app's catalog, as /api/console/biofield-portal/catalog serves it:
    slug, name and price only, and no inactive product."""
    return [{"slug": s, "name": p.get("name"), "price_cents": p.get("price_cents")}
            for s, p in cat.items() if not p.get("inactive")]


@pytest.mark.parametrize("name,slug", ACCEPT)
def test_invoice_bills_each_name_to_its_product(catalog, name, slug):
    assert resolve_line_slug(name, _intake_catalog(catalog)) == slug


@pytest.mark.parametrize("name,slug", ACCEPT)
def test_invoice_bills_old_names_without_products_json(monkeypatch, name, slug):
    """The Intake app may not carry products.json; _RETIRED_NAMES covers it alone."""
    import dashboard.biofield_invoice as bi
    monkeypatch.setattr(bi, "_old_names", lambda: {})
    assert resolve_line_slug(name, _intake_catalog(_catalog())) == slug


@pytest.mark.parametrize("name", AMBIGUOUS + ["Molybdenum Synergy", "Molybdenum Syntropy"])
def test_invoice_leaves_seacure_and_molybdenum_for_manual_add(catalog, name):
    assert resolve_line_slug(name, _intake_catalog(catalog)) is None


@pytest.mark.parametrize("name", AMBIGUOUS)
def test_invoice_guard_holds_even_if_a_product_were_named_seacure(name):
    """Exact-only today, so the guard is what keeps a future "Seacure" row from billing."""
    cat = _intake_catalog(_catalog()) + [{"slug": "seaaminos-old", "name": "Seacure"}]
    assert resolve_line_slug(name, cat) is None


@pytest.mark.parametrize("name", POWDER)
def test_invoice_never_bills_seaamino_powder_names_as_the_capsules(catalog, name):
    assert resolve_line_slug(name, _intake_catalog(catalog)) in (None, "seaaminos")


@pytest.mark.parametrize("old,slug", [(o, s) for s, (o, _, _) in RENAMES.items() if s in LIVE]
                         + [("Vitamin D Synergy Powder", "vitamin-d-syntropy")])
@pytest.mark.parametrize("remedy,qty", [("name only", 1), ({"qty": 2}, 2), ({"qty": None}, 1)])
def test_invoice_line_end_to_end(old, slug, remedy, qty):
    """Slug, an explicit quantity, the default of 1, the unit price and the line total."""
    from dashboard.biofield_invoice import build_invoice_lines
    cat = _intake_catalog(_catalog())
    r = old if remedy == "name only" else dict(remedy, name=old)
    out = build_invoice_lines({}, [r], cat, include_fee=False)
    assert out["skipped"] == []
    (line,) = out["lines"]
    assert (line["slug"], line["qty"]) == (slug, qty)
    price = next(c["price_cents"] for c in cat if c["slug"] == line["slug"])
    assert price == 7000
    assert price * line["qty"] == 7000 * qty


# ── clinical overrides ───────────────────────────────────────────────────────

@pytest.mark.parametrize("name,slug", ACCEPT + [("Molybdenum Synergy", "molybdenum-syntropy"),
                                                ("Molybdenum Syntropy", "molybdenum-syntropy")])
def test_clinical_links_find_each_name(catalog, name, slug):
    assert remedy_product_slug(name, product_name_index(catalog), load_overrides()) == slug


@pytest.mark.parametrize("name", AMBIGUOUS)
def test_clinical_links_leave_bare_seacure_as_text(catalog, name):
    assert remedy_product_slug(name, product_name_index(catalog), load_overrides()) is None


@pytest.mark.parametrize("name", AMBIGUOUS)
def test_clinical_guard_holds_even_if_an_override_named_seacure(name):
    idx = dict(product_name_index(_catalog()), seacure="seaaminos")
    assert remedy_product_slug(name, idx, {"Seacure": "seaamino-syntropy"}) is None


def test_overrides_are_keyed_in_lower_case():
    """remedy_product_slug tries the raw string, then the normalised one, so a Title
    Case key misses "SeaCure Synergy"."""
    ov = load_overrides()
    for s, (old, _, _) in RENAMES.items():
        assert ov[old.lower()] == s


# ── portal cart ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("name,slug", ACCEPT + [("Molybdenum Synergy", "molybdenum-syntropy"),
                                                ("Molybdenum Syntropy", "molybdenum-syntropy")])
def test_cart_finds_each_name(catalog, name, slug):
    assert name_to_slug(name, catalog) == slug


@pytest.mark.parametrize("name", AMBIGUOUS)
def test_cart_leaves_bare_seacure_unresolved(catalog, name):
    assert name_to_slug(name, catalog) is None


@pytest.mark.parametrize("name", POWDER)
def test_cart_keeps_seaamino_on_the_powder(catalog, name):
    assert name_to_slug(name, catalog) == "seaaminos"


def test_cart_never_reads_synergy_c_inside_synergy_capsules(catalog):
    assert name_to_slug("Magnesium Synergy Capsules", catalog) in ("magnesium-syntropy", None)


def test_cart_substring_matches_whole_words_only():
    cat = {"es1-lymph": {"name": "ES1 Lymph", "pinecone_title": "ES1"},
           "vitamin-c-syntropy": {"name": "Vitamin C Syntropy", "pinecone_title": "Synergy C"}}
    assert name_to_slug("Magnesium Synergy Capsules", cat) is None
    assert name_to_slug("Synergy C capsules", cat) == "vitamin-c-syntropy"
    assert name_to_slug("ES13 Something", cat) is None


# ── app title lookup ─────────────────────────────────────────────────────────

def test_title_lookup_finds_new_and_old_names_while_the_title_is_old():
    import app as a
    for slug, (old, new, _) in RENAMES.items():
        assert a._TITLE_TO_SLUG.get(new) == slug
        assert a._TITLE_TO_SLUG.get(old) == slug


def test_title_lookup_finds_old_names_after_the_flip():
    import app as a
    flipped = _flipped()
    idx = {(p.get("pinecone_title") or p.get("name")): s for s, p in flipped.items()}
    a._index_names_and_aliases(idx, flipped)
    for slug, (old, new, _) in RENAMES.items():
        assert idx.get(new) == slug
        assert idx.get(old) == slug


def test_title_lookup_never_lets_a_name_or_alias_take_a_real_title():
    import app as a
    cat = {"a": {"name": "Shared", "pinecone_title": "A title"},
           "b": {"name": "B", "pinecone_title": "Shared", "aliases": ["A title"],
                 "report_aliases": True}}
    idx = {(p.get("pinecone_title") or p.get("name")): s for s, p in cat.items()}
    a._index_names_and_aliases(idx, cat)
    assert idx["Shared"] == "b" and idx["A title"] == "a"


def test_title_lookup_skips_aliases_of_products_that_do_not_opt_in():
    import app as a
    idx = a._index_names_and_aliases({}, {"x": {"name": "X", "aliases": ["BFA"]}})
    assert "BFA" not in idx


# ── text that named the old names ────────────────────────────────────────────

LIVE_TEXT = ["data/atlas-concepts.json", "data/atlas-seed-input.json", "data/prl_seed.json",
             "data/e4l_stressor_map.json", "data/upsell-pairings.json",
             "dashboard/remedy_upgrades.py"]


@pytest.mark.parametrize("path", LIVE_TEXT)
def test_live_text_names_no_old_name(path):
    text = (ROOT / path).read_text()
    for old, _, _ in RENAMES.values():
        assert old not in text, (path, old)


def test_clinical_theory_live_fields_name_no_old_name():
    """Snapshot fields no code reads stay as they were, as #1853 left them."""
    doc = json.loads((ROOT / "data" / "clinical_theory_catalog.json").read_text())
    frozen = {"description_snapshot_2026_07_14", "description_live"}

    def walk(o, key=None):
        if isinstance(o, dict):
            for k, v in o.items():
                if k not in frozen:
                    yield from walk(v, k)
        elif isinstance(o, list):
            for v in o:
                yield from walk(v, key)
        elif isinstance(o, str):
            yield o
    text = "\n".join(walk(doc))
    for old, _, _ in RENAMES.values():
        assert old not in text, old


def test_atlas_keeps_old_search_words_and_adds_new_ones():
    for path in ("data/atlas-concepts.json", "data/atlas-seed-input.json"):
        text = (ROOT / path).read_text()
        for word in ("magnesium synergy blend", "molybdenum synergy formula",
                     "zinc synergy supplement"):
            assert f'"{word}"' in text
            assert f'"{word.replace("synergy", "syntropy")}"' in text


def test_atlas_vitamin_a_summary_drops_planned():
    want = ("Vitamin A Syntropy is a formula combining vitamin A with bioavailability-"
            "enhancing botanicals including ginkgo and moringa.")
    for path in ("data/atlas-concepts.json", "data/atlas-seed-input.json"):
        assert want in (ROOT / path).read_text()


def test_product_alias_map_points_at_the_new_names():
    doc = json.loads((ROOT / "data" / "product-aliases.json").read_text())["aliases"]
    for old, new, slug in (("Magnesium Synergy", "Magnesium Syntropy", "magnesium-syntropy"),
                           ("Molybdenum Synergy", "Molybdenum Syntropy", "molybdenum-syntropy")):
        assert doc[old]["catalog_name"] == new
        assert doc[new]["catalog_name"] == new
        assert doc[new]["url"].endswith("/" + slug)
