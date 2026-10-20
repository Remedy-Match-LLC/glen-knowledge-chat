"""An "X in Terrain Restore" remedy with no product of its own still goes in the bottle.

Glen, 2026-10-19, on Josie's "Digestzymes in Terrain Restore", which the re-raise of
order 236 skipped because no such product exists: "It is made into her Terrain Restore
with the other ingredients, with no change in the fee." He adds up to five ingredients
plus oligoelements, so the catalogue will never list every combination.
"""
from dashboard import biofield_invoice as bi
from dashboard import biofield_portal_publish as bpp

CATALOG = [
    {"slug": "terrain-restore", "name": "Terrain Restore"},
    {"slug": "terrain-restore-custom", "name": "Custom Terrain Restore"},
    {"slug": "pyrite-gem-elixir-in-terrain-restore", "name": "Pyrite Gem Elixir in Terrain Restore"},
    {"slug": "digestzymes", "name": "Digestzymes"},
    {"slug": "liver-support", "name": "Liver Support"},
]
CAT_D = {c["slug"]: {"name": c["name"]} for c in CATALOG}


def test_an_unlisted_ingredient_folds_into_the_one_custom_bottle():
    out = bi.build_invoice_lines({}, ["Pyrite Gem Elixir in Terrain Restore",
                                      "Digestzymes in Terrain Restore", "Liver Support"],
                                 CATALOG, include_fee=False)
    assert out["skipped"] == []
    assert out["lines"] == [
        {"slug": "terrain-restore-custom", "qty": 1, "source": "biofield",
         "note": "With Pyrite Gem Elixir and Digestzymes"},
        {"slug": "liver-support", "qty": 1, "source": "biofield"}]


def test_an_unlisted_ingredient_alone_is_still_the_custom_bottle():
    out = bi.build_invoice_lines({}, ["Digestzymes in Terrain Restore"], CATALOG,
                                 include_fee=False)
    assert [(l["slug"], l.get("note")) for l in out["lines"]] == [
        ("terrain-restore-custom", "With Digestzymes")]


def test_it_never_bills_the_ingredient_as_its_own_bottle():
    out = bi.build_invoice_lines({}, ["Digestzymes in Terrain Restore"], CATALOG,
                                 include_fee=False)
    assert "digestzymes" not in [l["slug"] for l in out["lines"]]


def test_other_unknown_names_are_still_skipped():
    out = bi.build_invoice_lines({}, ["Digestzymes Forte", "Terrain Restore Plus"], CATALOG,
                                 include_fee=False)
    assert out["lines"] == [] and out["skipped"] == ["Digestzymes Forte", "Terrain Restore Plus"]


def test_no_terrain_restore_in_the_catalogue_means_skipped_not_guessed():
    cat = [c for c in CATALOG if c["slug"] != "terrain-restore"]
    assert bi.resolve_line_slug("Digestzymes in Terrain Restore", cat) is None
    assert bi.resolve_line_slug("Digestzymes in Terrain Restore",
                                [dict(c, inactive=True) if c["slug"] == "terrain-restore" else c
                                 for c in CATALOG]) is None


def test_a_listed_ingredient_keeps_its_own_product():
    assert bi.resolve_line_slug("Pyrite Gem Elixir in Terrain Restore", CATALOG) \
        == "pyrite-gem-elixir-in-terrain-restore"


def test_the_portal_resolver_never_turns_it_into_a_bare_bottle():
    assert bpp._match("Digestzymes in Terrain Restore", CAT_D) == "terrain-restore"
    assert bpp._match("Pyrite Gem Elixir in Terrain Restore", CAT_D) \
        == "pyrite-gem-elixir-in-terrain-restore"
    assert bpp._match("Digestzymes", CAT_D) == "digestzymes"


def test_the_portal_folds_it_into_the_bottle():
    cands = [{"slug": bpp._match(n, CAT_D), "qty": 1, "name": n}
             for n in ("Pyrite Gem Elixir in Terrain Restore", "Digestzymes in Terrain Restore")]
    folded = bi.fold_terrain_restore(cands, True, name_of=lambda s: CAT_D.get(s, {}).get("name"))
    assert [(f["slug"], f["note"]) for f in folded] == [
        ("terrain-restore-custom", "With Pyrite Gem Elixir and Digestzymes")]


def test_the_real_catalogue_never_makes_a_bare_bottle_of_an_ingredient():
    """Before the fix the near match on the real catalogue turned these two into a
    Digestzymes and a Microbiome bottle on the portal."""
    import json
    import pathlib
    cat = json.loads((pathlib.Path(__file__).resolve().parents[1] / "data" / "products.json")
                     .read_text())["products"]
    assert bpp._match("Digestzymes in Terrain Restore", cat) == "terrain-restore"
    assert bpp._match("Microbiome in Terrain Restore", cat) == "terrain-restore"
    assert bpp._match("Pyrite Gem Elixir in Terrain Restore", cat) \
        == "pyrite-gem-elixir-in-terrain-restore"


