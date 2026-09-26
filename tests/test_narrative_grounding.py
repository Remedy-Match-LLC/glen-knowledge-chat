"""The Biofield narrative may only name what the client's chain supplies.

Clinical, 2026-09-25, from Donna Banks (a43) and Peach Goddard (a41):
1. It named Liver Support, which is on no chain row. It came from the scan block, whose
   findings end "Consider: Liver Support, Free & Easy".
2. It wrote "E4L voice scan". Glen, 2026-09-18: E4L's instrument is the Bioenergetic
   Wellness Scan and a bare "voice scan" never appears in client copy.
3. It credited B17 Max with vitamins A, B6, B9, B12 and D3, which only B17 Syntropy
   contains, by naming both remedies in one sentence.
"""
from dashboard import narrative_grounding as ng

ING = {
    "B17 Syntropy": ["Vitamin A (Retinyl Palmitate)", "Vitamin B6 (P5P)", "Vitamin B9 (5-MTHF)",
                     "Vitamin B12 (Cobalamins)", "Vitamin D3 (Cholecalciferol)",
                     "Zinc (Zinc Orotate)", "Magnesium (Magnesium Ascorbate)"],
    "B17 Max": ["Amygdalin, [Laetrile: drug name], B17", "Pangamic Acid"],
    "AngiogenX": ["Vitamin D3: Cholecalciferol", "Honokiol", "Curcumin 95% HPLC"],
    "Apoptogenesis": ["Vitamin C (Ascorbyl Palmitate)", "Magnesium (Magnesium Taurate 8%)",
                      "Zinc (Zinc Carnosine 23%)", "Curcumin (Curcuma longa)"],
}
CATALOG_INGREDIENTS = [i for v in ING.values() for i in v] + ["Coenzyme Q10", "Silymarin"]
CATALOG_NAMES = list(ING) + ["Liver Support", "Free & Easy", "Transform", "Microbiome"]


def _check(text, allowed_text="", chain=None, heads=""):
    return ng.check_narrative(
        text, chain=chain or list(ING), ingredients=ING, catalog_names=CATALOG_NAMES,
        allowed_text=allowed_text, heads_text=heads)


# ── scan block cleaning ─────────────────────────────────────────────────────

def test_consider_lines_and_source_tags_are_stripped():
    desc = ("Liver\nLarge intestine\nConsider: Liver Support, Free & Easy\n\n"
            "Primarily the liver and large intestine.\n\n"
            "[SOURCED: FMP vl_bio_main_stress (category NES), extract 2026-05-23, Glen-authored]")
    out = ng.clean_scan_description(desc)
    assert "Liver Support" not in out and "Consider" not in out and "SOURCED" not in out
    assert "Primarily the liver and large intestine." in out


def test_consider_mid_line_is_stripped_case_insensitively():
    assert ng.clean_scan_description("Spleen.  consider: Immune Modulation") == "Spleen."


# ── scan names ──────────────────────────────────────────────────────────────

def test_e4l_voice_scan_becomes_bioenergetic_wellness_scan():
    t = "Your recent E4L voice scan corroborates this. The E4L Voice Scan showed it."
    assert ng.fix_scan_names(t) == (
        "Your recent Bioenergetic Wellness Scan corroborates this. "
        "The Bioenergetic Wellness Scan showed it.")


def test_bare_voice_scan_and_voice_analysis_are_renamed():
    assert ng.fix_scan_names("the recent voice scan") == "the recent Bioenergetic Wellness Scan"
    assert ng.fix_scan_names("Bioenergetic Voice Analysis") == "Bioenergetic Wellness Scan"


def test_five_element_voice_scan_is_left_alone():
    t = "Glen's Five Element Voice Scan and the five element voice scan"
    assert ng.fix_scan_names(t) == t


def test_no_voice_scan_survives_in_any_case():
    for t in ("VOICE SCAN", "Voice scans", "voice-scan"):
        assert "voice" not in ng.fix_scan_names(t).lower(), t


# ── off-chain products ──────────────────────────────────────────────────────

def test_off_chain_product_is_flagged():
    probs = _check("2. Liver Support, taken later in the day, aids liver health.")
    assert any("Liver Support" in p for p in probs)


