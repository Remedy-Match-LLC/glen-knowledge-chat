"""Glen, 2026-11-13: neither annual plan renews by itself. The household annual
($1,497) is paid once. It is never charged again, its cover ends on its end date,
the holder gets a renewal reminder in the last 3 days, and a renewal with the same
email adds a year from the end date. The one household annual sold before this rule
follows it too (Glen, 13 Nov, via marketing), with no data change: the rule is keyed
on the stored 12-month cadence."""
import sqlite3
from unittest import mock

import pytest

import app as appmod
from dashboard import family_plan as fp
from dashboard import household as hh

HOLDER, MEMBER = "term-holder@x.com", "term-member@x.com"
TODAY, END = "2026-11-13", "2027-08-12"


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


def _annual(cx, end=END, **kw):
    kw.setdefault("customer_id", "cus")
    kw.setdefault("payment_method_id", "pm")
    fp.activate(cx, HOLDER, next_charge_at=end, amount_cents=149700, cadence_months=12, **kw)


def test_an_annual_plan_is_never_charged_and_ends_on_its_end_date(today):
    """The existing holder's row: active, saved card, next charge 12 Aug 2027."""
    cx = _cx()
    _annual(cx)
    hh.add_member(cx, HOLDER, MEMBER, relationship="child")
    assert fp.is_term(fp.get(cx, HOLDER))
    assert fp.covers(cx, HOLDER) and fp.covers(cx, MEMBER)
    assert fp.due(cx, END) == [] and fp.due(cx, "2099-01-01") == []
    assert fp.term_ends(cx, HOLDER) == END
    today["d"] = "2027-08-11"                  # the last covered day
    assert fp.covers(cx, MEMBER)
    assert [r["caregiver_email"] for r in fp.list_active(cx)] == [HOLDER]
    today["d"] = END                           # cover stops on the end date itself
    assert not fp.covers(cx, HOLDER) and not fp.covers(cx, MEMBER)
    assert fp.list_active(cx) == [] and fp.term_ends(cx, HOLDER) is None


def test_monthly_and_comped_plans_are_unchanged(today):
    cx = _cx()
    fp.activate(cx, HOLDER, next_charge_at="2026-11-01", customer_id="c", payment_method_id="p")
    assert fp.covers(cx, HOLDER)                       # monthly past its date: still covered
    assert [r["caregiver_email"] for r in fp.due(cx, TODAY)] == [HOLDER]
    assert not fp.is_term(fp.get(cx, HOLDER))
    fp.activate(cx, HOLDER, next_charge_at=None, source="comp", cadence_months=12)
    assert fp.covers(cx, HOLDER) and fp.due(cx, "2099-01-01") == []
    assert not fp.is_term(fp.get(cx, HOLDER))


def test_dates_and_labels():
    assert fp.last_covered_day("2027-08-12") == "2027-08-11"
    assert fp.long_date("2027-08-11") == "11 August 2027"
    assert fp.label_for({"cadence_months": 12}) == "Household Membership — Annual"
    assert fp.label_for({"cadence_months": 1}) == "Household Membership — Monthly"


@pytest.mark.parametrize("status,start", [
    ("active", END),          # renewing early: the new year starts when this one ends
    ("cancelling", END),      # cancelled but still inside the paid year
    ("cancelled", TODAY),
])
def test_renewal_starts_when_the_running_term_ends(today, status, start):
    cx = _cx()
    _annual(cx)
    fp.set_status(cx, HOLDER, status)
    assert fp.renewal_start(cx, HOLDER) == start


def test_renewal_after_the_term_ended_or_from_monthly_starts_today(today):
    cx = _cx()
    _annual(cx, end="2026-11-10")
    assert fp.renewal_start(cx, HOLDER) == TODAY
    fp.activate(cx, HOLDER, next_charge_at="2026-12-01", customer_id="c", payment_method_id="p")
    assert fp.renewal_start(cx, HOLDER) == TODAY


def test_terms_ending_is_the_last_three_days_only(today):
    cx = _cx()
    _annual(cx, end="2026-11-16")
    assert [r["caregiver_email"] for r in fp.terms_ending(cx, TODAY)] == [HOLDER]
    _annual(cx, end="2026-11-17")
    assert fp.terms_ending(cx, TODAY) == []
    _annual(cx, end=TODAY)
    assert fp.terms_ending(cx, TODAY) == []           # already ended
    _annual(cx, end="2026-11-15")
    fp.set_status(cx, HOLDER, fp.CANCELLING)
    assert fp.terms_ending(cx, TODAY) == []           # a holder who cancelled is not nagged


