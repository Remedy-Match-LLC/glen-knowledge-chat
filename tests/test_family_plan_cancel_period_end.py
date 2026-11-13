"""Glen, 2026-11-13: a Household plan the holder cancels keeps its cover until the
period already paid for ends. Before this the cancel ended cover at once, while the
cancel dialog promised "the end of the cycle". An unpaid (past_due) or comped plan
has no paid period left and still ends at once."""
import os
import re
import shutil
import sqlite3
import subprocess

import pytest

import app as appmod
from dashboard import family_plan as fp
from dashboard import household as hh

HOLDER, MEMBER = "cancel-holder@x.com", "cancel-member@x.com"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TODAY, END = "2026-11-13", "2027-11-01"


def _cx():
    cx = sqlite3.connect(":memory:")
    cx.row_factory = sqlite3.Row
    fp.init_family_plan_table(cx)
    hh.init_household_tables(cx)
    return cx


@pytest.fixture
def today(monkeypatch):
    box = {"d": TODAY}
    monkeypatch.setattr(fp, "_today", lambda: box["d"])
    return box


def test_cancel_keeps_cover_until_the_paid_period_ends(today):
    cx = _cx()
    fp.activate(cx, HOLDER, next_charge_at=END, customer_id="c", payment_method_id="p")
    hh.add_member(cx, HOLDER, MEMBER, relationship="child")
    assert fp.cancel_at_period_end(cx, HOLDER) == END
    assert fp.get(cx, HOLDER)["status"] == fp.CANCELLING
    assert fp.covers(cx, HOLDER) and fp.covers(cx, MEMBER)
    assert fp.access_ends(cx, HOLDER) == END
    assert [r["caregiver_email"] for r in fp.list_active(cx)] == [HOLDER]
    assert fp.active_plan_holders_for(cx, MEMBER) == {HOLDER}
    # Never charged again, even once the renewal date arrives.
    assert fp.due(cx, END) == [] and fp.due(cx, "2099-01-01") == []
    # Repeating the cancel changes nothing.
    assert fp.cancel_at_period_end(cx, HOLDER) == END
    assert fp.get(cx, HOLDER)["status"] == fp.CANCELLING
    # The last covered day is the day before the renewal date.
    today["d"] = "2027-10-31"
    assert fp.covers(cx, MEMBER)
    today["d"] = END
    assert not fp.covers(cx, HOLDER) and not fp.covers(cx, MEMBER)
    assert fp.access_ends(cx, HOLDER) is None
    assert fp.list_active(cx) == []


@pytest.mark.parametrize("setup", ["past_due", "comp", "period_over"])
def test_cancel_ends_at_once_with_no_paid_period_left(today, setup):
    cx = _cx()
    if setup == "comp":
        fp.activate(cx, HOLDER, next_charge_at=None, source="comp")
    else:
        fp.activate(cx, HOLDER, next_charge_at=END if setup == "past_due" else TODAY,
                    customer_id="c", payment_method_id="p")
        if setup == "past_due":
            fp.set_status(cx, HOLDER, "past_due")
    assert fp.covers(cx, HOLDER)
    assert fp.cancel_at_period_end(cx, HOLDER) is None
    assert fp.get(cx, HOLDER)["status"] == "cancelled"
    assert not fp.covers(cx, HOLDER)


def test_a_new_purchase_reactivates_a_cancelling_plan(today):
    cx = _cx()
    fp.activate(cx, HOLDER, next_charge_at=END, customer_id="c", payment_method_id="p")
    fp.cancel_at_period_end(cx, HOLDER)
    fp.activate(cx, HOLDER, next_charge_at=END, customer_id="c", payment_method_id="p")
    assert fp.get(cx, HOLDER)["status"] == "active"
    assert fp.access_ends(cx, HOLDER) is None


# ---- the portal route and the portal page -------------------------------------------