def test_chain_remedy_is_not_flagged():
    assert _check("1. B17 Max supports the growth layer.") == []


def test_product_named_in_the_writers_input_is_allowed():
    assert _check("Transform supports the gut.", allowed_text="remedy: Transform") == []


def test_lowercase_common_word_is_not_a_product():
    assert _check("Balancing these patterns helps the body transform its function.") == []


# ── ingredients ─────────────────────────────────────────────────────────────

def test_ingredients_credited_to_two_remedies_are_flagged():
    probs = _check("B17 Syntropy and B17 Max support pathways including vitamins A, B6, "
                   "B9, B12, and D3.")
    assert any("B17 Max" in p and "Vitamin A" in p for p in probs), probs


def test_ingredient_absent_from_the_named_remedy_is_flagged():
    probs = _check("AngiogenX provides vitamin C, magnesium, and zinc.")
    joined = " | ".join(probs).lower()
    assert "angiogenx" in joined
    for n in ("vitamin c", "magnesium", "zinc"):
        assert n in joined, n


def test_correct_single_remedy_sentence_passes():
    assert _check("Apoptogenesis provides vitamin C, magnesium and zinc.") == []
    assert _check("B17 Syntropy supplies vitamins A, B6, B9, B12 and D3.") == []


def test_shared_ingredient_across_two_remedies_passes():
    # Vitamin D3 is in both, so naming both is true.
    assert _check("B17 Syntropy and AngiogenX both carry vitamin D3.") == []


def test_pronoun_sentence_inherits_the_previous_remedy():
    probs = _check("AngiogenX supports circulation. It also provides zinc.")
    assert any("AngiogenX" in p and "zinc" in p.lower() for p in probs), probs


def test_ingredient_from_outside_the_chain_is_flagged():
    probs = _check("B17 Max supports energy with ingredients like Coenzyme Q10.")
    assert any("Coenzyme Q10" in p for p in probs), probs
    # Known gap, accepted in review round 2: a bare "with" is not read as a claim,
    # because "taken with raw honey" would be.
    assert _check("B17 Max supports energy with Coenzyme Q10.") == []


def test_ingredient_without_any_remedy_in_paragraph_is_not_flagged():
    assert _check("Zinc supports many enzymes.") == []


def test_tail_words_are_not_treated_as_ingredients():
    # "Silymarin" is an ingredient term; if a head/tail uses the word it is anatomy or
    # a named area, not a nutrient claim.
    assert _check("B17 Max sits beside Silymarin Terrain.", heads="Silymarin Terrain") == []


def test_a_clean_zero_is_not_blindness():
    # The checker must see this obvious fault, or every zero above means nothing.
    assert _check("B17 Max contains vitamin A.") != []


# ── found on the 36 saved narratives, 2026-09-25: both were false alarms ─────

def test_nutrient_synonym_counts_as_contained():
    assert _check("B17 Syntropy provides folate and cobalamin.") == []


def test_synonym_still_fails_on_the_wrong_remedy():
    probs = _check("B17 Max provides folate.")
    assert any("B17 Max" in p and "folate" in p.lower() for p in probs), probs


def test_another_catalog_spelling_of_a_chain_remedy_is_not_off_chain():
    probs = ng.check_narrative(
        "The use of Clear Lens Eyedrops supports eye health.",
        chain=["Clear Lens Eye Drops ACES+CAT Eye Drops"], ingredients={},
        catalog_names=["Clear Lens Eyedrops", "Clear Lens+ Eye Drops ACES+CAT Eye Drops"], allowed_text="")
    assert probs == []


# ── blind review round 1, 2026-09-25 ────────────────────────────────────────

def test_a_remedy_with_no_known_ingredients_is_not_checked():
    # Unknown is not wrong: 894 of 1,092 catalog products carry no ingredient list.
    probs = ng.check_narrative(
        "Sterol Max supports the acid layer. Leafy greens rich in magnesium help too.",
        chain=["Sterol Max"], ingredients={"Sterol Max": []}, catalog_names=["Sterol Max"], allowed_text="")
    assert probs == []


def test_a_one_word_product_at_a_sentence_start_is_ordinary_prose():
    probs = ng.check_narrative(
        "Sleep is when repair happens. Comfort comes as the layer settles.",
        chain=["B17 Max"], ingredients={}, catalog_names=["Sleep", "Comfort"], allowed_text="")
    assert probs == []