# ---- app: checkout, fulfilment, cron, portal, reminder --------------------------------

@pytest.fixture
def db_clean():
    def wipe():
        with sqlite3.connect(appmod.LOG_DB) as cx:
            cx.row_factory = sqlite3.Row
            fp.init_family_plan_table(cx)
            cx.execute("CREATE TABLE IF NOT EXISTS family_sub_grants "
                       "(session_id TEXT PRIMARY KEY, email TEXT, created_at TEXT)")
            cx.execute("DELETE FROM family_subscriptions WHERE caregiver_email=?", (HOLDER,))
            cx.execute("DELETE FROM family_sub_charges WHERE caregiver_email=?", (HOLDER,))
            cx.execute("DELETE FROM family_sub_grants WHERE email=?", (HOLDER,))
            cx.commit()
    wipe()
    yield
    wipe()


def _db_row():
    with sqlite3.connect(appmod.LOG_DB) as cx:
        cx.row_factory = sqlite3.Row
        return fp.get(cx, HOLDER)


def _fulfil(sid, pm="pm_new"):
    sess = {"metadata": {"kind": "family_plan", "email": HOLDER, "tier": "family_annual",
                         "upgrade_credit_cents": "0"},
            "payment_intent": "pi_" + sid, "amount_total": 149700}
    pi = {"status": "succeeded", "customer": "cus_new", "payment_method": pm}
    with mock.patch("dashboard.stripe_pay.get_session", return_value=sess), \
         mock.patch("dashboard.stripe_pay.get_payment_intent", return_value=pi), \
         mock.patch.object(appmod, "send_evox_email") as mail:
        res = appmod._fulfill_family_plan(sid)
    return res, mail


def test_buying_annual_keeps_no_card_and_says_it_does_not_renew(db_clean, today):
    res, mail = _fulfil("cs_term_new")
    assert res["ok"] is True
    row = _db_row()
    assert row["payment_method_id"] is None and row["cadence_months"] == 12
    assert row["next_charge_at"] == "2027-11-13"
    body = mail.call_args.args[3]
    assert "through 12 November 2027" in body and "does not renew" in body
    assert "cancel" not in body.lower()


def test_buying_annual_works_without_a_saved_card(db_clean, today):
    res, _ = _fulfil("cs_term_nocard", pm=None)
    assert res["ok"] is True and _db_row()["cadence_months"] == 12


def test_early_renewal_adds_a_year_from_the_end_date_once(db_clean, today):
    with sqlite3.connect(appmod.LOG_DB) as cx:
        cx.row_factory = sqlite3.Row
        _annual(cx)
    _fulfil("cs_term_renew")
    _fulfil("cs_term_renew")                         # webhook plus return: once only
    row = _db_row()
    assert row["next_charge_at"] == "2028-08-12"
    assert row["payment_method_id"] is None          # the old saved card is dropped too
    with sqlite3.connect(appmod.LOG_DB) as cx:
        n = cx.execute("SELECT count(*) FROM family_sub_charges WHERE caregiver_email=?",
                       (HOLDER,)).fetchone()[0]
    assert n == 1


MONTHLY = "term-monthly@x.com"


def test_the_charge_cron_never_charges_an_annual_plan(db_clean, monkeypatch):
    """Even the old row with a saved card and a date that has arrived, and even if the
    due list names it. A monthly plan due the same day is still charged, which proves
    the run reached the charging loop."""
    with sqlite3.connect(appmod.LOG_DB) as cx:
        cx.row_factory = sqlite3.Row
        cx.execute("DELETE FROM family_subscriptions WHERE caregiver_email=?", (MONTHLY,))
        _annual(cx, end="2026-01-01")
        fp.activate(cx, MONTHLY, next_charge_at="2026-01-01", customer_id="cm",
                    payment_method_id="pm_m")
    real_due = fp.due
    monkeypatch.setattr(fp, "due", lambda c, d: real_due(c, d) + [dict(_db_row())])
    with mock.patch("dashboard.stripe_pay.charge_off_session",
                    return_value={"status": "succeeded", "id": "pi_m"}) as charge:
        appmod.app.config["TESTING"] = True
        r = appmod.app.test_client().post("/api/cron/family-plan/charge",
                                          headers={"X-Console-Key": appmod.CONSOLE_SECRET})
    assert r.status_code == 200
    emails = [c.kwargs.get("metadata", {}).get("email") for c in charge.call_args_list]
    assert MONTHLY in emails and HOLDER not in emails
    assert _db_row()["next_charge_at"] == "2026-01-01"
    with sqlite3.connect(appmod.LOG_DB) as cx:
        cx.execute("DELETE FROM family_subscriptions WHERE caregiver_email=?", (MONTHLY,))
        cx.execute("DELETE FROM family_sub_charges WHERE caregiver_email=?", (MONTHLY,))


