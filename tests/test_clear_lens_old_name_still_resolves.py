"""The old store name "Clear Lens Eyedrops" still finds the renamed product.

Glen, 2026-10-13: the product became "Clear Lens Eye Drops" to match the label.
Order history and Pinecone still carry the old name, and the buy-link lookup
matched display names only, so the old name found nothing.
"""
import app


def test_the_old_and_new_names_find_the_live_product():
    for name in ("Clear Lens Eyedrops", "Clear Lens Eye Drops",
                 "Clear Lens Eye Drops ACES+CAT Eye Drops"):
        assert app._resolve_buy_slug(name) == "clear-lens-eye-drops", name


def test_an_exact_name_still_wins_over_an_alias():
    assert app._resolve_buy_slug("Terrain Restore") == "terrain-restore"


def test_a_one_word_name_keeps_its_old_match():
    """"Sleep" is an alias of Sleep Infoceutical but has always found Sleep Syntropy."""
    assert app._resolve_buy_slug("Sleep") == app._resolve_buy_slug("sleep")
    assert "syntropy" in (app._resolve_buy_slug("Sleep") or "")
