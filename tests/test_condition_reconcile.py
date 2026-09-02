"""Reconciling a client's whole "what you are working on" set.

The permanent Find Solutions checklist accumulates: a client checks what they
are working on now and unchecks what has cleared up, and submitting reconciles
the stored set against the submitted one.

The load-bearing case is UNCHECKING. If unchecking removed the stored condition
but left the remedies that condition seeded, a client who no longer has dry eye
would keep being recommended dry-eye remedies with no way left to stop it. So
every removal test below asserts BOTH halves: the condition row is gone AND its
seeded rows are gone, while every other condition's seeds are untouched.
"""
import sqlite3

import pytest

from dashboard import condition_programs as cprog
from dashboard import condition_triage as ct
from dashboard import recommendation_events as re_events

EMAIL = "reconcile@x.com"


@pytest.fixture
def cx():
    cx = sqlite3.connect(":memory:")
    cx.row_factory = sqlite3.Row
    ct.init_table(cx)
    re_events.init_recommendation_events(cx)
    cprog.init_table(cx)
    cprog.upsert(cx, "dry-eye", "Dry eye", False,
                 [{"slug": "tear-support", "name": "Tear Support"}])
    cprog.upsert(cx, "symptom-sleep", "Trouble sleeping", False,
                 [{"slug": "sleep-calm", "name": "Sleep Calm"}])
    cprog.upsert(cx, "symptom-fatigue", "Fatigue", False,
                 [{"slug": "energy-boost", "name": "Energy Boost"}])
    cprog.upsert(cx, "glaucoma-elevated-iop", "Glaucoma - elevated IOP", False,
                 [{"slug": "neuroprotect", "name": "Neuroprotect"}])
    return cx


def _conditions(cx):
    return sorted(r[0] for r in cx.execute(
        "SELECT condition FROM condition_triage WHERE lower(email)=lower(?)", (EMAIL,)))


def _seeds(cx):
    """{origin_ref: {slug, ...}} for every condition-sourced recommendation."""
    out = {}
    for row in cx.execute(
        "SELECT origin_ref, product_key FROM recommendation_events "
        "WHERE client_email=? AND source_key='condition'", (EMAIL,)).fetchall():
        out.setdefault(row[0], set()).add(row[1])
    return out


def test_checklist_conditions_match_the_rendered_javascript_list():
    """One authored list. The browser renders static/js/portal-conditions.js and
    the endpoint validates against condition_triage.CHECKLIST_CONDITIONS. A value
    present in one and absent from the other is a condition a client can check
    and never get remedies for (or vice versa), so pin them equal."""
    import pathlib
    import re as _re
    src = (pathlib.Path(__file__).resolve().parent.parent
           / "static" / "js" / "portal-conditions.js").read_text()
    js_values = set(_re.findall(r"\{value: '([^']+)', label:", src))
    assert js_values, "no condition values parsed out of portal-conditions.js"
    assert js_values == set(ct.CHECKLIST_CONDITIONS)


def test_remove_condition_drops_the_row_and_its_seeded_remedies(cx):
    ct.seed_from_triage(cx, EMAIL, "dry-eye", {})
    ct.seed_from_triage(cx, EMAIL, "symptom-sleep", {})
    assert _conditions(cx) == ["dry-eye", "symptom-sleep"]
    assert _seeds(cx)["dry-eye"] == {"tear-support"}

    cleared = ct.remove_condition(cx, EMAIL, "dry-eye")

    assert cleared == 1
    assert _conditions(cx) == ["symptom-sleep"]
    seeds = _seeds(cx)
    assert "dry-eye" not in seeds                       # its remedies are gone
    assert seeds["symptom-sleep"] == {"sleep-calm"}     # the others are untouched


def test_reconcile_adds_keeps_and_removes_in_one_pass(cx):
    ct.seed_from_triage(cx, EMAIL, "dry-eye", {})
    ct.seed_from_triage(cx, EMAIL, "symptom-sleep", {})

    res = ct.reconcile_conditions(cx, EMAIL, [
        {"condition": "symptom-sleep"},                 # still checked
        {"condition": "symptom-fatigue"},               # newly checked
    ])                                                  # dry-eye unchecked

    assert res["added"] == ["symptom-fatigue"]
    assert res["kept"] == ["symptom-sleep"]
    assert res["removed"] == ["dry-eye"]
    assert _conditions(cx) == ["symptom-fatigue", "symptom-sleep"]
    seeds = _seeds(cx)
    assert "dry-eye" not in seeds
    assert seeds["symptom-sleep"] == {"sleep-calm"}
    assert seeds["symptom-fatigue"] == {"energy-boost"}


def test_reconcile_leaves_a_still_checked_condition_alone(cx):
    """Still checked means untouched: no re-seed, no duplicate row, and the
    stored answers survive even though this submit carried none."""
    ct.seed_from_triage(cx, EMAIL, "glaucoma", {"iop_od": 25})
    before = cx.execute("SELECT updated_at FROM condition_triage "
                        "WHERE lower(email)=lower(?)", (EMAIL,)).fetchone()[0]

    res = ct.reconcile_conditions(cx, EMAIL, [{"condition": "glaucoma"}])

    assert res["added"] == [] and res["removed"] == []
    assert res["kept"] == ["glaucoma"]
    after = cx.execute("SELECT updated_at FROM condition_triage "
                       "WHERE lower(email)=lower(?)", (EMAIL,)).fetchone()[0]
    assert after == before                              # not rewritten
    stored = ct.get_triage(cx, EMAIL, "glaucoma")
    assert stored["iop_od"] == "25"                     # answers survive
    assert stored["resolved_programs"] == ["glaucoma-elevated-iop"]
    rows = cx.execute(
        "SELECT COUNT(*) FROM recommendation_events WHERE client_email=? "
        "AND source_key='condition' AND origin_ref='glaucoma'", (EMAIL,)).fetchone()[0]
    assert rows == 1                                    # not duplicated


def test_reconcile_with_an_empty_set_clears_everything(cx):
    ct.seed_from_triage(cx, EMAIL, "dry-eye", {})
    ct.seed_from_triage(cx, EMAIL, "symptom-sleep", {})

    res = ct.reconcile_conditions(cx, EMAIL, [])

    assert res["removed"] == ["dry-eye", "symptom-sleep"]
    assert _conditions(cx) == []
    assert _seeds(cx) == {}


def test_reconcile_does_not_touch_other_sources(cx):
    """clear_events is scoped to source_key='condition'. A remedy the client
    added themselves, or one a scan matched, must survive a condition removal."""
    ct.seed_from_triage(cx, EMAIL, "dry-eye", {})
    re_events.record_self(cx, EMAIL, "tear-support")
    re_events.record_event(cx, EMAIL, "scan-pick", "scan",
                           occurred_at="2026-01-01", origin_ref="scan:1:0")

    ct.reconcile_conditions(cx, EMAIL, [])

    survivors = {(r["product_key"], r["source_key"])
                 for r in re_events.list_events(cx, EMAIL)}
    assert survivors == {("tear-support", "self"), ("scan-pick", "scan")}