def test_a_charge_landing_after_a_switch_to_annual_never_rewrites_the_term():
    """A monthly charge in flight while the holder buys the annual: the success or the
    decline must not move the annual's end date or mark it unpaid."""
    cx = _cx()
    _annual(cx, end="2027-11-13")
    assert fp.mark_charged(cx, HOLDER, "2026-12-13") is False    # the cron asks for a refund
    fp.mark_failed(cx, HOLDER, "2026-11-15")
    row = fp.get(cx, HOLDER)
    assert row["next_charge_at"] == "2027-11-13" and row["status"] == "active"
    assert row["fail_count"] == 0


def test_a_crash_after_the_claim_leaves_the_purchase_fulfillable(db_clean, today, monkeypatch):
    real = fp.activate_term
    monkeypatch.setattr(fp, "activate_term", mock.Mock(side_effect=RuntimeError("db gone")))
    res, _ = _fulfil("cs_term_crash")
    assert res["ok"] is False and _db_row() is None
    monkeypatch.setattr(fp, "activate_term", real)
    res, _ = _fulfil("cs_term_crash")                # the webhook retry
    assert res["ok"] is True and _db_row()["next_charge_at"] == "2027-11-13"


def test_two_renewals_each_add_a_year(today):
    cx = _cx()
    _annual(cx)
    assert fp.activate_term(cx, HOLDER, months=12, amount_cents=149700) == "2028-08-12"
    assert fp.activate_term(cx, HOLDER, months=12, amount_cents=149700) == "2029-08-12"
    assert fp.get(cx, HOLDER)["payment_method_id"] is None


def test_a_renewal_that_loses_a_race_reads_again(today, monkeypatch):
    """Another renewal lands between this one's read and write: the conditional write
    misses, the loop reads again, and both years count."""
    cx = _cx()
    _annual(cx)
    real_start = fp.renewal_start
    calls = {"n": 0}

    def start_then_race(c, email, today=None):
        s = real_start(c, email, today)
        if calls["n"] == 0:
            c.execute("UPDATE family_subscriptions SET next_charge_at='2028-08-12' "
                      "WHERE caregiver_email=?", (HOLDER,))
        calls["n"] += 1
        return s
    monkeypatch.setattr(fp, "renewal_start", start_then_race)
    assert fp.activate_term(cx, HOLDER, months=12, amount_cents=149700) == "2029-08-12"
    assert calls["n"] == 2


def test_portal_shows_the_last_day_and_no_cancel(db_clean, today, monkeypatch):
    monkeypatch.setattr(appmod, "_family_plan_enabled", lambda: True)
    with sqlite3.connect(appmod.LOG_DB) as cx:
        cx.row_factory = sqlite3.Row
        _annual(cx)
    plan = appmod._portal_options_for(HOLDER)["family_plan"]
    assert plan["term_last_day"] == "2027-08-11"
    assert plan["label"] == "Household Membership — Annual"
    page = open(appmod.STATIC / "client-portal.html", encoding="utf-8").read()
    i = page.index("fpl.term_last_day){")
    branch = page[i:page.index("</p>`;", i)]
    assert "does not renew" in branch and "data-fp-cancel" not in branch
    assert i < page.index("data-fp-cancel")          # checked before the Cancel branch


