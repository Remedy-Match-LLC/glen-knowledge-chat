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
    # Flipped 2026-10-07 after knowledge re-titled the vectors; Molybdenum has no vector.
    assert p["pinecone_title"] == (old if slug == "molybdenum-syntropy" else new)
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

@pytest.mark.parametrize("name,slug", ACCEPT)
def test_clinical_links_find_each_name(catalog, name, slug):
    assert remedy_product_slug(name, product_name_index(catalog), load_overrides()) == slug


@pytest.mark.parametrize("name", ["Molybdenum Synergy", "Molybdenum Syntropy"])
def test_clinical_links_never_point_at_off_sale_molybdenum(catalog, name):
    """Round 2: a glossary link renders as /begin/product/<slug>, a page selling nothing."""
    assert remedy_product_slug(name, product_name_index(catalog), load_overrides()) is None


def test_no_override_points_at_off_sale_molybdenum():
    assert "molybdenum-syntropy" not in load_overrides().values()


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
        if s in LIVE:
            assert ov[old.lower()] == s


# ── portal cart ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("name,slug", ACCEPT)
def test_cart_finds_each_name(catalog, name, slug):
    assert name_to_slug(name, catalog) == slug


@pytest.mark.parametrize("name", ["Molybdenum Synergy", "Molybdenum Syntropy", "Molybdenum"])
def test_cart_never_offers_off_sale_molybdenum(catalog, name):
    """Round 1: the assistant gave it an Add button and the cart accepted it."""
    assert name_to_slug(name, catalog) is None


def test_off_sale_products_are_not_orderable():
    from dashboard.practitioner_portal import is_orderable
    cat = _catalog()
    assert is_orderable("molybdenum-syntropy", cat) is False
    assert is_orderable("magnesium-syntropy", cat) is True
    assert all(not is_orderable(s, cat) for s, p in cat.items() if p.get("inactive"))


def test_cart_sends_a_retired_twin_to_its_replacement():
    cat = {"old": {"name": "Old Powder", "inactive": True, "superseded_by": "new"},
           "new": {"name": "New Capsules"},
           "gone": {"name": "Gone Formula", "inactive": True}}
    assert name_to_slug("Old Powder", cat) == "new"
    assert name_to_slug("Gone Formula", cat) is None
    loop = {"a": {"name": "Alpha Blend", "inactive": True, "superseded_by": "b"},
            "b": {"name": "Beta Blend", "inactive": True, "superseded_by": "a"}}
    assert name_to_slug("Alpha Blend", loop) is None


@pytest.mark.parametrize("name", AMBIGUOUS)
def test_cart_leaves_bare_seacure_unresolved(catalog, name):
    assert name_to_slug(name, catalog) is None


@pytest.mark.parametrize("name", POWDER)
def test_cart_keeps_seaamino_on_the_powder(catalog, name):
    assert name_to_slug(name, catalog) == "seaaminos"


def test_cart_never_reads_synergy_c_inside_synergy_capsules(catalog):
    assert name_to_slug("Magnesium Synergy Capsules", catalog) in ("magnesium-syntropy", None)


def test_a_one_letter_ending_never_runs_into_a_longer_word():
    cat = {"vitamin-c-syntropy": {"name": "Vitamin C Syntropy", "pinecone_title": "Synergy C"},
           "vital": {"name": "Vital Energy Be"}}
    assert name_to_slug("Magnesium Synergy Capsules", cat) is None
    assert name_to_slug("Synergy C capsules", cat) == "vitamin-c-syntropy"
    assert name_to_slug("take Synergy C daily", cat) == "vitamin-c-syntropy"
    # Round 1: a wider whole-word rule moved these; a two-letter ending still matches.
    assert name_to_slug("Vital Energy Bee", cat) == "vital"


@pytest.mark.parametrize("name,slug", [("Ocuflow Day", "ocuflow-daytime"),
                                       ("Vital Energy Bee", "vital-energy-be"),
                                       ("Digestzymes in Terrain Restore", "digestzymes"),
                                       ("Ginger", "gingerol")])
def test_cart_keeps_how_other_short_forms_resolved(name, slug):
    """Round 1 measured whole-word and longest-overlap rules on 12,086 clinical names;
    both moved names like these onto other products. They resolve as they did."""
    assert name_to_slug(name, _catalog()) == slug


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
        for word in ("magnesium synergy blend", "zinc synergy supplement"):
            assert f'"{word}"' in text
            assert f'"{word.replace("synergy", "syntropy")}"' in text


def test_atlas_vitamin_a_summary_drops_planned():
    want = ("Vitamin A Syntropy is a formula combining vitamin A with bioavailability-"
            "enhancing botanicals including ginkgo and moringa.")
    for path in ("data/atlas-concepts.json", "data/atlas-seed-input.json"):
        assert want in (ROOT / path).read_text()


def test_product_alias_map_points_at_the_new_names():
    doc = json.loads((ROOT / "data" / "product-aliases.json").read_text())["aliases"]
    old, new, slug = "Magnesium Synergy", "Magnesium Syntropy", "magnesium-syntropy"
    assert doc[old]["catalog_name"] == new
    assert doc[new]["catalog_name"] == new
    assert doc[new]["url"].endswith("/" + slug)


# ── Molybdenum was never produced ───────────────────────────────────────────
# Glen, 2026-10-07: "no - not produced. Only an idea." It leaves every surface that
# recommends or links it. The catalog record stays, inactive, for order history.

def test_chat_links_never_name_molybdenum():
    doc = json.loads((ROOT / "data" / "product-aliases.json").read_text())["aliases"]
    assert not [k for k, v in doc.items() if "molybdenum-syntropy" in json.dumps(v)]


