"""Old GrooveKart store links (remedymatch.com) in chatbot answers.

Knowledge-base text still carries remedymatch.com product links, and only a prompt rule
kept the chat from repeating them. Glen, 2026-09-14: "Update chatbot to the new product
pages." These pin the rewrite: a mapped product link goes to its new page; any other store
link keeps its markdown text, or as a bare link becomes the browse entry page; and the brand
name, the support address and go.remedymatch.com are left alone. Answers stream, so a link
split across deltas must still be caught.
"""
from dashboard import legacy_store_links as lsl

BASE = "https://illtowell.com"
ENTRY = BASE + lsl.BROWSE_ENTRY_PATH

# A small catalog in the real shape: the old address lives in `url`, the id is the number
# at the start of the last path segment, and a retired record may name a successor.
PRODUCTS = {
    "nous-energy": {"name": "Nous Energy",
                    "url": "https://remedymatch.com/remedies/syntropy/85-nous-energy"},
    "terrain-restore": {"name": "Terrain Restore",
                        "url": "https://remedymatch.com/remedies/syntropy/113-terrain-restore"},
    "coq10": {"name": "CoQ10", "url": "https://remedymatch.com/remedies/503-coq10"},
    "old-rescue": {"name": "Rescue (old)", "inactive": True, "superseded_by": "rescue",
                   "url": "https://remedymatch.com/remedies/syntropy/232-rescue"},
    "rescue": {"name": "Rescue"},
    # Its stored url carries a different id than the knowledge base's link does.
    "microbiome": {"name": "Microbiome",
                   "url": "https://remedymatch.com/remedies/syntropy/610-microbiome"},
    # Retired, no successor, and NOT on the do-not-recommend list.
    "molecular-hydrogen-tablets": {
        "name": "Molecular Hydrogen Tablets", "inactive": True,
        "url": "https://remedymatch.com/remedies/378-molecular-hydrogen-tablets"},
}


def rw(text, products=PRODUCTS):
    return lsl.rewrite_text(text, BASE, products=products)


def stream(deltas):
    return "".join(lsl.rewrite_stream(iter(deltas), BASE, products=PRODUCTS))


# ── the mapping ───────────────────────────────────────────────────────────────

def test_mapping_joins_on_the_product_id():
    m = lsl.build_map(PRODUCTS)
    assert m["85"] == "/begin/product/nous-energy"
    assert m["503"] == "/begin/product/coq10"


def test_mapping_follows_a_retired_record_to_its_successor():
    assert lsl.build_map(PRODUCTS)["232"] == "/begin/product/rescue"


def test_mapping_marks_a_retired_record_with_no_successor_as_unmapped():
    m = lsl.build_map(PRODUCTS)
    assert "378" in m and m["378"] is None


def test_the_real_catalog_named_cases():
    from dashboard import products as _p
    m = lsl.build_map(_p.load_products())
    assert m["85"] == "/begin/product/nous-energy"
    assert m["378"] is None          # molecular-hydrogen-tablets, retired
    assert m["542"] is None          # electrolyte-mineral-manna, do not recommend
    assert sum(1 for v in m.values() if v) >= 290


# ── do-not-recommend: never linked, whatever the inactive flag says ──────────
# Glen on Electrolyte Mineral Manna: "not discontinued but not being promoted by chat."

DNR_ACTIVE = dict(PRODUCTS, **{
    "electrolyte-mineral-manna": {
        "name": "Electrolyte Mineral Manna",
        "url": "https://remedymatch.com/remedies/syntropy/542-electrolyte-mineral-manna"},
})


def test_an_active_do_not_recommend_product_is_not_mapped():
    assert lsl.build_map(DNR_ACTIVE)["542"] is None


def test_an_active_do_not_recommend_product_is_not_linked():
    url = "https://remedymatch.com/remedies/syntropy/542-electrolyte-mineral-manna"
    assert rw(f"Try [EMM]({url}).", DNR_ACTIVE) == "Try EMM."
    assert rw(f"Try {url} today", DNR_ACTIVE) == f"Try {ENTRY} today"


def test_do_not_recommend_is_also_blocked_through_the_slug_fallback():
    url = "https://remedymatch.com/remedies/syntropy/9999-electrolyte-mineral-manna"
    products = {k: v for k, v in DNR_ACTIVE.items()}
    products["electrolyte-mineral-manna"] = {"name": "Electrolyte Mineral Manna"}
    assert rw(f"[EMM]({url})", products) == "EMM"


# ── rewriting text ────────────────────────────────────────────────────────────

def test_markdown_product_link_goes_to_the_new_page():
    assert rw("Try [Nous Energy](https://remedymatch.com/remedies/syntropy/85-nous-energy) daily.") == \
        f"Try [Nous Energy]({BASE}/begin/product/nous-energy) daily."


def test_bare_product_link_goes_to_the_new_page():
    assert rw("See https://remedymatch.com/remedies/503-coq10 for details") == \
        f"See {BASE}/begin/product/coq10 for details"


def test_query_string_and_trailing_punctuation():
    out = rw("Order here: https://remedymatch.com/remedies/syntropy/113-terrain-restore?utm=x.")
    assert out == f"Order here: {BASE}/begin/product/terrain-restore."
    out = rw("(https://remedymatch.com/remedies/503-coq10)")
    assert out == f"({BASE}/begin/product/coq10)"


def test_bare_path_without_scheme():
    assert rw("at remedymatch.com/remedies/503-coq10 today") == \
        f"at {BASE}/begin/product/coq10 today"


def test_www_and_uppercase_host():
    assert rw("https://www.RemedyMatch.com/remedies/503-coq10") == f"{BASE}/begin/product/coq10"