def test_a_one_word_product_is_flagged_only_when_it_looks_like_a_brand():
    # Review round 2: 82 one-word catalog names ("Energy", "Clarity") are ordinary words.
    names = ["Comfort", "AngiogenX", "OcuHeal+"]
    probs = ng.check_narrative(
        "Your layer responds to Comfort at night, to AngiogenX, and to OcuHeal+ drops.",
        chain=["B17 Max"], ingredients={}, catalog_names=names, allowed_text="")
    assert not any("Comfort" in p for p in probs)
    assert any("AngiogenX" in p for p in probs) and any("OcuHeal+" in p for p in probs)


def test_herb_common_names_match_their_label_names():
    probs = ng.check_narrative(
        "Liver Support supports your liver with milk thistle and turmeric.",
        chain=["Liver Support"], ingredients={"Liver Support": ["Silymarin", "Curcumin"]},
        catalog_names=["Liver Support"],
        allowed_text="")
    assert probs == []


def test_five_elements_plural_keeps_its_name():
    t = "the Five Elements Voice Scan"
    assert ng.fix_scan_names(t) == t


# ── blind review round 1, accuracy reviewer, 2026-09-25 ─────────────────────

def _chk(text, chain, ing=None, names=None, ingredients=None, heads=""):
    return ng.check_narrative(text, chain=chain, ingredients=ing or {},
                              catalog_names=names or [],
                              allowed_text=heads, heads_text=heads)


def test_a_remedy_with_no_ingredients_may_not_be_credited_with_nutrients():
    probs = _chk("ED11 Liver Driver supplies vitamin C and zinc.", ["ED11 Liver Driver"],
                 {"ED11 Liver Driver": []}, ingredients=["Zinc (Orotate)"])
    assert any("ED11 Liver Driver" in p for p in probs), probs


def test_only_a_pronoun_carries_the_remedy_to_the_next_sentence():
    ing = {"Sterol Max": []}
    assert _chk("Sterol Max supports the acid layer. Leafy greens rich in magnesium help too.",
                ["Sterol Max"], ing, ingredients=["Magnesium"]) == []
    assert _chk("Sterol Max supports the acid layer. It supplies magnesium.",
                ["Sterol Max"], ing, ingredients=["Magnesium"]) != []


def test_ampersand_and_and_are_the_same_product_name():
    probs = _chk("Free and Easy can also help.", ["B17 Max"], names=["Free & Easy"])
    assert any("Free & Easy" in p for p in probs), probs


def test_bare_vitamin_codes_and_short_nutrients_are_seen():
    ing = {"B17 Max": ["Amygdalin, [Laetrile: drug name], B17", "Pangamic Acid"]}
    assert _chk("B17 Max provides B12 and B6.", ["B17 Max"], ing) != []
    assert _chk("B17 Max provides DHA.", ["B17 Max"], ing, ingredients=["DHA (Algal Oil)"]) != []


def test_vitamin_b17_is_amygdalin():
    ing = {"B17 Max": ["Amygdalin, [Laetrile: drug name], B17", "Pangamic Acid"]}
    assert _chk("B17 Max provides vitamin B17 and vitamin B15.", ["B17 Max"], ing) == []


def test_a_negated_nutrient_is_not_a_claim():
    ing = {"B17 Max": ["Amygdalin"]}
    assert _chk("B17 Max supports growth. It contains no curcumin.", ["B17 Max"], ing,
                ingredients=["Curcumin"]) == []


def test_scan_name_variants_do_not_double_words():
    assert ng.fix_scan_names("Your E4L Bioenergetic Voice Scan showed stress.") == (
        "Your Bioenergetic Wellness Scan showed stress.")
    assert ng.fix_scan_names("an E4L voice scanning session") == (
        "a Bioenergetic Wellness Scan session") or "voice" not in ng.fix_scan_names(
        "an E4L voice scanning session").lower()
    assert "voice" not in ng.fix_scan_names("the E4L voice analysis").lower()


# ── found re-running the stricter check on the 36 saved letters ─────────────

