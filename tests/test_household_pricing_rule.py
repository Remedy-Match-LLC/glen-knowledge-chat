# tests/test_household_pricing_rule.py
"""Who prices as one buyer: household.pricing_household_emails.

Glen, 2026-10-28, sales tab, "yes" to:
1. A caregiving link counts wherever the member lives.
2. An independent link counts only with an active Family Plan in the household.
3. A person-based group counts for pricing only with an active Family Plan.
Shipping is unchanged.
"""
import json
import sqlite3

import pytest

from dashboard import family_plan, household

STEVE, MICHAEL, FATHER, KID, KID2 = ("steve@x.com", "michael@x.com", "father@x.com",
                                     "kid@x.com", "kid2@x.com")
ADULT, COACH, CLIENT = "adult@x.com", "coach@x.com", "client@x.com"
ANN, BEN, CAL = "ann@x.com", "ben@x.com", "cal@x.com"


@pytest.fixture
def cx():
    c = sqlite3.connect(":memory:")
    household.init_household_tables(c)
    family_plan.init_family_plan_table(c)
    c.execute("CREATE TABLE people (id INTEGER PRIMARY KEY, email TEXT, tags TEXT)")
    yield c
    c.close()


def _link(cx, p, m, rel):
    household.add_member(cx, p, m, relationship=rel)
    cx.commit()


def _plan(cx, email):
    family_plan.activate(cx, email, next_charge_at=None, source="comp")


def _person(cx, email, *slugs):
    cx.execute("INSERT INTO people (email, tags) VALUES (?, ?)",
               (email, json.dumps([f"household:{s}" for s in slugs])))
    cx.commit()


def H(cx, e):
    return household.pricing_household_emails(cx, e)


# ── rule 1: caregiving links count anywhere ─────────────────────────────────
@pytest.mark.parametrize("rel", sorted(household.DEPENDENT_RELATIONSHIPS))
def test_a_caregiving_link_counts_without_a_plan(cx, rel):
    _link(cx, STEVE, FATHER, rel)
    assert H(cx, STEVE) == {STEVE, FATHER}
    assert H(cx, FATHER) == {STEVE, FATHER}


def test_two_dependents_of_one_caregiver_share_through_the_caregiver(cx):
    _link(cx, STEVE, KID, "child")
    _link(cx, STEVE, KID2, "child")
    assert H(cx, KID) == {STEVE, KID, KID2}


def test_a_sibling_counts_only_when_both_links_count(cx):
    _link(cx, STEVE, KID, "child")
    _link(cx, STEVE, ADULT, "partner")     # independent, no plan
    assert H(cx, KID) == {STEVE, KID}
    assert H(cx, ADULT) == {ADULT}
    assert H(cx, STEVE) == {STEVE, KID}


# ── rule 2: independent links need a Family Plan in the household ──────────
@pytest.mark.parametrize("rel", ["partner", "family", "son", "daughter", "parent", ""])
def test_an_independent_link_does_not_count_without_a_plan(cx, rel):
    _link(cx, STEVE, ADULT, rel)
    assert H(cx, STEVE) == {STEVE}
    assert H(cx, ADULT) == {ADULT}


def test_steves_household_as_in_prod(cx):
    """Steve holds the Family Plan; Michael is a partner; the father lives elsewhere."""
    _plan(cx, STEVE)
    _link(cx, STEVE, MICHAEL, "partner")
    _link(cx, STEVE, FATHER, "parent")
    whole = {STEVE, MICHAEL, FATHER}
    for e in whole:
        assert H(cx, e) == whole, e


def test_a_plan_held_by_a_member_opens_the_household(cx):
    _link(cx, STEVE, MICHAEL, "partner")
    _plan(cx, MICHAEL)
    assert H(cx, STEVE) == {STEVE, MICHAEL}


def test_a_lapsed_plan_does_not_count(cx):
    _plan(cx, STEVE)
    family_plan.set_status(cx, STEVE, "cancelled")
    _link(cx, STEVE, MICHAEL, "partner")
    assert H(cx, MICHAEL) == {MICHAEL}


def test_an_outsider_never_joins(cx):
    _plan(cx, STEVE)
    _link(cx, STEVE, MICHAEL, "partner")
    assert H(cx, ADULT) == {ADULT}
    assert ADULT not in H(cx, STEVE)


# ── rule 3: person-based groups need a Family Plan ─────────────────────────
def test_a_person_group_with_a_plan_counts(cx):
    _person(cx, ANN, "ann-ben")
    _person(cx, BEN, "ann-ben")
    _plan(cx, BEN)
    assert H(cx, ANN) == {ANN, BEN}


def test_a_person_group_without_a_plan_is_shipping_only(cx):
    _person(cx, ANN, "ann-ben")
    _person(cx, BEN, "ann-ben")
    assert H(cx, ANN) == {ANN}


