"""Every paid rung needs a focused page, not the catalog row drawn as one.

The pages already existed at /begin/ascend/<slug> and rendered about 600
characters: a title, a price, one sentence and a button. Glen asked on 2026-09-12
for each item to have a real page so links can point straight at it. Five
shortlinks point at that ladder, so this is the copy those clicks land on.

Copy adapted from the tier video scripts in the vault. Those are spoken scripts
carrying outcome claims about other people's conditions. These tests pin the rules
that keep the written page on the right side of that line.
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import begin_funnel as bf  # noqa: E402

PAID = set(bf.TIER_CATALOG)


def bodies():
    return {slug: page for slug, page in bf.TIER_PAGES.items()}


def all_text(page):
    """Every piece of prose on the page, including any field added later.

    This used to list the fields by hand. When `note` was added on 2026-09-12 the
    whole suite kept passing while nothing checked a word of it. A guard that does
    not see a field is not guarding it."""
    parts = []
    for key, val in page.items():
        if key == "steps":
            parts += [a + " " + b for a, b in val]
        elif isinstance(val, str):
            parts.append(val)
    return " ".join(parts)


def test_all_text_sees_every_prose_field():
    """The guard on the guard. A new field must not slip past the copy rules."""
    for slug, page in bodies().items():
        prose = {k for k, v in page.items() if isinstance(v, str) and v}
        seen = all_text(page)
        for k in prose:
            assert page[k][:40] in seen, f"{slug}: field {k!r} is not checked"


# --- coverage -------------------------------------------------------------------

def test_every_paid_rung_has_a_page():
    missing = PAID - set(bf.TIER_PAGES)
    assert not missing, f"rungs with no page copy: {sorted(missing)}"


def test_no_page_copy_for_a_rung_that_does_not_exist():
    """A stray key would render nowhere and rot unnoticed."""
    extra = set(bf.TIER_PAGES) - PAID
    assert not extra, f"page copy for unknown rungs: {sorted(extra)}"


def test_each_page_is_substantially_more_than_the_catalog_row():
    for slug, page in bodies().items():
        words = len(all_text(page).split())
        assert words >= 100, f"{slug} is only {words} words, still a card"
        assert page.get("steps"), f"{slug} has no steps"
        assert page.get("lede") and page.get("close"), slug


# --- Glen's standing copy rules --------------------------------------------------

def test_no_em_dashes():
    for slug, page in bodies().items():
        assert "—" not in all_text(page), slug


def test_no_shouting():
    for slug, page in bodies().items():
        shouted = [w for w in re.findall(r"\b[A-Z]{3,}\b", all_text(page))
                   if w not in ("ASH", "E4L", "NIH", "CSO", "DIY")]
        assert not shouted, f"{slug}: {shouted}"


def test_client_never_patient():
    """Glen is retired from licensed practice. The word is client."""
    for slug, page in bodies().items():
        assert not re.search(r"\bpatients?\b", all_text(page), re.I), slug


def test_no_present_tense_licensed_practice():
    """The source scripts say "I've been practicing". He is retired from licensure."""
    for slug, page in bodies().items():
        t = all_text(page)
        for phrase in ("my practice", "i practice", "practicing naturopathic",
                       "in practice for over"):
            assert phrase not in t.lower(), f"{slug}: {phrase!r}"


# --- health claims ---------------------------------------------------------------

def test_no_cure_or_treatment_promises():
    banned = ("cure", "cures", "treat your", "will heal you", "guaranteed",
              "reverses your", "irreversible")
    for slug, page in bodies().items():
        t = all_text(page).lower()
        hits = [b for b in banned if b in t]
        assert not hits, f"{slug}: {hits}"


def test_no_third_party_outcome_claims():
    """Glen's own documented history is biography and may stay. Claims about what
    happened to other people's conditions are the thing the scripts carry and the
    page must not."""
    for slug, page in bodies().items():
        t = all_text(page).lower()
        for phrase in ("clients reverse", "watched clients", "conditions stabilize",
                       "tissue healing", "written off"):
            assert phrase not in t, f"{slug}: {phrase!r}"


def test_the_free_scan_is_not_confused_with_the_paid_analysis():
    """They are different instruments and the skill says never describe one as the
    other. The Biofield page is the one that must draw the line."""
    page = bf.TIER_PAGES["biofield-analysis"]
    t = all_text(page).lower()
    assert "biofield analysis" in t
    assert "voice scan" in t, "the page does not distinguish the free scan"


def test_no_retired_platform_is_named():
    for slug, page in bodies().items():
        t = all_text(page).lower()
        for dead in ("practice better", "practicebetter", "skool", "clientclub"):
            assert dead not in t, f"{slug}: {dead}"


def test_the_membership_coaching_cadence_is_weekly():
    """Glen ruled it weekly on 2026-09-08. The source copy still says monthly."""
    t = all_text(bf.TIER_PAGES["membership"]).lower()
    assert "weekly" in t
    assert "monthly group coaching" not in t


# --- prices must match the live membership page ---------------------------------
#
# Glen asked on 2026-09-12 whether the $1 trial was still running. It is not, and
# the first draft of this copy said "Start for one dollar. After the trial it is
# ninety-nine dollars a month." That would have shipped a price that does not exist
# onto a page five shortlinks point at.
#
# Read from https://illtowell.com/membership on 2026-09-12:
#   Monthly $99 · Annual monthly $99/mo · Annual full pay $990
#   Household monthly $147/mo · Household annual $1,497/yr
#   Causal Biofield Analysis: $300 non-member, INCLUDING the first month of
#   individual membership; $200 for active members.

def test_no_dollar_one_trial_is_claimed():
    for slug, page in bodies().items():
        t = all_text(page).lower()
        for phrase in ("$1 trial", "one dollar", "free trial", "start for a dollar"):
            assert phrase not in t, f"{slug}: {phrase!r}, and the trial is retired"


def test_the_word_trial_is_not_used_as_an_offer():
    for slug, page in bodies().items():
        assert "trial" not in all_text(page).lower(), slug


def test_membership_states_the_real_prices():
    t = all_text(bf.TIER_PAGES["membership"])
    assert "ninety-nine dollars" in t.lower() or "$99" in t
    assert "$990" in t, "the annual full-pay price is the best value and is missing"
    assert "household" in t.lower()


def test_biofield_states_both_prices_and_what_300_includes():
    t = all_text(bf.TIER_PAGES["biofield-analysis"])
    assert "hree hundred" in t or "$300" in t
    assert "wo hundred" in t or "$200" in t
    assert "first month" in t.lower(), \
        "the $300 includes the first month of membership and the page must say so"


def test_the_three_services_are_kept_apart():
    """The free voice scan, the automated Remedy Match report, and the paid
    Causal Biofield Analysis are three different things. The live membership page
    says so explicitly, and conflating them is the mistake the skill warns about."""
    bio = all_text(bf.TIER_PAGES["biofield-analysis"]).lower()
    assert "voice scan" in bio and "remedy match report" in bio, \
        "the Biofield page does not distinguish all three"
    mem = all_text(bf.TIER_PAGES["membership"]).lower()
    assert "not a consultation" in mem, \
        "the membership page does not say the included report is not a consultation"


# --- no rung may claim to be the cheapest way in ---------------------------------
#
# Glen, 2026-09-12: the Biofield page said "It is the lowest step onto the paid
# path". That is false. Individual membership is $99 and the analysis is $300, or
# $200 for members. A cheaper rung exists and the page was overstating.
#
# He also asked for "Valued at $1,000" rather than "Priced at a thousand dollars",
# which reads as the price rather than the comparison.

def test_no_rung_claims_to_be_the_cheapest_entry():
    banned = ("lowest step", "cheapest", "least expensive", "lowest priced",
              "lowest-cost", "lowest cost", "the first paid step")
    for slug, page in bodies().items():
        t = all_text(page).lower()
        hits = [b for b in banned if b in t]
        assert not hits, f"{slug}: {hits}, and membership at $99 is cheaper"


def test_membership_is_in_fact_the_cheapest_paid_rung():
    """The control for the rule above, read off the catalog rather than assumed."""
    import re as _re
    def cheapest(p):
        nums = [int(n.replace(",", "")) for n in _re.findall(r"\$([\d,]+)", p or "")]
        return min(nums) if nums else 10 ** 9
    prices = {slug: cheapest(t.get("price")) for slug, t in bf.TIER_CATALOG.items()}
    assert min(prices, key=prices.get) == "membership", prices


def test_the_thousand_is_framed_as_value_not_price():
    t = all_text(bf.TIER_PAGES["biofield-analysis"])
    assert "Valued at $1,000" in t
    assert "priced at a thousand" not in t.lower()
    assert "priced at $1,000" not in t.lower()


# --- speed claims are about the process, never the outcome -----------------------
#
# Glen, 2026-09-12, replacing "with results that come faster": say "you get your
# program started in days, not weeks". The first version compared outcomes against
# functional medicine, which is an efficacy claim on a public page from someone
# retired from licensure. How quickly the program STARTS is a process fact and his
# to state. How quickly it WORKS is not.

def test_no_comparative_outcome_speed_claim():
    banned = ("results that come faster", "faster results", "results sooner",
              "heal faster than", "work faster than", "quicker results")
    for slug, page in bodies().items():
        t = all_text(page).lower()
        hits = [b for b in banned if b in t]
        assert not hits, f"{slug}: {hits}"


def test_the_biofield_note_states_the_start_time_instead():
    t = all_text(bf.TIER_PAGES["biofield-analysis"])
    assert "started in days, not weeks" in t


# --- the struck price ------------------------------------------------------------
#
# Glen picked option C on 2026-09-12: a struck $1,000, then $300, with the member
# price on a line beneath.
#
# `was` is a separate field on purpose. Every other rung uses `value` for a
# descriptor, "cancel anytime", "6 months", "1 week". Reusing that field would put
# a line through those.

def test_only_a_money_amount_is_ever_struck():
    for slug, t in bf.TIER_CATALOG.items():
        was = t.get("was")
        if was:
            assert was.startswith("$"), f"{slug}: struck {was!r}, which is not a price"


def test_no_rung_carries_both_a_struck_price_and_a_value():
    """They render in the same slot. Carrying both would print one over the other."""
    for slug, t in bf.TIER_CATALOG.items():
        assert not (t.get("was") and t.get("value")), slug


def test_the_descriptors_on_other_rungs_are_untouched():
    """The control. A change that blanked every `value` would pass the test above."""
    assert bf.TIER_CATALOG["membership"]["value"] == "cancel anytime"
    assert bf.TIER_CATALOG["one-to-one"]["value"] == "6 months"
    assert bf.TIER_CATALOG["hawaii-immersion"]["value"] == "1 week"
    for slug in ("membership", "one-to-one", "hawaii-immersion"):
        assert not bf.TIER_CATALOG[slug].get("was"), slug


def test_the_biofield_price_line_reads_as_glen_chose_it():
    t = bf.TIER_CATALOG["biofield-analysis"]
    assert t["was"] == "$1,000"
    assert t["price"] == "$300"
    assert "$200" in t["price_sub"]
    assert "first month" in t["price_sub"], \
        "the $300 includes the first month and the price line should say so"


def test_the_struck_price_agrees_with_the_note():
    """Two surfaces telling different stories is what this change was fixing."""
    note = bf.TIER_PAGES["biofield-analysis"]["note"]
    assert "thousand dollars has been the full investment" in note.lower()
    assert bf.TIER_CATALOG["biofield-analysis"]["was"] == "$1,000"


def test_both_templates_render_the_struck_price():
    """The tier page and the ladder hero both print a price. If only one learns
    about `was`, the two surfaces disagree, which is the bug this replaced."""
    import pathlib
    root = pathlib.Path(__file__).resolve().parents[1] / "static"
    for name in ("begin-ascend-tier.html", "begin-ascend.html"):
        src = (root / name).read_text(encoding="utf-8")
        assert ".was" in src or "rec.was" in src or "tier.was" in src, name
        assert "createElement('s')" in src or 'createElement("s")' in src, \
            f"{name} does not build a strike element"