def test_a_chain_remedy_shortened_to_a_nutrient_word_is_not_a_claim():
    ing = {"MSM Powder": ["MSM"], "Scar Silk": ["Gotu kola"]}
    assert _chk("Scar Silk restores tissue. These will support healing, along with the "
                "MSM on layer 1.", ["MSM Powder", "Scar Silk"], ing,
                ingredients=["MSM", "Gotu kola"]) == []


def test_a_catalog_name_inside_a_chain_label_is_that_ingredient():
    ing = {"B17 Syntropy": ["Vitamin B9 (5-MTHF)"]}
    assert _chk("Its folate comes as 5-MTHF, the active form.", ["B17 Syntropy"], ing,
                names=["5-MTHF"]) == []


# ── blind review round 2, client-visible text, 2026-09-25 ───────────────────

def test_a_longer_catalog_name_containing_a_chain_name_is_a_different_product():
    for chain, text, name in (("OcuHeal", "Begin OcuHeal+ Eye Drops tonight.", "OcuHeal+ Eye Drops"),
                              ("ES1", "Add ES15 Heavy Metals Energetic Star.", "ES15 Heavy Metals Energetic Star"),
                              ("Energy", "Take Nous Energy daily.", "Nous Energy")):
        probs = _chk(text, [chain], names=[name])
        assert any(name in p for p in probs), (chain, probs)


def test_the_formula_and_this_remedy_carry_the_remedy_forward():
    ing = {"B17 Max": ["Amygdalin"]}
    assert _chk("B17 Max supports the terrain. The formula also supplies zinc.",
                ["B17 Max"], ing, ingredients=["Zinc"]) != []
    assert _chk("B17 Max supports the terrain.\n\nThis remedy brings zinc.",
                ["B17 Max"], ing, ingredients=["Zinc"]) != []


def test_common_botanicals_are_seen_without_a_catalog_label():
    ing = {"B17 Max": ["Amygdalin"]}
    for herb in ("quercetin", "ashwagandha", "rhodiola"):
        assert _chk(f"B17 Max provides {herb}.", ["B17 Max"], ing) != [], herb


def test_five_element_voice_scan_survives_formatting():
    for t in ("Your **Five Element** Voice Scan", "the Five Element\nVoice Scan",
              "the Five Elements' Voice Scan", "Glen's own voice scan (Five Element)"):
        assert ng.fix_scan_names(t) == t, t


def test_plural_voice_scans_stay_plural():
    assert ng.fix_scan_names("Your two voice scans show") == (
        "Your two Bioenergetic Wellness Scans show")



# ── blind review round 2, cost reviewer: correct clinical prose, no flags ────

CORRECT = [
    "EI8 Microbes-Liver Integrator supports the balance between the microbiome and the liver.",
    "Rose Quartz Gem Elixir is made in Terrain Restore.",
    "We are in Phase 1, Energize, where the focus is Energy and circulation.",
    "B17 Max works best alongside raw honey and pure water.",
    "Kale and dandelion greens support the liver alongside B17 Max.",
]


def test_correct_clinical_sentences_raise_no_flags():
    for t in CORRECT:
        probs = ng.check_narrative(
            t, chain=["B17 Max", "EI8 Microbes-Liver Integrator Infoceutical",
                      "Rose Quartz Gem Elixir"],
            ingredients={"B17 Max": ["Amygdalin"]},
            catalog_names=["Energy", "Terrain Restore", "Microbiome"],
            allowed_text="Terrain Restore")
        assert probs == [], (t, probs)


def test_an_infoceutical_short_name_is_its_long_catalog_name():
    probs = ng.check_narrative(
        "ED11 Liver Driver supports liver flow.",
        chain=["ED11 Liver Energetic Driver Infoceutical"], ingredients={},
        catalog_names=["ED11 Liver Driver"], allowed_text="")
    assert probs == []



def test_an_infoceutical_by_its_short_name_cannot_be_credited_with_nutrients():
    probs = ng.check_narrative(
        "ED11 Liver Driver supplies zinc and selenium.",
        chain=["ED11 Liver Energetic Driver Infoceutical"], ingredients={},
        catalog_names=[], allowed_text="")
    assert any("ED11 Liver Energetic Driver Infoceutical" in p and "zinc" in p for p in probs), probs


# ── blind review round 3, false-alarm reviewer, 2026-09-25 ──────────────────

