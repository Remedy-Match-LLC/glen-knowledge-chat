import sqlite3
from dashboard import household as hh

def _cx():
    cx = sqlite3.connect(":memory:"); cx.row_factory = sqlite3.Row
    hh.init_household_tables(cx)
    return cx

def test_pay_consent_default_off_and_grant_flow():
    cx = _cx()
    hh.add_member(cx, "steve@x.com", "michael@x.com", relationship="partner")
    # default: no pay consent
    assert hh.can_pay(cx, "steve@x.com", "michael@x.com") is False
    assert hh.payable_members_for(cx, "steve@x.com") == []
    # member grants pay consent with line-item visibility
    hh.set_pay_consent(cx, "steve@x.com", "michael@x.com", 1, share_scope="line_items")
    assert hh.can_pay(cx, "steve@x.com", "michael@x.com") is True
    pm = hh.payable_members_for(cx, "steve@x.com")
    assert pm == [{"member_email": "michael@x.com", "label": "", "pay_share_scope": "line_items"}]
    # revoke is non-destructive to the link, just flips the flag
    hh.set_pay_consent(cx, "steve@x.com", "michael@x.com", 0)
    assert hh.can_pay(cx, "steve@x.com", "michael@x.com") is False

def test_pay_consent_self_pay_guard():
    cx = _cx()
    hh.add_member(cx, "steve@x.com", "steve@x.com", relationship="")
    hh.set_pay_consent(cx, "steve@x.com", "steve@x.com", 1)
    assert hh.can_pay(cx, "steve@x.com", "steve@x.com") is False


# Glen, 2026-09-19: "Pets cannot consent. Parents and guardians can consent for minor
# children." The caregiver's authority stands in for consent for a pet or a child.
# The data holds a relationship label but no age, so "child" is taken as a minor; an
# adult son or daughter is labelled something else and keeps the consent step.

def test_a_caregiver_can_pay_for_a_pet_or_child_without_a_consent_flag():
    cx = _cx()
    hh.add_member(cx, "sharon@x.com", "hershey@x.com", "Hershey", relationship="pet")
    hh.add_member(cx, "sharon@x.com", "kid@x.com", "Kid", relationship="Child")
    assert hh.can_pay(cx, "sharon@x.com", "hershey@x.com") is True
    assert hh.can_pay(cx, "sharon@x.com", "kid@x.com") is True
    assert [m["member_email"] for m in hh.payable_members_for(cx, "sharon@x.com")] == [
        "hershey@x.com", "kid@x.com"]


def test_other_relationships_still_need_consent():
    cx = _cx()
    for rel in ("partner", "spouse", "adult child", "dependent", "caregiving-client", ""):
        hh.add_member(cx, "sharon@x.com", f"{rel or 'none'}@x.com", relationship=rel)
        assert hh.can_pay(cx, "sharon@x.com", f"{rel or 'none'}@x.com") is False, rel
    assert hh.payable_members_for(cx, "sharon@x.com") == []


def test_only_the_pets_own_caregiver_can_pay():
    cx = _cx()
    hh.add_member(cx, "sharon@x.com", "hershey@x.com", relationship="pet")
    assert hh.can_pay(cx, "stranger@x.com", "hershey@x.com") is False