def test_scan_suggestions_never_recommend_molybdenum():
    text = (ROOT / "data" / "e4l_stressor_map.json").read_text()
    assert "Molybdenum Syntropy" not in text and "Molybdenum Synergy" not in text
    doc = json.loads(text)
    found = []

    def walk(o):
        if isinstance(o, dict):
            if o.get("slug") == "nut-mo":
                found.append(o)
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
    walk(doc)
    (mo,) = found
    assert mo["remedies"] == [] and mo["name"] == "Molybdenum"   # the mineral stays


def test_glossary_names_molybdenum_only_as_the_mineral():
    doc = json.loads((ROOT / "data" / "clinical_theory_catalog.json").read_text())
    (e,) = [e for d in doc["dimensions"] for e in d["entries"] if e.get("slug") == "molybdenum"]
    assert e["description"] == ("Molybdenum is important for endocrine regulation.You can find "
                                "Molybdenum in Sulfur Syntropy and Thyroid Support")
    assert [r["name"] for r in e["remedies"]] == ["Sulfur Syntropy", "Thyroid Support"]


def test_atlas_drops_molybdenum_and_no_edge_points_at_it():
    """The repo copy drops the concept outright. The live disk copy keeps it with status
    "retired" (reversible); a retired record left here would make the drift check
    (atlas-rebuild-from-live.py --check) report the repo ahead of live on every run."""
    concepts = json.loads((ROOT / "data" / "atlas-concepts.json").read_text())["concepts"]
    assert "molybdenum-synergy" not in {c["id"] for c in concepts}
    assert not [c["id"] for c in concepts if "molybdenum-synergy" in (c.get("neighbors") or [])]
    # The real reader that /atlas/data serves, pointed at the repo copy.
    import atlas_store
    from unittest import mock
    with mock.patch.object(atlas_store, "CONCEPTS_PATH", ROOT / "data" / "atlas-concepts.json"):
        served = atlas_store.build_graph()["concepts"]
    ids = {c["id"] for c in served}
    assert "molybdenum-synergy" not in ids and "zinc-synergy" in ids
    seed = (ROOT / "data" / "atlas-seed-input.json").read_text()
    assert '"molybdenum-synergy"' not in seed


def test_seacure_syntropy_gets_the_capsules_dose():
    """Round 1: dictation sends "Seacure Syntropy" to SeaAmino Syntropy; dosing agrees."""
    assert ba.remedy_dosing(_snap(), "Seacure Syntropy")["dosage"] == "dose-381"


# ── review round 3 ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("name", ["Zinc", "Iron", "Sea"])
def test_cart_never_substring_matches_a_short_word(name):
    """Without the length floor, "Zinc" went to Zinc Taste Test and "Sea" to the capsules."""
    assert name_to_slug(name, _catalog()) is None


class _Match:
    def __init__(self, title):
        self.score = 0.95
        self.metadata = {"title": title, "url": "https://example.test/old-page", "price": 70}


class _Idx:
    def __init__(self, title):
        self.title = title

    def query(self, **_kw):
        return type("R", (), {"matches": [_Match(self.title)]})()


@pytest.mark.parametrize("spoken", ["Molybdenum", "Molybdenum Syntropy"])
def test_assistant_never_offers_off_sale_molybdenum_by_vector(monkeypatch, spoken):
    """Round 3: the semantic fallback met the old vector title and gave an Add button."""
    import app as a
    monkeypatch.setattr(a, "embed", lambda _n: [0.0])
    monkeypatch.setattr(a, "_idx", _Idx("Molybdenum Synergy"))
    assert a._assist_resolve_products([{"name": spoken, "why": "x"}]) == []


def test_assistant_vector_fallback_still_finds_a_live_product(monkeypatch):
    import app as a
    monkeypatch.setattr(a, "embed", lambda _n: [0.0])
    monkeypatch.setattr(a, "_idx", _Idx("Zinc Synergy"))
    (hit,) = a._assist_resolve_products([{"name": "zinc for taste", "why": "x"}])
    assert hit["slug"] == "zinc-syntropy"


def test_concierge_neither_adds_nor_opens_off_sale_molybdenum(monkeypatch):
    import app as a
    monkeypatch.setattr(a, "embed", lambda _n: [0.0])
    monkeypatch.setattr(a, "_idx", _Idx("Molybdenum Synergy"))
    a._COMPLEMENT_CACHE.clear()
    c = a._resolve_complement("Molybdenum Synergy")
    a._COMPLEMENT_CACHE.clear()
    assert not c["in_catalog"] and not c["url"] and c["slug"] is None


def test_publish_stores_no_slug_for_off_sale_molybdenum():
    from dashboard.biofield_portal_publish import resolve_remedy_slug
    cat = _catalog()
    for name in ("Molybdenum Syntropy", "Molybdenum Synergy"):
        assert resolve_remedy_slug(name, cat) is None
    assert resolve_remedy_slug("Zinc Synergy", cat) in ("zinc-syntropy", None)
    assert resolve_remedy_slug("Zinc Syntropy", cat) == "zinc-syntropy"


def test_glossary_keeps_links_for_retired_twins_with_a_replacement():
    """Round 3: skipping every inactive product dropped 250 rows of working links."""
    cat = _catalog()
    idx = product_name_index(cat)
    retired = [(s, p) for s, p in cat.items() if p.get("inactive") and p.get("superseded_by")
               and p["superseded_by"] in cat and not cat[p["superseded_by"]].get("inactive")]
    assert len(retired) > 100
    from dashboard.clinical_glossary import _norm_name
    for s, p in retired:
        assert _norm_name(s) in idx, s
    assert "molybdenum syntropy" not in idx and "molybdenum syntropy" not in idx.values()