def test_no_sellable_custom_bottle_means_skipped_never_plain_terrain_restore():
    """Review round 1: with the custom bottle waitlisted the ingredients billed as one
    plain Terrain Restore at the volume price, and their names were lost."""
    for cat in ([dict(c, waitlist_only=True) if c["slug"] == "terrain-restore-custom" else c
                 for c in CATALOG],
                [c for c in CATALOG if c["slug"] != "terrain-restore-custom"]):
        out = bi.build_invoice_lines({}, ["Digestzymes in Terrain Restore"], cat,
                                     include_fee=False)
        assert out["lines"] == [] and out["skipped"] == ["Digestzymes in Terrain Restore"]
    waitlisted = dict(CAT_D, **{"terrain-restore-custom": {"name": "Custom Terrain Restore",
                                                           "waitlist_only": True}})
    assert bpp._match("Digestzymes in Terrain Restore", waitlisted) is None
    assert bpp._match("Digestzymes in Terrain Restore",
                      {k: v for k, v in CAT_D.items() if k != "terrain-restore-custom"}) is None


def test_the_portal_never_near_matches_an_ingredient_name():
    """Review rounds 1 and 2: a near match picked another product for these."""
    import json
    import pathlib
    cat = json.loads((pathlib.Path(__file__).resolve().parents[1] / "data" / "products.json")
                     .read_text())["products"]
    assert bpp._match("Jasper Gem Elixir in Terrain Restore", cat) == "terrain-restore"
    assert bpp._match("Gem Elixir in Terrain Restore", cat) == "terrain-restore"
    # Spellings the bottle cannot take stay unresolved, as the invoice skips them.
    assert bpp._match("Digestzymes In Terrain Restore.", cat) is None
    assert bpp._match("Digestzymes in Terrain Restore Drops", cat) is None
    assert bi.resolve_line_slug("Digestzymes In Terrain Restore.",
                                [dict(p, slug=s) for s, p in cat.items()]) is None


# Review round 3 (2026-10-19).
def _real_catalogue():
    import json
    import pathlib
    root = pathlib.Path(bpp.__file__).resolve().parents[1]
    return json.loads((root / "data" / "products.json").read_text())["products"]


def test_the_portal_reads_a_double_space_in_the_suffix_as_the_bottle():
    """Authored names carry stray spaces. "in  Terrain Restore" must still fold,
    never near-match a bare Digestzymes bottle (kills P1)."""
    cat = _real_catalogue()
    for name in ("Digestzymes in  Terrain Restore", "Digestzymes in Terrain  Restore",
                 "Digestzymes\tin Terrain Restore"):
        assert bpp._match(name, cat) == "terrain-restore", name
        assert bpp.resolve_remedy_slug(name, cat) == "terrain-restore", name


def test_the_portal_never_guesses_an_inactive_terrain_restore():
    """Plain Terrain Restore off sale: the ingredient is left for staff (kills P4, P11).
    With a successor set, the unfiltered fallback would store the successor's slug."""
    off = dict(CAT_D, **{"terrain-restore": {"name": "Terrain Restore", "inactive": True}})
    assert bpp._match("Digestzymes in Terrain Restore", off) is None
    moved = dict(CAT_D, **{"terrain-restore": {"name": "Terrain Restore", "inactive": True,
                                               "superseded_by": "liver-support"}})
    assert bpp.resolve_remedy_slug("Digestzymes in Terrain Restore", moved) is None


def test_the_portal_never_folds_into_an_inactive_custom_bottle():
    """Custom bottle off sale (inactive, not waitlisted): the ingredient stays
    unresolved. Otherwise the portal stores a bare plain Terrain Restore line with the
    ingredient's name lost and the courtesy price applied (round 1's high finding,
    on the portal path; kills P7)."""
    off = dict(CAT_D, **{"terrain-restore-custom": {"name": "Custom Terrain Restore",
                                                    "inactive": True}})
    assert bpp._match("Digestzymes in Terrain Restore", off) is None
    assert bpp.resolve_remedy_slug("Digestzymes in Terrain Restore", off) is None


def test_the_portal_never_near_matches_a_hyphenated_suffix():
    """Round 3: "in Terrain-Restore" near-matched a bare Digestzymes bottle."""
    assert bpp._match("Digestzymes in Terrain-Restore", _real_catalogue()) is None