def test_a_group_tag_is_matched_whole(cx):
    """"household:ann" must not match "household:ann-ben"."""
    _person(cx, ANN, "ann")
    _person(cx, BEN, "ann-ben")
    _plan(cx, BEN)
    assert H(cx, ANN) == {ANN}


def test_a_head_tag_is_not_a_group(cx):
    cx.execute("INSERT INTO people (email, tags) VALUES (?, ?)",
               (ANN, json.dumps(["household-head:ann-ben"])))
    _person(cx, BEN, "ann-ben")
    _plan(cx, BEN)
    assert H(cx, ANN) == {ANN}


def test_no_people_table_still_prices_the_links(cx):
    cx.execute("DROP TABLE people")
    _link(cx, STEVE, KID, "child")
    assert H(cx, KID) == {STEVE, KID}


# ── edges ───────────────────────────────────────────────────────────────────
def test_blank_and_messy_emails(cx):
    _link(cx, STEVE, KID, "child")
    assert H(cx, "") == set()
    assert H(cx, "  Kid@X.com ") == {STEVE, KID}


def test_a_caregiving_client_link_counts_as_glen_ruled(cx):
    _link(cx, COACH, CLIENT, "caregiving-client")
    assert H(cx, CLIENT) == {COACH, CLIENT}


def test_it_never_reaches_beyond_household_emails(cx):
    _plan(cx, STEVE)
    _link(cx, STEVE, MICHAEL, "partner")
    _link(cx, MICHAEL, CAL, "child")       # two hops from Steve
    _link(cx, STEVE, KID, "child")
    for e in (STEVE, MICHAEL, KID, CAL):
        assert H(cx, e) <= household.household_emails(cx, e), e


# ── review round 2 ──────────────────────────────────────────────────────────
def test_a_plan_in_one_household_never_opens_another(cx):
    """P cares for E (child) and holds a plan. Q is E's partner, with no plan."""
    _plan(cx, STEVE)
    _link(cx, STEVE, KID, "child")
    _link(cx, ADULT, KID, "partner")
    _link(cx, ADULT, MICHAEL, "partner")
    assert H(cx, KID) == {STEVE, KID}
    assert H(cx, MICHAEL) == {MICHAEL}


def test_wildcards_in_a_slug_match_nothing_else(cx):
    _person(cx, ANN, "team_a")
    _person(cx, BEN, "teamxa")
    _person(cx, CAL, "team%")
    _plan(cx, BEN)
    assert H(cx, ANN) == {ANN}
    assert H(cx, CAL) == {CAL}


def test_slug_case_is_exact(cx):
    _person(cx, ANN, "Team")
    _person(cx, BEN, "team")
    _plan(cx, BEN)
    assert H(cx, ANN) == {ANN}
    assert H(cx, BEN) == {BEN}


def test_a_messy_people_email_still_finds_its_group(cx):
    _person(cx, " Ann@X.com ", "ann-ben")
    _person(cx, BEN, "ann-ben")
    _plan(cx, BEN)
    assert H(cx, ANN) == {ANN, BEN}


def test_pricing_is_symmetric_across_two_households(cx):
    """E and Q are each X's primary, as partners; only Q holds a plan (review round 1)."""
    _link(cx, ADULT, MICHAEL, "partner")     # E=ADULT, X=MICHAEL
    _link(cx, STEVE, MICHAEL, "partner")     # Q=STEVE
    _plan(cx, STEVE)
    assert H(cx, MICHAEL) == {STEVE, MICHAEL}
    assert H(cx, STEVE) == {STEVE, MICHAEL}
    assert H(cx, ADULT) == {ADULT}
    for a in (STEVE, MICHAEL, ADULT):
        for b in (STEVE, MICHAEL, ADULT):
            assert (b in H(cx, a)) == (a in H(cx, b)), (a, b)


# ── messy rows (review round 3) ─────────────────────────────────────────────
def test_a_legacy_messy_link_row_still_counts(cx):
    cx.execute("INSERT INTO household_members (primary_email, member_email, relationship) "
               "VALUES (?, ?, 'child')", (" Steve@X.com", "Kid@X.com "))
    cx.commit()
    assert H(cx, KID) == {STEVE, KID}
    assert H(cx, STEVE) == {STEVE, KID}


def test_a_corrupt_tags_row_does_not_hide_a_good_one(cx):
    cx.execute("INSERT INTO people (email, tags) VALUES (?, ?)", (ANN, '["household:x'))
    _person(cx, ANN, "ann-ben")
    _person(cx, BEN, "ann-ben")
    _plan(cx, BEN)
    assert H(cx, ANN) == {ANN, BEN}


def test_a_blank_people_email_never_joins_or_answers(cx):
    _person(cx, "", "ann-ben")
    _person(cx, BEN, "ann-ben")
    _plan(cx, BEN)
    assert H(cx, BEN) == {BEN}
    assert H(cx, "") == set()
