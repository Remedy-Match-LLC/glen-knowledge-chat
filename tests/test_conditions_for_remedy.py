"""Which conditions is this remedy used for?

Glen, 2026-09-04: the dispensed-before panel should show, beside each remedy,
the symptoms and conditions it relates to, each clickable to add both to the
client's active list.

The catalog already stores condition -> remedy. This is the same table read the
other way round, and it has to agree with the forward direction: a remedy the
practitioner hid from a condition must not come back through the reverse door.
"""
import pytest

from dashboard import biofield_clinical_checklist as cc
from dashboard import db


@pytest.fixture()
def cx(tmp_path):
    c = db.connect(str(tmp_path / "c.db"))
    cc.ensure_catalog_schema(c)
    yield c
    c.close()


def test_it_finds_the_conditions_a_remedy_is_used_for(cx):
    cc.remember_remedies(cx, "Glaucoma — Elevated IOP", ["IOP Syntropy"])
    cc.remember_remedies(cx, "Dry Eye", ["IOP Syntropy", "OcuFlow Bedtime"])
    assert cc.conditions_for_remedy(cx, "IOP Syntropy") == ["Dry Eye", "Glaucoma — Elevated IOP"]


def test_it_matches_the_remedy_however_it_was_typed(cx):
    cc.remember_remedies(cx, "Dry Eye", ["OcuFlow Bedtime"])
    for spelling in ("ocuflow bedtime", "  OcuFlow   Bedtime  ", "OCUFLOW BEDTIME"):
        assert cc.conditions_for_remedy(cx, spelling) == ["Dry Eye"]


def test_a_hidden_pairing_does_not_come_back_through_the_reverse_door(cx):
    """forget_remedy hides a pairing the practitioner rejected. The reverse
    lookup must honour that, or a dismissed association reappears as a suggestion."""
    cc.remember_remedies(cx, "Dry Eye", ["OcuFlow Bedtime"])
    cc.forget_remedy(cx, "Dry Eye", "OcuFlow Bedtime")
    assert cc.conditions_for_remedy(cx, "OcuFlow Bedtime") == []


def test_an_unknown_remedy_yields_nothing(cx):
    assert cc.conditions_for_remedy(cx, "Nothing At All") == []
    assert cc.conditions_for_remedy(cx, "") == []
    assert cc.conditions_for_remedy(cx, None) == []


def test_results_are_ordered_and_deduplicated(cx):
    """A stable, unique list: this renders as clickable chips, and a repeat would
    add the same condition twice."""
    cc.remember_remedies(cx, "Zeta", ["X"])
    cc.remember_remedies(cx, "Alpha", ["X"])
    cc.remember_remedies(cx, "Alpha", ["X"])
    assert cc.conditions_for_remedy(cx, "X") == ["Alpha", "Zeta"]


def test_it_is_capped_so_one_remedy_cannot_flood_the_row(cx):
    for i in range(40):
        cc.remember_remedies(cx, "Condition %02d" % i, ["Common"])
    got = cc.conditions_for_remedy(cx, "Common", limit=6)
    assert len(got) == 6 and got == sorted(got)


def test_a_missing_table_is_not_an_error(tmp_path):
    """It feeds a reference panel; a fresh database must not break the page."""
    c = db.connect(str(tmp_path / "empty.db"))
    assert cc.conditions_for_remedy(c, "X") == []
    c.close()