def test_reminder_names_the_last_day_price_and_link(db_clean, today, monkeypatch):
    monkeypatch.setattr(appmod, "_family_plan_enabled", lambda: True)
    monkeypatch.setattr(appmod, "PUBLIC_BASE_URL", "https://test.local")
    with sqlite3.connect(appmod.LOG_DB) as cx:
        cx.row_factory = sqlite3.Row
        _annual(cx, end="2026-11-15")
    sent = []
    monkeypatch.setattr(appmod, "_send_inquiry_email",
                        lambda **k: sent.append(k) or True)
    assert appmod._remind_household_terms_ending("2026-11-13T20:00:00Z") == 1
    assert appmod._remind_household_terms_ending("2026-11-13T21:00:00Z") == 0   # once a day
    (m,) = sent
    today["d"] = "2026-11-14"                        # the next Hawaii day: one more
    assert appmod._remind_household_terms_ending("2026-11-14T19:00:00Z") == 1
    assert m["to_email"] == HOLDER
    assert m["subject"] == "Your Household Membership ends in 2 days"
    assert "through 14 November 2026" in m["body"]
    assert "no card will be charged" in m["body"]
    assert "($1,497)" in m["body"] and "https://test.local/membership" in m["body"]
    assert "—" not in m["body"]


def test_a_failed_send_is_tried_again_and_a_cancel_or_renewal_stops_it(db_clean, today,
                                                                     monkeypatch):
    monkeypatch.setattr(appmod, "_family_plan_enabled", lambda: True)
    with sqlite3.connect(appmod.LOG_DB) as cx:
        cx.row_factory = sqlite3.Row
        _annual(cx, end="2026-11-15")
    monkeypatch.setattr(appmod, "_send_inquiry_email", lambda **k: False)
    assert appmod._remind_household_terms_ending("x") == 0
    assert _db_row()["last_reminder_at"] is None      # released for a later run
    sent = []
    monkeypatch.setattr(appmod, "_send_inquiry_email", lambda **k: sent.append(k) or True)
    real = fp.terms_ending

    def listed_then_renewed(c, day, lead_days=3):
        rows = real(c, day, lead_days)
        with sqlite3.connect(appmod.LOG_DB) as cx2:
            cx2.execute("UPDATE family_subscriptions SET next_charge_at='2027-11-15' "
                        "WHERE caregiver_email=?", (HOLDER,))
        return rows
    monkeypatch.setattr(fp, "terms_ending", listed_then_renewed)
    assert appmod._remind_household_terms_ending("x") == 0 and sent == []


def test_portal_shows_the_annual_line_after_a_cancel_too(db_clean, today, monkeypatch):
    monkeypatch.setattr(appmod, "_family_plan_enabled", lambda: True)
    with sqlite3.connect(appmod.LOG_DB) as cx:
        cx.row_factory = sqlite3.Row
        _annual(cx)
        fp.cancel_at_period_end(cx, HOLDER)
    plan = appmod._portal_options_for(HOLDER)["family_plan"]
    assert plan["term_last_day"] == "2027-08-11" and plan["ends"] == END


def test_reminder_is_off_with_the_household_plan(db_clean, today, monkeypatch):
    monkeypatch.setattr(appmod, "_family_plan_enabled", lambda: False)
    with sqlite3.connect(appmod.LOG_DB) as cx:
        cx.row_factory = sqlite3.Row
        _annual(cx, end="2026-11-15")
    monkeypatch.setattr(appmod, "_send_inquiry_email", lambda **k: pytest.fail("sent"))
    assert appmod._remind_household_terms_ending("2026-11-13T20:00:00Z") == 0


def test_sales_page_no_longer_says_renewed_annually():
    page = open(appmod.STATIC / "membership-choose.html", encoding="utf-8").read()
    assert "renewed annually" not in page
    assert "A full year of household coverage. It is a single payment that does not renew." in page


def test_no_monthly_purchase_over_a_running_annual(db_clean, today, monkeypatch):
    with sqlite3.connect(appmod.LOG_DB) as cx:
        cx.row_factory = sqlite3.Row
        _annual(cx)
    with mock.patch.object(appmod, "_STRIPE_ACTIVE", True), \
         mock.patch.object(appmod, "_family_plan_enabled", return_value=True), \
         mock.patch.object(appmod, "_family_upgrade_credit", return_value=0), \
         mock.patch("dashboard.stripe_pay.create_checkout_session",
                    return_value={"id": "cs", "url": "https://stripe/cs"}) as create:
        c = appmod.app.test_client()
        r = c.post("/family-plan/checkout", json={"email": HOLDER, "tier": "family_monthly"})
        assert r.status_code == 409 and "11 August 2027" in r.get_json()["error"]
        assert not create.called
        r = c.post("/family-plan/checkout", json={"email": HOLDER, "tier": "family_annual"})
        assert r.status_code == 200 and create.call_args.kwargs["save_card"] is False


