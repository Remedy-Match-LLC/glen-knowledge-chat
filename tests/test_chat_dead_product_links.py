"""Chat answers must not link a product page that is a 404 or a product we must not offer.

knowledge, 2026-11-23: asked "Does Lymph Flow contain NAC?", the chat answered "You may be
thinking of [Lymph Support](https://myhealingoasis.com/begin/product/lymph-support)". That
page is a 404, and Glen ruled on 2026-11-24 that Lymph Support is retired. Knowledge text
still names it in about 50 chunks, so the guard lives in the code every chat answer passes.
"""
from dashboard import legacy_store_links as lsl

BASE = "https://illtowell.com"
ENTRY = BASE + lsl.BROWSE_ENTRY_PATH

PRODUCTS = {
    "lymph-flow": {"name": "Lymph Flow"},
    "old-rescue": {"name": "Rescue (old)", "inactive": True, "superseded_by": "rescue"},
    "rescue": {"name": "Rescue"},
    "cds-activator": {"name": "CDS Activator", "inactive": True, "page_redirect": "cds-pair"},
    "cds-pair": {"name": "CDS pair"},
    "retired-thing": {"name": "Retired thing", "inactive": True},
}


def rw(text, products=PRODUCTS):
    return lsl.rewrite_text(text, BASE, products=products)


def stream(deltas, products=PRODUCTS):
    return "".join(lsl.rewrite_stream(iter(deltas), BASE, products=products))


def test_the_reported_answer_keeps_the_name_and_drops_the_dead_link():
    text = ("You may be thinking of "
            "[Lymph Support](https://myhealingoasis.com/begin/product/lymph-support).")
    assert rw(text) == "You may be thinking of Lymph Support."


def test_a_live_product_link_is_left_exactly_as_written():
    for text in ("[Lymph Flow](https://myhealingoasis.com/begin/product/lymph-flow)",
                 "[Lymph Flow](https://illtowell.com/begin/product/lymph-flow?utm_source=chat)",
                 "[Lymph Flow](/begin/product/lymph-flow)",
                 "See https://illtowell.com/begin/product/lymph-flow for more.",
                 "See /begin/product/lymph-flow."):
        assert rw(text) == text


def test_a_superseded_or_page_redirected_slug_counts_as_live():
    assert rw("[R](/begin/product/old-rescue)") == "[R](/begin/product/old-rescue)"
    assert rw("[C](/begin/product/cds-activator)") == "[C](/begin/product/cds-activator)"


def test_a_retired_or_unknown_slug_loses_its_link():
    assert rw("[Retired](https://illtowell.com/begin/product/retired-thing)") == "Retired"
    assert rw("[Gone](/begin/product/no-such-slug)") == "Gone"


def test_a_bare_dead_link_becomes_the_browse_page():
    text = "Order at https://myhealingoasis.com/begin/product/lymph-support."
    assert rw(text) == f"Order at {ENTRY}."
    assert rw("Try /begin/product/lymph-support today") == f"Try {ENTRY} today"


def test_a_do_not_recommend_product_loses_its_link():
    from dashboard.related_products import DO_NOT_RECOMMEND
    slug = sorted(DO_NOT_RECOMMEND)[0]
    products = dict(PRODUCTS, **{slug: {"name": "Not offered"}})
    assert rw(f"[Not offered](/begin/product/{slug})", products) == "Not offered"


def test_another_sites_product_path_is_not_touched():
    text = "[x](https://example.com/begin/product/lymph-support)"
    assert rw(text) == text


def test_an_empty_catalog_strips_nothing():
    text = "[Lymph Support](/begin/product/lymph-support)"
    assert rw(text, products={}) == text


def test_a_dead_link_split_across_stream_deltas_is_still_caught():
    deltas = ["You may be thinking of [Lymph Sup", "port](https://myhealing",
              "oasis.com/begin/prod", "uct/lymph-supp", "ort). It is retired."]
    assert stream(deltas) == "You may be thinking of Lymph Support. It is retired."
    bare = ["Order at https://illtowell.com/begin/pro", "duct/lymph-", "support now"]
    assert stream(bare) == f"Order at {ENTRY} now"
    live = ["Open /begin/product/lym", "ph-flow now"]
    assert stream(live) == "Open /begin/product/lymph-flow now"


def test_lymph_support_is_not_a_live_page_in_the_real_catalog():
    import json
    from pathlib import Path
    path = Path(__file__).resolve().parent.parent / "data" / "products.json"
    real = json.loads(path.read_text(encoding="utf-8"))["products"]
    assert not lsl.own_page_is_live("lymph-support", real)
    assert lsl.own_page_is_live("lymph-flow", real)
