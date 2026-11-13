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
    assert "is cancelled and will not renew" in a
    assert "through the day before Nov 1, 2027, when it would have renewed" in a
    assert "data-fp-cancel" not in a
    assert "data-fp-cancel" in b and "will not renew" not in b


# ---- races (review round 2) ----------------------------------------------------------

def test_cancel_does_not_overwrite_a_change_made_after_it_read(today, monkeypatch):
    """The console cancels between the holder's read and write: it stays cancelled."""
    cx = _cx()
    fp.activate(cx, HOLDER, next_charge_at=END, customer_id="c", payment_method_id="p")
    real_get = fp.get
    calls = {"n": 0}

    def racing_get(c, email):
        row = real_get(c, email)
        calls["n"] += 1
        if calls["n"] == 1:
            fp.set_status(c, HOLDER, "cancelled")       # lands after the read
        return row
    monkeypatch.setattr(fp, "get", racing_get)
    assert fp.cancel_at_period_end(cx, HOLDER) is None
    assert real_get(cx, HOLDER)["status"] == "cancelled"
    assert not fp.covers(cx, HOLDER)


def test_renewal_never_revives_a_cancelled_plan():
    cx = _cx()
    fp.activate(cx, HOLDER, next_charge_at=TODAY, customer_id="c", payment_method_id="p")
    fp.set_status(cx, HOLDER, "cancelled")
    assert fp.mark_charged(cx, HOLDER, END) is False
    fp.mark_failed(cx, HOLDER, END)
    row = fp.get(cx, HOLDER)
    assert row["status"] == "cancelled" and row["next_charge_at"] == TODAY
    fp.set_status(cx, HOLDER, "active")
    assert fp.mark_charged(cx, HOLDER, END) is True


@pytest.fixture
def cron_db():
    with sqlite3.connect(appmod.LOG_DB) as cx:
        cx.row_factory = sqlite3.Row
        fp.init_family_plan_table(cx)
        cx.execute("DELETE FROM family_subscriptions WHERE caregiver_email=?", (HOLDER,))
        fp.activate(cx, HOLDER, next_charge_at="2026-01-01", customer_id="cus",
                    payment_method_id="pm")
        cx.commit()
    yield
    with sqlite3.connect(appmod.LOG_DB) as cx:
        cx.execute("DELETE FROM family_subscriptions WHERE caregiver_email=?", (HOLDER,))
        cx.execute("DELETE FROM family_sub_charges WHERE caregiver_email=?", (HOLDER,))
        cx.commit()


def _cron():
    from unittest import mock  # noqa: F401
    appmod.app.config["TESTING"] = True
    return appmod.app.test_client().post("/api/cron/family-plan/charge",
                                         headers={"X-Console-Key": appmod.CONSOLE_SECRET})


def _status():
    with sqlite3.connect(appmod.LOG_DB) as cx:
        cx.row_factory = sqlite3.Row
        return fp.get(cx, HOLDER)["status"]


def test_cron_skips_a_plan_cancelled_after_the_due_list_was_read(cron_db, monkeypatch):
    from unittest import mock
    real_due = fp.due

    def due_then_cancel(c, day):
        rows = real_due(c, day)
        with sqlite3.connect(appmod.LOG_DB) as cx2:
            fp.set_status(cx2, HOLDER, "cancelled")
        return rows
    monkeypatch.setattr(fp, "due", due_then_cancel)
    with mock.patch("dashboard.stripe_pay.charge_off_session") as charge:
        _cron()
    assert not any(c.kwargs.get("metadata", {}).get("email") == HOLDER
                   for c in charge.call_args_list)
    assert _status() == "cancelled"


def test_cron_charge_landing_after_a_cancel_keeps_it_cancelled_and_asks_for_a_refund(cron_db):
    from unittest import mock

    def charge_while_cancelled(*a, **k):
        with sqlite3.connect(appmod.LOG_DB) as cx2:
            fp.set_status(cx2, HOLDER, "cancelled")
        return {"id": "pi_late", "status": "succeeded"}
    with mock.patch("dashboard.stripe_pay.charge_off_session",
                    side_effect=charge_while_cancelled), \
         mock.patch.object(appmod, "send_evox_email") as mail:
        _cron()
    assert _status() == "cancelled"
    subjects = [c.args[2] for c in mail.call_args_list]
    assert any(s.startswith("Refund needed: Family Plan charged after cancel") for s in subjects)
    assert all(c.args[0] == appmod.GLEN_CONSULT_EMAIL for c in mail.call_args_list)