def test_console_re_enrol_refuses_a_running_annual(db_clean, today):
    with sqlite3.connect(appmod.LOG_DB) as cx:
        cx.row_factory = sqlite3.Row
        _annual(cx)
    r = appmod.app.test_client().post("/api/console/family-plan", json={"email": HOLDER},
                                      headers={"X-Console-Key": appmod.CONSOLE_SECRET})
    assert r.status_code == 409
    assert _db_row()["cadence_months"] == 12


def test_members_board_shows_an_annual_as_yearly_with_an_end_date():
    page = open(appmod.STATIC / "console-members.html", encoding="utf-8").read()
    assert "(m.cadence_months||1) >= 12" in page
    assert '`<span>ends ${esc(day(m.next_charge_date))' in page and '"/yr"' in page


def test_a_cancel_after_the_list_was_read_sends_no_reminder(db_clean, today, monkeypatch):
    monkeypatch.setattr(appmod, "_family_plan_enabled", lambda: True)
    with sqlite3.connect(appmod.LOG_DB) as cx:
        cx.row_factory = sqlite3.Row
        _annual(cx, end="2026-11-15")
    real = fp.terms_ending

    def listed_then_cancelled(c, day, lead_days=3):
        rows = real(c, day, lead_days)
        with sqlite3.connect(appmod.LOG_DB) as cx2:
            fp.set_status(cx2, HOLDER, fp.CANCELLING)
        return rows
    monkeypatch.setattr(fp, "terms_ending", listed_then_cancelled)
    monkeypatch.setattr(appmod, "_send_inquiry_email", lambda **k: pytest.fail("sent"))
    assert appmod._remind_household_terms_ending("x") == 0


def test_a_paid_monthly_checkout_never_overwrites_a_running_annual(db_clean, today):
    with sqlite3.connect(appmod.LOG_DB) as cx:
        cx.row_factory = sqlite3.Row
        _annual(cx)
    sess = {"metadata": {"kind": "family_plan", "email": HOLDER, "tier": "family_monthly"},
            "payment_intent": "pi_overlap", "amount_total": 14700}
    pi = {"status": "succeeded", "customer": "cus_m", "payment_method": "pm_m"}
    with mock.patch("dashboard.stripe_pay.get_session", return_value=sess), \
         mock.patch("dashboard.stripe_pay.get_payment_intent", return_value=pi), \
         mock.patch.object(appmod, "send_evox_email") as mail:
        res = appmod._fulfill_family_plan("cs_overlap")
    assert res == {"ok": True, "reason": "annual_running_refund_needed"}
    row = _db_row()
    assert row["cadence_months"] == 12 and row["next_charge_at"] == END
    assert row["payment_method_id"] == "pm"          # untouched
    assert "Refund needed" in mail.call_args.args[2]
    assert mail.call_args.args[0] == appmod.GLEN_CONSULT_EMAIL


def test_portal_monthly_subscribe_refuses_a_running_annual(db_clean, today, monkeypatch):
    from dashboard import client_portal as cp
    with sqlite3.connect(appmod.LOG_DB) as cx:
        cx.row_factory = sqlite3.Row
        _annual(cx)
        cp.init_client_portal_table(cx)
        cp.upsert_portal(cx, HOLDER, "T", {})
        tok = cp.reissue_token(cx, HOLDER)
        cx.commit()
    with mock.patch.object(appmod, "_STRIPE_ACTIVE", True), \
         mock.patch.object(appmod, "_family_plan_enabled", return_value=True), \
         mock.patch("dashboard.stripe_pay.create_checkout_session") as create:
        r = appmod.app.test_client().post(f"/api/portal/{tok}/family-plan/subscribe")
    assert r.status_code == 409 and not create.called


def test_membership_reminder_date_is_the_hawaii_date():
    # 03:00 UTC on 19 Aug is 17:00 on 18 Aug in Hilo.
    assert appmod._family_long_date("2027-08-19T03:00:00Z") == "18 August 2027"
    assert appmod._family_long_date("2027-08-18") == "18 August 2027"
