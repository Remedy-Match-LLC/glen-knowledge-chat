"""The client portal shows one card per layer, with all of that layer's remedies.

Glen, 2026-09-22: "one card per layer with all its remedies. It was built that way, but
some code has not respected that plan." build_portal_content emitted one card per remedy
ROW, numbered by row position, so a two-remedy layer showed as two cards.
"""
import sqlite3

from dashboard import analysis_autoconfirm as ac
from dashboard import biofield_portal_publish as bpp
from dashboard.biofield_authoring import add_chain_row, create_test

CATALOG = {"candida-cleanse": {"name": "Candida Cleanse"},
           "fungifuge": {"name": "Fungifuge"},
           "microbiome": {"name": "Microbiome"}}


def _peach(cx):
    tid = create_test(cx, "Peach", "peach@example.com", "2026-09-22")
    aid = f"a{tid}"
    add_chain_row(cx, aid, 1, "Muscle", "", "Candida Cleanse", "1 capsule", "daily", "")
    add_chain_row(cx, aid, 1, "Muscle", "", "Fungifuge", "2 capsules", "daily", "")
    add_chain_row(cx, aid, 2, "Colon", "", "Microbiome", "1 capsule", "twice daily", "")
    return aid


def test_a_two_remedy_layer_is_one_card():
    cx = sqlite3.connect(":memory:")
    c = bpp.build_portal_content(cx, _peach(cx), special_price_cents=4000,
                                 catalog=CATALOG)["content"]
    assert [(L["n"], L["title"]) for L in c["layers"]] == [(1, "Muscle"), (2, "Colon")]
    assert c["layers"][0]["remedy"] == "Candida Cleanse + Fungifuge"
    assert c["layers"][0]["dosing"] == ("Candida Cleanse: 1 capsule daily; "
                                        "Fungifuge: 2 capsules daily")
    assert c["layers"][1]["dosing"] == "1 capsule twice daily"
    # The order still lists every remedy.
    assert sorted(i["slug"] for i in c["reorder_items"]) == [
        "candida-cleanse", "fungifuge", "microbiome"]


def test_the_publish_check_resolves_each_remedy_of_a_layer():
    names = {"candida cleanse", "fungifuge", "neuro+ eye drops"}
    resolve = lambda r: r if r.lower() in names else None
    ok, why = ac.evaluate_quality(
        {"layers": [{"title": "Muscle", "remedy": "Candida Cleanse + Fungifuge",
                     "dosing": "x"},
                    {"title": "Eyes", "remedy": "Neuro+ Eye Drops", "dosing": "x"}]},
        resolve_slug=resolve, red_flag_terms=[])
    assert ok, why
    ok, why = ac.evaluate_quality(
        {"layers": [{"title": "Muscle", "remedy": "Candida Cleanse + Nonesuch",
                     "dosing": "x"}]},
        resolve_slug=resolve, red_flag_terms=[])
    assert not ok and why == ["layer 0: remedy not in catalog ('Nonesuch')"]
