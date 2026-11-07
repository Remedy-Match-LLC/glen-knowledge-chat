"""Add to plan: joined, related or added (Glen, 2026-11-07).

"Each one added adds its stress patterns that are already covered to the layers
that cover them from other sections already entered." See dashboard/biofield_add_to_plan."""
from dashboard import biofield_add_to_plan as atp
from dashboard.biofield_reveal_import import harmony_laser_for


def _row(rid, layer, head, remedy, tail="", codes=(), origin="live"):
    return {"id": rid, "layer": layer, "head": head, "most_affected": tail,
            "remedy": remedy, "codes": list(codes), "origin": origin}


CHAIN = [_row(1, 1, "Gut", "Alkalize Powder", tail="Gut", codes=["ED2"]),
         _row(2, 2, "Liver and gallbladder", "Liver Life", origin="clinical"),
         _row(3, 3, "Thyroid", "ES1 Lymph and Immune Energetic Star Infoceutical",
              origin="clinical")]


def test_the_same_product_joins_its_codes_and_tail_onto_the_layer_and_adds_no_row():
    got = atp.classify(CHAIN, [{"title": "Kidney Health", "most_affected": "Kidney, Bladder",
                                "remedy_name": "Alkalize Powder", "codes": ["ED2", "ED12"]}],
                       last_layer=3)
    assert got["add"] == [] and got["related"] == []
    j = got["joined"][0]
    assert j["into"] == 1 and j["into_rid"] == 1
    assert j["codes"] == ["ED12"]                      # ED2 is already on the row
    assert j["tail"] == "Gut, Kidney, Bladder"


def test_the_same_product_matches_by_catalog_identity_not_spelling():
    key = lambda n: "es1" if "es1" in str(n).lower() else str(n or "").strip().lower()
    got = atp.classify(CHAIN, [{"title": "Lymph", "remedy_name": "ES1 Immune",
                                "codes": ["ES1"]}], key=key, last_layer=3)
    assert got["joined"][0]["into"] == 3 and got["joined"][0]["codes"] == ["ES1"]


def test_a_related_heading_with_another_product_is_a_proposal_numbered_after_the_chain():
    got = atp.classify(CHAIN, [{"title": "Liver Detoxification and Support",
                                "remedy_name": "Detox Daily", "codes": ["ED9"]}],
                       last_layer=3)
    assert got["joined"] == [] and got["add"] == []
    rel = got["related"][0]
    assert rel["rule"] == "same heading" and rel["into"] == 2 and rel["into_rids"] == [2]
    assert rel["n"] == 4 and rel["layer"]["n"] == 4
    assert rel["into_remedies"] == ["Liver Life"]


def test_a_ticked_recommended_infoceutical_relates_to_the_layer_holding_it():
    got = atp.classify(CHAIN, [{"title": "Immune Response", "remedy_name": "Immune Plus",
                                "codes": ["ES1", "MR3"]}],
                       recommended={"ES1"}, last_layer=3)
    rel = got["related"][0]
    assert rel["rule"] == "recommended infoceutical already ticked"
    assert rel["into"] == 3 and rel["why"] == "ES1"


def test_a_code_ticked_only_in_the_clinical_summary_relates_to_that_rows_layer():
    chain = [_row(1, 1, "Gut", "Alkalize Powder", tail="Gut")]
    got = atp.classify(chain, [{"title": "Kidney", "remedy_name": "Kidney Kare",
                                "codes": ["ED12"]}],
                       recommended={"ED12"},
                       ticked_names={"gut": ["ED12 Kidney Energetic Driver Infoceutical"]})
    assert got["related"][0]["into"] == 1


def test_an_unrecommended_code_is_not_related():
    got = atp.classify(CHAIN, [{"title": "Immune Response", "remedy_name": "Immune Plus",
                                "codes": ["ES1"]}], recommended=set(), last_layer=3)
    assert got["related"] == [] and got["add"][0]["n"] == 4


def test_unrelated_layers_are_added_in_order_after_the_last_stored_layer():
    got = atp.classify(CHAIN, [{"title": "Kidney Health", "remedy_name": "Kidney Kare"},
                               {"title": "Bone Density", "remedy_name": "Bone Builder"}],
                       last_layer=7)
    assert [(L["title"], L["n"]) for L in got["add"]] == [("Kidney Health", 8),
                                                          ("Bone Density", 9)]


