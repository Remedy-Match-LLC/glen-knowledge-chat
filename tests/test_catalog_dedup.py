"""Duplicate FMP records for the same eye-drop product are deprecated, not deleted.

FMP carried two near-identical records for each of two eye drops. Invoice/prescription
history (00 System/fmp-extracts) identifies the live one in each pair:

    372  Clear Lens Eye Drops ACES+CAT      16 invoices, 3 prescriptions  <- live
    440  Clear Lens+ Eye Drops ACES+CAT      1 invoice,  0 prescriptions
    390  Neuro Eye Drops ACES+GL Lite        1 invoice,  0 prescriptions  <- live
    369  Neuro+ Eye Drops                    0 invoices, 0 prescriptions

The loser is marked `inactive` rather than removed: prod order history lives in
chat_log.db on the Render disk, and a deleted slug would orphan any line item that
references it. `inactive` makes _get_product return None (unsellable) while leaving
the record readable.
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

RETIRED = {
    "clear-lens-eye-drops-aces-cat-eye-drops-2": "440",
    # Glen 2026-09-03: the FMP-side twin of clear-lens-eye-drops. Same $69.97, same
    # 5 mL dropper, differing from the survivor by a space in the name. The survivor
    # is the store-recovery record, the only one of the two carrying an ingredient
    # list (Ann Bauder read its DMSO 1% off the page).
    "clear-lens-eye-drops-aces-cat-eye-drops": "372",
    "neuro-eye-drops": "369",
}
LIVE = {
    "neuro-eye-drops-aces-gl-lite-eye-drops": ("390", "Neuro Eye Drops"),
}
# The clear-lens survivor has no fmp_id of its own: the retired twin keeps 372 so
# FMP lookups still land somewhere and redirect (the wholomega precedent).
LIVE_CLEAR_LENS = "clear-lens-eye-drops"


def _products():
    return json.loads((ROOT / "data" / "products.json").read_text())["products"]


def test_duplicate_records_are_deprecated_not_deleted():
    prods = _products()
    for slug, fmp_id in RETIRED.items():
        assert slug in prods, f"{slug} was deleted; it must stay for order history"
        assert prods[slug].get("inactive") is True, f"{slug} should be inactive"
        assert prods[slug]["fmp_id"] == fmp_id


def test_surviving_record_is_live_and_renamed():
    prods = _products()
    for slug, (fmp_id, name) in LIVE.items():
        e = prods[slug]
        assert e["fmp_id"] == fmp_id
        assert e["name"] == name
        assert not e.get("inactive")


def test_the_clear_lens_survivor_is_live_and_owns_no_fmp_id():
    prods = _products()
    e = prods[LIVE_CLEAR_LENS]
    assert not e.get("inactive") and "superseded_by" not in e
    # Keeps its own name: an active record may not wear a retired one's name
    # (test_es1_lymph_canonical.test_no_active_catalog_name_is_also_a_retired_name).
    assert e["name"] == "Clear Lens Eyedrops"
    assert e["price_cents"] == 6997 and e["bottle_type"] == "Dropper 5 mL"
    assert e.get("ingredients"), "the survivor must be the record carrying ingredients"
    assert "fmp_id" not in e, "fmp_id 372 stays on the retired twin so 372 still resolves"


def test_rename_does_not_touch_pinecone_title():
    """pinecone_title is the retrieval key against the vector store — renaming the
    display name must not move it (see reference_product_catalog_pinecone_coupling)."""
    prods = _products()
    assert prods["clear-lens-eye-drops-aces-cat-eye-drops"]["pinecone_title"] == \
        "Clear Lens Eye Drops ACES+CAT Eye Drops"
    assert prods["neuro-eye-drops-aces-gl-lite-eye-drops"]["pinecone_title"].startswith(
        "Neuro Eye Drops")
    # The survivor was renamed to the corrected spelling; its retrieval key must not
    # have followed the display name.
    assert prods["clear-lens-eye-drops"]["pinecone_title"] == "Clear Lens Eyedrops"


def test_serenity_capsule_and_drink_mix_both_stay_sellable():
    """Not duplicates: 305 is a capsule ('1 capsule daily'), 1081 a drink mix
    ('1 scoop 2 times a day'). 305 carries FMP's trailing '*' = discontinuing but
    STILL sellable, and it is the only one of the two with any sales history."""
    prods = _products()
    for slug in ("serenity-blue-green-balance", "serenity-bluegreen-balance-drink-mix"):
        assert not prods[slug].get("inactive"), f"{slug} must remain sellable"


def test_both_retired_clear_lens_slugs_resolve_to_the_survivor():
    """The behaviour, not just the pointers.

    Glen 2026-09-03: drop clear-lens-eye-drops-aces-cat-eye-drops. Order history,
    FMP id 372 and the ACES+CAT vector title all still name it, so every one of them
    has to land on the surviving record rather than on nothing.
    """
    from dashboard.products import superseded_slug
    prods = _products()
    for dead in ("clear-lens-eye-drops-aces-cat-eye-drops",
                 "clear-lens-eye-drops-aces-cat-eye-drops-2"):
        assert superseded_slug(dead, prods) == LIVE_CLEAR_LENS, dead
    # and the survivor resolves to itself rather than wandering off
    assert superseded_slug(LIVE_CLEAR_LENS, prods) == LIVE_CLEAR_LENS


def test_no_upsell_or_program_target_names_a_retired_record():
    """A name-keyed config pointing at a retired record is how the wrong SKU gets
    picked later; the two eye-drop names differ only by a space."""
    import json
    import pathlib
    prods = _products()
    retired_names = {(p.get("name") or "").strip()
                     for p in prods.values() if p.get("inactive")}
    root = pathlib.Path(__file__).resolve().parent.parent
    pairings = json.loads((root / "data" / "upsell-pairings.json").read_text())
    targets = {t for v in (pairings.get("pairings") or pairings).values()
               if isinstance(v, list) for t in v}
    clash = sorted(t for t in targets if t in retired_names)
    assert clash == [], f"upsell pairings naming retired records: {clash}"