def test_retired_markdown_link_keeps_its_text_and_drops_the_link():
    assert rw("Avoid [MHT](https://remedymatch.com/remedies/378-molecular-hydrogen-tablets).") \
        == "Avoid MHT."


def test_unknown_id_markdown_keeps_text_and_bare_goes_to_the_entry_page():
    assert rw("Buy [Mystery](https://remedymatch.com/remedies/999-mystery) now") == "Buy Mystery now"
    assert rw("Link: https://remedymatch.com/remedies/999-mystery") == f"Link: {ENTRY}"


def test_platforms_sentences_never_leave_broken_prose():
    assert rw("Shop at https://remedymatch.com or remedymatch.com.") == \
        f"Shop at {ENTRY} or remedymatch.com."
    assert rw("Browse https://remedymatch.com/remedies/syntropy today.") == \
        f"Browse {ENTRY} today."


def test_other_store_pages():
    assert rw("Browse [the store](https://remedymatch.com/) or https://remedymatch.com/info/terms") \
        == f"Browse the store or {ENTRY}"


def test_brand_email_and_ghl_are_left_alone():
    text = ("Shop RemedyMatch.com or write support@remedymatch.com. "
            "Book at https://go.remedymatch.com/widget/bookings/x or go.remedymatch.com/y.")
    assert rw(text) == text


def test_other_links_are_left_alone():
    text = "[Terrain Restore](https://illtowell.com/begin/product/terrain-restore) and https://amzn.to/abc"
    assert rw(text) == text


def test_empty_and_none():
    assert rw("") == ""
    assert lsl.rewrite_text(None, BASE, products=PRODUCTS) == ""


# ── streaming ─────────────────────────────────────────────────────────────────

def test_link_split_across_two_deltas():
    out = stream(["Try https://remedymatch.com/remed", "ies/503-coq10 today."])
    assert out == f"Try {BASE}/begin/product/coq10 today."


def test_link_split_across_three_deltas_inside_markdown():
    out = stream(["Take [Nous En", "ergy](https://remedy", "match.com/remedies/syntropy/85-nous-energy) now"])
    assert out == f"Take [Nous Energy]({BASE}/begin/product/nous-energy) now"


def test_host_split_right_after_the_scheme():
    out = stream(["See https://", "remedymatch.com/remedies/503-coq10\n"])
    assert out == f"See {BASE}/begin/product/coq10\n"


def test_stream_ending_mid_link_is_flushed_rewritten():
    out = stream(["Order at https://remedymatch.com/remedies/syntropy/113-terr", "ain-restore"])
    assert out == f"Order at {BASE}/begin/product/terrain-restore"


def test_unmapped_markdown_split_keeps_text_only():
    out = stream(["Skip [M", "HT](https://remedymatch.com/remedies/378-mol", "ecular-hydrogen-tablets)."])
    assert out == "Skip MHT."


def test_plain_text_is_not_held_back():
    chunks = list(lsl.rewrite_stream(iter(["Hello ", "there, ", "friend."]), BASE, products=PRODUCTS))
    assert "".join(chunks) == "Hello there, friend."
    assert chunks[0] == "Hello "  # emitted as it arrived, not buffered to the end


def test_stream_drains_the_input():
    seen = []

    def gen():
        for t in ["a ", "https://remedymatch.com/remedies/503-coq10", " b"]:
            seen.append(t)
            yield t
    "".join(lsl.rewrite_stream(gen(), BASE, products=PRODUCTS))
    assert len(seen) == 3


# ── second step: exact slug, only when the id is unknown ─────────────────────
# Knowledge's real link list: 18 ids the catalog does not carry, 11 of which name a live
# product exactly (73-microbiome). Anything fuzzier stays unmapped.

def test_unknown_id_whose_name_is_exactly_a_live_slug_maps_by_slug():
    assert rw("[Microbiome](https://remedymatch.com/remedies/syntropy/73-microbiome)") == \
        f"[Microbiome]({BASE}/begin/product/microbiome)"


def test_unknown_id_with_no_exact_slug_keeps_text_only():
    assert rw("Try [Neuro Magnesium](https://remedymatch.com/remedies/80-neuromagnesium).") == \
        "Try Neuro Magnesium."


def test_a_known_retired_id_does_not_fall_back_to_its_slug():
    products = dict(PRODUCTS, **{"molecular-hydrogen-tablets-2": {"name": "MHT 2"}})
    out = lsl.rewrite_text("[MHT](https://remedymatch.com/remedies/378-molecular-hydrogen-tablets)",
                           BASE, products=products)
    assert out == "MHT"


def test_the_real_catalog_slug_fallback_and_near_misses():
    from dashboard import products as _p
    cat = _p.load_products()
    out = lsl.rewrite_text("https://remedymatch.com/remedies/syntropy/73-microbiome "
                           "http://remedymatch.com/remedies/syntropy/250-free-easy "
                           "https://remedymatch.com/remedies/80-neuromagnesium", BASE, products=cat)
    assert out.split(" ") == [f"{BASE}/begin/product/microbiome", ENTRY, ENTRY]


# ── the URL classifier the match card uses ───────────────────────────────────

def test_is_legacy_store_url_keeps_its_contract():
    assert lsl.is_legacy_store_url("https://remedymatch.com/remedies/x")
    assert lsl.is_legacy_store_url("http://RemedyMatch.com?controller=search")
    assert not lsl.is_legacy_store_url("")
    assert not lsl.is_legacy_store_url(None)
    assert not lsl.is_legacy_store_url("https://illtowell.com/begin/product/x")
