from dashboard import biofield_portal_publish as bpp

def test_dosing_joins_present_fields_and_skips_blanks():
    assert bpp._dosing({"dosage": "1 capsule", "frequency": "daily",
                        "timing": "with food"}) == "1 capsule daily with food"
    assert bpp._dosing({"dosage": "10 drops", "frequency": "", "timing": ""}) == "10 drops"
    assert bpp._dosing({"dosage": "", "frequency": "", "timing": ""}) == ""

def test_segment_narrative_splits_layer_by_layer():
    layers = [{"remedy": "Vitality", "head": "ED3 Cell Driver"},
              {"remedy": "Chelation", "head": "Kidney"},
              {"remedy": "Nous Energy", "head": "Kidney"}]
    narr = ("Aloha Karin. The surface layer needs Vitality to restore energy. "
            "Next, Chelation clears the burden. Finally, Nous Energy steadies the mind.")
    segs = bpp.segment_narrative(narr, layers)
    assert len(segs) == 3
    assert "Vitality" in segs[0]
    assert "Chelation" in segs[1]
    assert "Nous Energy" in segs[2]

def test_segment_narrative_returns_empty_when_not_alignable():
    layers = [{"remedy": "Vitality", "head": "ED3"},
              {"remedy": "Chelation", "head": "Kidney"}]
    # Narrative never mentions the second remedy/head -> cannot align 1:1.
    assert bpp.segment_narrative("A generic message with no cues at all.", layers) == []
    assert bpp.segment_narrative("", layers) == []


# Clinical, 2026-09-25 (Peach Goddard a41): cutting at each remedy's name started every
# card mid-paragraph and ended it with the next layer's opening clause ("...with the
# capsule formula" / "Chelation recommended."). The writer numbers each layer's
# paragraph "1.", "2.", and those are the boundaries.
PEACH_LIKE = (
    "Aloha Peach,\n\nPhase 1, Energize: the reading located the terrain.\n\n"
    "1. The first layer addresses energy, with Vitality recommended. It restores cells.\n\n"
    "2. The second layer addresses heavy metal detoxification, with the capsule formula "
    "Chelation recommended. It helps clear the burden.\n\n"
    "3. The third layer addresses the kidney, with Nous Energy. It steadies the mind.\n\n"
    "If you tend to be highly sensitive, begin gently.\n\nIn wellness,\nDr. Glen & Rae")
LAYERS3 = [{"remedy": "Vitality", "head": "Energy"},
           {"remedy": "Chelation", "head": "Heavy Metals"},
           {"remedy": "Nous Energy", "head": "Kidney"}]


def test_cards_split_at_numbered_paragraphs_not_remedy_names():
    segs = bpp.segment_narrative(PEACH_LIKE, LAYERS3)
    assert segs[0] == "The first layer addresses energy, with Vitality recommended. It restores cells."
    assert segs[1] == ("The second layer addresses heavy metal detoxification, with the capsule "
                       "formula Chelation recommended. It helps clear the burden.")
    assert segs[2].startswith("The third layer addresses the kidney, with Nous Energy.")


def test_no_card_ends_with_the_next_layers_opening():
    segs = bpp.segment_narrative(PEACH_LIKE, LAYERS3)
    assert not any(s.rstrip().endswith(("with", "formula", ",")) for s in segs), segs
    assert all(s[0].isupper() for s in segs), segs


def test_the_last_card_keeps_the_closing_as_before():
    assert "In wellness," in bpp.segment_narrative(PEACH_LIKE, LAYERS3)[2]


def test_missing_or_out_of_order_numbers_fall_back_to_remedy_cues():
    no_numbers = PEACH_LIKE.replace("1. ", "").replace("2. ", "").replace("3. ", "")
    segs = bpp.segment_narrative(no_numbers, LAYERS3)
    assert len(segs) == 3 and "Chelation" in segs[1]
    # the older, unnumbered narratives: each card still opens at its paragraph
    assert segs[1].startswith("The second layer addresses heavy metal"), segs[1]
    assert not segs[0].rstrip().endswith("formula")
    swapped = PEACH_LIKE.replace("2. The second", "X").replace("3. The third", "2. The third")
    segs = bpp.segment_narrative(swapped.replace("X", "3. The second"), LAYERS3)
    assert len(segs) == 3


def test_a_numbered_dose_inside_a_paragraph_is_not_a_boundary():
    narr = PEACH_LIKE.replace("It restores cells.", "It restores cells. Take it at 2. pm")
    segs = bpp.segment_narrative(narr, LAYERS3)
    assert segs[1].startswith("The second layer")