NM = {"Neuro Magnesium": ["Magnesium (Threonate)", "Vitamin D3", "Lithium Orotate"],
      "B17 Syntropy": ["Selenium (MSC)", "Vitamin A"], "Zinc Synergy": ["Zinc", "Copper"],
      "Lymph Flow": ["Cleavers"], "Heart Health": ["Vitamin C", "Hawthorn"],
      "Crab Apple Flower Essence in Terrain Restore": [], "ED11 Liver Energetic Driver Infoceutical": []}


def _nm(text):
    return ng.check_narrative(text, chain=list(NM), ingredients=NM, catalog_names=[],
                              allowed_text="")


def test_the_article_a_is_not_vitamin_a():
    assert _nm("Neuro Magnesium provides vitamin D3 and a small amount of lithium orotate.") == []
    assert _nm("Heart Health provides vitamin C, a key antioxidant.") == []


def test_a_lowercase_phrase_is_not_the_remedy():
    assert _nm("Healthy lymph flow depends on hydration and foods that provide potassium.") == []


def test_two_remedies_in_contrasting_clauses_are_checked_separately():
    assert _nm("B17 Syntropy provides selenium, while Zinc Synergy provides copper.") == []
    assert _nm("B17 Syntropy provides selenium; Zinc Synergy provides copper.") == []


def test_food_advice_after_a_pronoun_is_not_a_remedy_claim():
    assert _nm("Take one capsule of B17 Syntropy with breakfast. They are best paired with "
               "bananas, which are rich in potassium.") == []


def test_a_which_clause_binds_to_the_remedy_just_before_it():
    assert _nm("Crab Apple Flower Essence in Terrain Restore pairs with Neuro Magnesium, "
               "which provides magnesium.") == []
    assert _nm("ED11 Liver Driver works well alongside foods rich in selenium.") == []


def test_the_original_faults_are_still_caught():
    assert _nm("B17 Syntropy and Zinc Synergy provide vitamin A and selenium.") != []
    assert _nm("Neuro Magnesium provides zinc.") != []
    assert _nm("ED11 Liver Driver supplies zinc.") != []



# ── blind review round 3, client-text reviewer, 2026-09-25 ──────────────────

def test_a_missing_b1_is_not_hidden_by_b12():
    assert _nm("Neuro Magnesium provides thiamine and vitamin D3.") != []
    assert _nm("B17 Syntropy provides vitamin B1 and vitamin A.") != []


def test_hyphenated_b_vitamins_are_read():
    assert _nm("Neuro Magnesium provides vitamin B-12.") != []
    assert any("Vitamin B12" in p for p in _nm("Neuro Magnesium provides B-12."))


def test_a_title_does_not_end_the_sentence():
    probs = _nm("Neuro Magnesium supplies vitamin D3, as Dr. Glen notes, and vitamin A.")
    assert any("Vitamin A" in p for p in probs), probs


def test_a_two_product_row_is_checked_product_by_product():
    probs = ng.check_narrative("Zinc Synergy provides vitamin A.",
                               chain=["Zinc Synergy", "Heart Health"], ingredients=NM,
                               catalog_names=[], allowed_text="")
    assert any("Zinc Synergy" in p for p in probs)


def test_a_bare_voice_scan_beside_the_five_element_scan_is_left_for_glen():
    t = "Your Five Element Voice Scan showed Water weakness. The voice scan also showed a weak Kidney tone."
    assert ng.fix_scan_names(t) == t
    assert ng.scan_name_problems(t) != []
    assert ng.scan_name_problems("The voice scan showed it.") != []   # renamed, and flagged when saved



# Clinical, 2026-09-25: saved letters said "the fresh E4L Voice Scan", "your recent voice
# scan". A check that only corrects at generation never flagged them.
def test_saved_voice_scan_wording_is_flagged_in_any_case():
    for t in ("the fresh E4L Voice Scan", "your recent voice scan showing",
              "as corroborated by your recent voice scan.", "Your recent E4L Voice Scan"):
        assert any("Bioenergetic Wellness Scan" in p for p in ng.scan_name_problems(t)), t
    assert ng.scan_name_problems("Your Bioenergetic Wellness Scan showed it.") == []
    assert ng.scan_name_problems("Your Five Element Voice Scan showed it.") == []