@pytest.fixture
def portal_plan(monkeypatch, today):
    monkeypatch.setenv("FAMILY_PLAN_ENABLED", "1")
    with sqlite3.connect(appmod.LOG_DB) as cx:
        cx.row_factory = sqlite3.Row
        fp.init_family_plan_table(cx)
        hh.init_household_tables(cx)
        cx.execute("DELETE FROM family_subscriptions WHERE caregiver_email=?", (HOLDER,))
        fp.activate(cx, HOLDER, next_charge_at=END, customer_id="c", payment_method_id="p")
        hh.add_member(cx, HOLDER, MEMBER, relationship="child")
        cx.commit()
    yield
    with sqlite3.connect(appmod.LOG_DB) as cx:
        cx.execute("DELETE FROM family_subscriptions WHERE caregiver_email=?", (HOLDER,))
        for t, col in (("household_members", "member_email"), ("households", "primary_email")):
            try:
                cx.execute(f"DELETE FROM {t} WHERE {col} IN (?,?)", (HOLDER, MEMBER))
            except sqlite3.OperationalError:
                pass
        cx.commit()


def _tok(email):
    from dashboard import client_portal as cp
    with sqlite3.connect(appmod.LOG_DB) as cx:
        cx.row_factory = sqlite3.Row
        cp.init_client_portal_table(cx)
        cp.upsert_portal(cx, email, "X", {})
        tok = cp.reissue_token(cx, email)
        cx.commit()
    return tok


def test_portal_cancel_answers_with_the_end_date(portal_plan):
    appmod.app.config["TESTING"] = True
    c = appmod.app.test_client()
    tok = _tok(HOLDER)
    r = c.post(f"/api/portal/{tok}/family-plan/cancel")
    assert r.status_code == 200 and r.get_json() == {"ok": True, "access_until": END}
    # A retry after a lost reply gets the same answer and changes nothing.
    r = c.post(f"/api/portal/{tok}/family-plan/cancel")
    assert r.get_json() == {"ok": True, "access_until": END}
    with sqlite3.connect(appmod.LOG_DB) as cx:
        cx.row_factory = sqlite3.Row
        assert fp.get(cx, HOLDER)["status"] == fp.CANCELLING
        assert fp.covers(cx, MEMBER)
    # The covered member still cannot cancel the holder's plan.
    r = c.post(f"/api/portal/{_tok(MEMBER)}/family-plan/cancel")
    assert r.status_code == 409


def test_portal_options_carry_the_end_date(portal_plan):
    with sqlite3.connect(appmod.LOG_DB) as cx:
        cx.row_factory = sqlite3.Row
        fp.cancel_at_period_end(cx, HOLDER)
        cx.commit()
    plan = appmod._portal_options_for(HOLDER)["family_plan"]
    assert plan["active"] is True and plan["holder"] is True and plan["ends"] == END
    member = appmod._portal_options_for(MEMBER)["family_plan"]
    assert member["active"] is True and member["holder"] is False


def test_wording():
    src = open(os.path.join(ROOT, "app.py")).read()
    assert "this month's Family Plan charge" not in src      # the annual plan exists too
    assert "We could not process the latest Family Plan charge" in src
    page = open(os.path.join(ROOT, "static", "client-portal.html")).read()
    assert "lose their unlocked analysis at the end of the cycle" not in page
    assert ("Cancel the Family Plan? It will not renew. Your household keeps its unlocked "
            "analysis until the end of the period you have paid for.") in page


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
def test_portal_line_after_cancel_shows_the_date_and_no_cancel_button():
    page = open(os.path.join(ROOT, "static", "client-portal.html")).read()
    esc = re.search(r"const esc = [^\n]*\n", page) or re.search(r"function esc\([\s\S]*?\n\}", page)
    fmt = re.search(r"const fmtDate = \(d\)=>\{[\s\S]*?\n\};", page)
    line = re.search(r"function familyPlanLine\(fpl\)\{[\s\S]*?\n\}", page)
    assert esc and fmt and line
    js = (esc.group(0) + "\n" + fmt.group(0) + "\n" + line.group(0) + "\n"
          "const base={label:'Household Membership',price_cents:14700,value_cents:19700};\n"
          "const a=familyPlanLine({...base,active:true,holder:true,ends:'2027-11-01'});\n"
          "const b=familyPlanLine({...base,active:true,holder:true,ends:null});\n"
          "console.log(JSON.stringify([a,b]));\n")
    r = subprocess.run(["node", "-e", js], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    import json
    a, b = json.loads(r.stdout)
    assert "is cancelled and will not renew" in a and "Nov 1, 2027" in a
    assert "data-fp-cancel" not in a
    assert "data-fp-cancel" in b and "will not renew" not in b
