"""Attach a remedy to a condition on this client's list, in one operation.

Glen, 2026-09-04, for the dispensed-before panel:

  "Click on condition associated with a remedy: add the remedy to the checked
   list for that condition, or add that condition with that remedy checked if
   not already on the client's list."

Both routes end in the same place, and getting there touches three stores: the
per-test checklist (is this condition on the client's list), the shared catalog
(is this remedy known for that condition), and the per-test selection (is it
ticked). save_selection REPLACES the ticked list, so "add one" is a
read-modify-write and doing it from the browser in three calls would drop a
tick whenever two happened close together.
"""
import pytest

from dashboard import biofield_clinical_checklist as cc
from dashboard import biofield_clinical_proposals as cp
from dashboard import db


@pytest.fixture()
def cx(tmp_path):
    c = db.connect(str(tmp_path / "c.db"))
    cc.ensure_catalog_schema(c)
    cp.ensure_schema(c)
    yield c
    c.close()


def _ticked(cx, label, test_id="a1"):
    return cp.selections(cx, test_id).get(cp._key(label), [])


def _on_list(cx, label, test_id="a1"):
    """Is this condition actually accepted onto the client's list? The return
    value alone lied when decide() was skipped."""
    row = cx.execute("SELECT status FROM biofield_clinical_proposals "
                     "WHERE test_id=? AND item_key=?",
                     (test_id, cp._key(label))).fetchone()
    return bool(row) and row[0] == "accepted"


def test_it_adds_the_condition_and_ticks_the_remedy(cx):
    out = cc.attach_remedy(cx, "a1", "Dry Eye", "OcuFlow Bedtime")
    assert out["label"] == "Dry Eye" and out["remedy"] == "OcuFlow Bedtime"
    assert out["added_condition"] is True
    assert _on_list(cx, "Dry Eye"), "the condition never reached the client's list"
    assert _ticked(cx, "Dry Eye") == ["OcuFlow Bedtime"]
    # and the pairing is remembered for next time
    assert "OcuFlow Bedtime" in cc.custom_remedies(cx, "Dry Eye")


def test_a_second_remedy_joins_the_first_rather_than_replacing_it(cx):
    """save_selection overwrites, so this is the bug the operation exists to avoid."""
    cc.attach_remedy(cx, "a1", "Dry Eye", "OcuFlow Bedtime")
    cc.attach_remedy(cx, "a1", "Dry Eye", "IOP Syntropy")
    assert _ticked(cx, "Dry Eye") == ["OcuFlow Bedtime", "IOP Syntropy"]


def test_attaching_the_same_remedy_twice_does_not_duplicate_it(cx):
    cc.attach_remedy(cx, "a1", "Dry Eye", "OcuFlow Bedtime")
    out = cc.attach_remedy(cx, "a1", "Dry Eye", "  ocuflow bedtime ")
    assert _ticked(cx, "Dry Eye") == ["OcuFlow Bedtime"]
    assert out["already_ticked"] is True


def test_a_condition_already_on_the_list_is_not_re_added(cx):
    cp.decide(cx, "a1", "Dry Eye", "accepted", "manual")
    out = cc.attach_remedy(cx, "a1", "Dry Eye", "OcuFlow Bedtime")
    assert out["added_condition"] is False
    assert _ticked(cx, "Dry Eye") == ["OcuFlow Bedtime"]


def test_one_test_does_not_tick_remedies_on_another(cx):
    cc.attach_remedy(cx, "a1", "Dry Eye", "OcuFlow Bedtime")
    cc.attach_remedy(cx, "a2", "Dry Eye", "IOP Syntropy")
    assert cp.selections(cx, "a1").get(cp._key("Dry Eye")) == ["OcuFlow Bedtime"]
    assert cp.selections(cx, "a2").get(cp._key("Dry Eye")) == ["IOP Syntropy"]


def test_a_previously_dismissed_condition_comes_back_when_chosen(cx):
    """Choosing it in the panel is an explicit decision that outranks an earlier
    dismissal, or the click would silently do nothing."""
    cp.decide(cx, "a1", "Dry Eye", "dismissed", "hidden earlier")
    out = cc.attach_remedy(cx, "a1", "Dry Eye", "OcuFlow Bedtime")
    assert out["added_condition"] is True
    assert _on_list(cx, "Dry Eye"), "claimed to add the condition but did not"
    assert _ticked(cx, "Dry Eye") == ["OcuFlow Bedtime"]


def test_blank_input_is_refused_rather_than_writing_junk(cx):
    for label, remedy in (("", "X"), ("Dry Eye", ""), (None, None), ("  ", " ")):
        with pytest.raises(ValueError):
            cc.attach_remedy(cx, "a1", label, remedy)
