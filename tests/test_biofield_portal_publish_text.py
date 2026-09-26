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


# Blind review round 1, 2026-09-25: each probe below gave a client wrong card text.
L2 = [{"remedy": "Vitality", "head": "Energy"}, {"remedy": "Chelation", "head": "Metals"}]


def test_a_numbered_list_elsewhere_does_not_capture_the_cards():
    narr = ("Aloha,\n\nThe first layer is energy, with Vitality.\n\nThe second is metals, "
            "with Chelation.\n\nNext steps:\n1. Order your remedies\n2. Book a follow-up\n\n"
            "In wellness")
    segs = bpp.segment_narrative(narr, L2)
    assert segs[0] == "The first layer is energy, with Vitality."
    assert segs[1].startswith("The second is metals, with Chelation.")


def test_a_numbered_terrain_paragraph_does_not_shift_the_cards():
    narr = ("1. Terrain: Phase 1 Energize.\n\n2. Vitality layer.\n\n3. Chelation layer.\n\n"
            "4. Nous Energy layer.\n\nIn wellness")
    segs = bpp.segment_narrative(narr, LAYERS3)
    assert "Vitality" in segs[0] and "Chelation" in segs[1] and "Nous Energy" in segs[2]
    assert "Terrain" not in segs[0]


def test_more_numbered_paragraphs_than_layers_is_not_trusted():
    # Not trusted as numbered, so each card still opens at its own layer. An extra
    # paragraph after the last layer lands on the last card, as the closing always has.
    narr = "1. Vitality.\n\n2. Chelation.\n\n3. Nous Energy.\n\n4. Calm extra.\n\nIn wellness"
    segs = bpp.segment_narrative(narr, LAYERS3)
    assert [s.split(".")[0] for s in segs] == ["Vitality", "Chelation", "Nous Energy"], segs


def test_windows_line_endings_split_at_paragraphs():
    narr = ("Aloha,\r\n\r\nThe first layer: energy with Vitality.\r\n\r\nSecond: metals, "
            "Chelation.\r\n\r\nThird: kidney, Nous Energy.\r\n\r\nIn wellness")
    segs = bpp.segment_narrative(narr, LAYERS3)
    assert segs[0] == "The first layer: energy with Vitality."
    assert segs[1] == "Second: metals, Chelation."
    assert segs[2].startswith("Third: kidney, Nous Energy.")


def test_a_sub_list_inside_a_layer_keeps_the_numbered_split():
    narr = ("1. First layer, Vitality. Steps:\n1. Take at breakfast\n2. Take at lunch\n\n"
            "2. Second, Chelation.\n\n3. Third, Nous Energy.")
    segs = bpp.segment_narrative(narr, LAYERS3)
    assert segs[0].startswith("First layer, Vitality.") and "Take at lunch" in segs[0]
    assert segs[1] == "Second, Chelation." and segs[2] == "Third, Nous Energy."


def test_other_number_styles_are_stripped():
    for a, b, c in (("**1.**", "**2.**", "**3.**"), ("1)", "2)", "3)")):
        narr = f"{a} Vitality here.\n\n{b} Chelation here.\n\n{c} Nous Energy here."
        segs = bpp.segment_narrative(narr, LAYERS3)
        assert segs == ["Vitality here.", "Chelation here.", "Nous Energy here."], segs


def test_numbers_that_run_1_to_n_but_miss_their_layer_are_not_trusted():
    narr = "1. Terrain first.\n\n2. Vitality layer.\n\n3. Chelation layer, then Nous Energy."
    segs = bpp.segment_narrative(narr, LAYERS3)
    assert "Terrain" not in segs[0] and "Vitality" in segs[0]



# Blind review round 2, 2026-09-25.
def test_blank_lines_holding_spaces_still_count_as_paragraph_breaks():
    narr = "Aloha,\n \nYour liver. Vitality.\n \nYour metals. Chelation.\n \nYour kidneys. Nous Energy."
    segs = bpp.segment_narrative(narr, LAYERS3)
    assert segs[0] == "Your liver. Vitality." and segs[1] == "Your metals. Chelation."


def test_a_markdown_heading_number_is_stripped():
    narr = "### 1. Vitality here.\n\n### 2. Chelation here.\n\n### 3. Nous Energy here."
    assert bpp.segment_narrative(narr, LAYERS3) == [
        "Vitality here.", "Chelation here.", "Nous Energy here."]


# Blind review round 3, 2026-09-25: two regressions against main.
def test_a_closing_action_list_does_not_replace_the_layer_text():
    one = [{"remedy": "Liver Flow", "head": "Liver"}]
    narr = ("Aloha Jane,\n\nYour liver is carrying old congestion. Liver Flow opens bile flow "
            "over six weeks.\n\nWhat to do:\n\n1. Take Liver Flow twice daily with food.\n"
            "2. Drink two litres of water.\n\nWarmly, Glen")
    seg = bpp.segment_narrative(narr, one)[0]
    assert "opens bile flow over six weeks" in seg, seg
    two = [{"remedy": "Liver Flow", "head": "Liver"}, {"remedy": "Kidney Tonic", "head": "Kidney"}]
    narr2 = ("Aloha,\n\nLiver Flow opens the liver.\n\nKidney Tonic supports the kidneys.\n\n"
             "Next steps:\n\n1. Start Liver Flow now.\n\n2. Add Kidney Tonic after two weeks.\n\nWarmly")
    segs = bpp.segment_narrative(narr2, two)
    assert segs[0].startswith("Liver Flow opens the liver.") and segs[1].startswith("Kidney Tonic supports")


def test_the_fallback_keeps_a_list_inside_a_card():
    two = [{"remedy": "Liver Flow", "head": "Liver"}, {"remedy": "Kidney Tonic", "head": "Kidney"}]
    narr = ("Dear Jane,\n\nLiver Flow opens the liver. Take it as follows:\n1. morning with food\n"
            "2. evening\n\nKidney Tonic supports kidneys. Steps:\n1. drink water\n2. rest")
    segs = bpp.segment_narrative(narr, two)
    assert "\n1. morning with food" in segs[0] and "\n1. drink water" in segs[1], segs