def test_a_repeat_within_one_click_joins_the_earlier_new_layer():
    got = atp.classify([], [{"title": "Kidney", "most_affected": "Kidney",
                             "remedy_name": "Kidney Kare", "codes": ["ED12"]},
                            {"title": "Bladder", "most_affected": "Bladder",
                             "remedy_name": "Kidney Kare", "codes": ["ED13"]}])
    assert len(got["add"]) == 1
    assert got["add"][0]["codes"] == ["ED12", "ED13"]
    assert got["add"][0]["most_affected"] == "Kidney, Bladder"
    assert got["joined"][0]["into_new"] == 1


def test_a_resolved_name_is_compared():
    got = atp.classify(CHAIN, [{"title": "X", "remedy_name": "alkalize"}],
                       resolve=lambda n: "Alkalize Powder")
    assert got["joined"][0]["into"] == 1


def test_a_join_that_puts_a_thyroid_code_on_a_laser_layer_is_proposed_not_joined():
    chain = [_row(1, 1, "Cells", "Rejuvenator Max", codes=["ER5"], origin="scan"),
             _row(2, 1, "Cells", "Harmony Laser", codes=["ER5"], origin="scan")]
    got = atp.classify(chain, [{"title": "Thyroid", "remedy_name": "Rejuvenator Max",
                                "codes": ["ER33"]}], laser_for=harmony_laser_for,
                       last_layer=1)
    assert got["joined"] == []
    assert got["related"][0]["rule"] == "same remedy"


def test_a_joined_layer_brings_its_laser_only_when_the_union_allows_one():
    laser = {"name": "Harmony Laser", "caution": "x"}
    chain = [_row(1, 1, "Cells", "Rejuvenator Max", codes=["ER5"], origin="scan")]
    ok = atp.classify(chain, [{"title": "Cells", "remedy_name": "Rejuvenator Max",
                               "codes": ["ER7"], "laser": laser}],
                      laser_for=harmony_laser_for)
    assert ok["joined"][0]["laser"] == laser
    chain[0]["codes"] = ["ER33"]
    blocked = atp.classify(chain, [{"title": "Cells", "remedy_name": "Rejuvenator Max",
                                    "codes": ["ER7"], "laser": laser}],
                           laser_for=harmony_laser_for)
    assert blocked["joined"][0]["laser"] is None


def test_codes_stored_as_json_are_read():
    chain = [dict(CHAIN[0], codes='["ED2"]')]
    got = atp.classify(chain, [{"title": "x", "remedy_name": "Alkalize Powder",
                                "codes": ["ED2"]}])
    assert got["joined"][0]["codes"] == []


def test_a_landed_live_layer_related_by_heading_is_proposed():
    rows = CHAIN + [_row(9, 4, "Liver Detoxification", "Detox Daily")]
    got = atp.related_after_landing(rows, [9])
    assert len(got) == 1
    assert got[0]["source_rids"] == [9] and got[0]["into_rids"] == [2]
    assert got[0]["rule"] == "same heading"


def test_a_landed_row_on_an_existing_layer_or_a_repeated_product_is_not_proposed():
    on_layer = CHAIN + [_row(9, 2, "Liver and gallbladder", "Detox Daily")]
    assert atp.related_after_landing(on_layer, [9]) == []
    # An anchor row with no remedy: the new row still joins that layer, not a new one.
    anchor = [_row(1, 1, "Liver and gallbladder", ""),
              _row(9, 1, "Liver and gallbladder", "Detox Daily")]
    assert atp.related_after_landing(anchor, [9]) == []
    repeat = CHAIN + [_row(9, 4, "Liver Detoxification", "Liver Life")]
    assert atp.related_after_landing(repeat, [9]) == []
    unrelated = CHAIN + [_row(9, 4, "Kidney Health", "Kidney Kare")]
    assert atp.related_after_landing(unrelated, [9]) == []


def test_a_held_related_layer_takes_no_number_and_leaves_no_gap():
    # Glen, 2026-11-07: "yes, hold them in the list."
    got = atp.classify(CHAIN, [{"title": "Liver Detoxification", "remedy_name": "Detox Daily"},
                               {"title": "Kidney Health", "remedy_name": "Kidney Kare"}],
                       last_layer=3, hold_related=True)
    assert got["related"][0]["n"] is None and got["related"][0]["layer"]["n"] is None
    assert [L["n"] for L in got["add"]] == [4]
