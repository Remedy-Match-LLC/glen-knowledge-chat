"""Family Plan store + entitlement. Pure sqlite, no Flask, no Stripe.

One plan per CAREGIVER. It is the single entitlement source for the whole
consented household — full scan results, member product pricing, the monthly
family shipment, and group coaching access all ask the same `covers()`.

Mirrors dashboard/coach_subscriptions.py: this module holds state only;
money-path correctness (idempotent first charge, no cron double-charge) lives in
the routes/cron, guarded by next_charge_at advancing only on success.

`past_due` still covers: a failed renewal must not blur a client's report
mid-month. The cron cancels after its retry budget, and that stops cover at once.
A holder's own cancel keeps cover to the end of the paid period (CANCELLING).

The household annual is a one-time term (Glen, 2026-11-13: neither annual plan renews
by itself). It is never charged again. Its cover ends on next_charge_at, which for a
term is the end date, and a renewal adds a year from that date (TERM_CADENCE_MONTHS).
"""

TIERS = {
    "family_monthly": {
        "key": "family_monthly", "amount_cents": 14700,
        "cadence_months": 1, "label": "Household Membership — Monthly",
    },
    "family_annual": {
        "key": "family_annual", "amount_cents": 149700,
        "cadence_months": 12, "label": "Household Membership — Annual",
    },
}
# Backward-compatible alias used by the portal and console while they migrate to tiers.
PLAN = {**TIERS["family_monthly"], "value_cents": 19700}


def get_tier(key):
    return TIERS.get(key)


# A plan billed every 12 months or more is a one-time term: paid once, never charged
# again, cover ends on its end date. Keyed on the stored cadence so the household
# annual sold before this rule follows it too, with no data change (Glen, 2026-11-13).
TERM_CADENCE_MONTHS = 12


def is_term(row):
    """True for a paid one-time term (the household annual). A comped plan is not."""
    if not row:
        return False
    return (int(row.get("cadence_months") or 1) >= TERM_CADENCE_MONTHS
            and (row.get("source") or "") != "comp")


def label_for(row):
    """The plan's own name: an annual plan is not called Monthly."""
    return TIERS["family_annual" if row and int(row.get("cadence_months") or 1)
                 >= TERM_CADENCE_MONTHS else "family_monthly"]["label"]

# Statuses that still entitle. `past_due` = grace while the cron retries.
ACTIVE_STATUSES = ("active", "past_due")

# Glen, 2026-11-13: a plan the holder cancels keeps its cover until the period already
# paid for ends. The row says `cancelling` and next_charge_at is that end date. It is
# never charged again (due() reads only ACTIVE_STATUSES), and it stops entitling on
# that date with no job needed. An unpaid (past_due) or comped plan has no paid period
# left, so it is cancelled at once.
CANCELLING = "cancelling"


def _today():
    """Today in Hilo. A cancelled plan's cover then runs to midnight Hawaii time on the
    renewal date, as the portal says, not 2pm the day before (review round 3)."""
    from datetime import datetime
    from zoneinfo import ZoneInfo
    return datetime.now(ZoneInfo("Pacific/Honolulu")).date().isoformat()


# Entitling rows: an active or past_due plan (a one-time term only before its end
# date), or a cancelling one inside its paid period.
_ENTITLED_SQL = ("((status IN (?,?) AND (coalesce(cadence_months,1) < ? OR source = 'comp' "
                 "OR (next_charge_at IS NOT NULL AND substr(next_charge_at,1,10) > ?))) "
                 "OR (status=? AND next_charge_at IS NOT NULL "
                 "AND substr(next_charge_at,1,10) > ?))")


def _entitled_params(today=None):
    today = today or _today()
    return (*ACTIVE_STATUSES, TERM_CADENCE_MONTHS, today, CANCELLING, today)

_DDL = """
CREATE TABLE IF NOT EXISTS family_subscriptions (
    caregiver_email TEXT PRIMARY KEY,
    amount_cents INTEGER,
    stripe_customer_id TEXT,
    payment_method_id TEXT,
    status TEXT,
    source TEXT,
    started_at TEXT,
    next_charge_at TEXT,
    last_charged_at TEXT,
    cadence_months INTEGER DEFAULT 1,
    fail_count INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_familysub_due ON family_subscriptions(status, next_charge_at);
CREATE TABLE IF NOT EXISTS family_sub_charges (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    caregiver_email TEXT,
    amount_cents INTEGER,
    pi_id TEXT,
    status TEXT,
    charged_at TEXT
);
"""


def _now():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def _lc(email):
    return (email or "").strip().lower()


def init_family_plan_table(cx):
    cx.executescript(_DDL)
    for ddl in ("ALTER TABLE family_subscriptions ADD COLUMN cadence_months INTEGER DEFAULT 1",
                "ALTER TABLE family_subscriptions ADD COLUMN last_reminder_at TEXT"):
        try:
            cx.execute(ddl)
            cx.commit()
        except Exception:
            try:
                cx.rollback()
            except Exception:
                pass
    cx.commit()


def activate(cx, caregiver_email, *, next_charge_at, customer_id=None,
             payment_method_id=None, source="stripe", amount_cents=None,
             cadence_months=1):
    """Start (or restart) a caregiver's plan. A comped plan passes source='comp'
    with no card and no next_charge_at — same entitlement, never billed.

    `amount_cents` sets a special per-household price (mirrors the per-client
    special pricing we give on Biofield tests); it defaults to PLAN['amount_cents'].
    The charge cron bills this stored amount, so a special price recurs at that
    rate. Negative amounts are rejected."""
    amt = int(amount_cents) if amount_cents is not None else PLAN["amount_cents"]
    if amt < 0:
        raise ValueError("amount_cents must be non-negative")
    cx.execute(
        "INSERT INTO family_subscriptions (caregiver_email,amount_cents,stripe_customer_id,"
        "payment_method_id,status,source,started_at,next_charge_at,cadence_months,fail_count) "
        "VALUES (?,?,?,?,'active',?,?,?,?,0) "
        "ON CONFLICT(caregiver_email) DO UPDATE SET "
        "amount_cents=excluded.amount_cents, stripe_customer_id=excluded.stripe_customer_id, "
        "payment_method_id=excluded.payment_method_id, status='active', "
        "source=excluded.source, next_charge_at=excluded.next_charge_at, "
        "cadence_months=excluded.cadence_months, fail_count=0",
        (_lc(caregiver_email), amt, customer_id, payment_method_id,
         source, _now(), next_charge_at, int(cadence_months)))
    cx.commit()


def get(cx, caregiver_email):
    row = cx.execute("SELECT * FROM family_subscriptions WHERE caregiver_email=?",
                     (_lc(caregiver_email),)).fetchone()
    return dict(row) if row else None


def set_status(cx, caregiver_email, status):
    cx.execute("UPDATE family_subscriptions SET status=? WHERE caregiver_email=?",
               (status, _lc(caregiver_email)))
    cx.commit()


def is_active(cx, caregiver_email):
    """True when this email holds an entitling plan of their own."""
    email = _lc(caregiver_email)
    if not email:
        return False
    row = cx.execute(
        "SELECT 1 FROM family_subscriptions WHERE caregiver_email=? AND " + _ENTITLED_SQL,
        (email, *_entitled_params())).fetchone()
    return row is not None


def cancel_at_period_end(cx, caregiver_email, today=None):
    """The holder cancels. Returns the date cover ends (YYYY-MM-DD) when a paid period
    is still running, else None after cancelling at once. Repeating it changes nothing."""
    today = today or _today()
    email = _lc(caregiver_email)
    for _ in range(3):
        row = get(cx, email)
        if not row:
            return None
        status = row.get("status") or ""
        raw_end = row.get("next_charge_at")
        end = (raw_end or "")[:10]
        if status == CANCELLING:
            return end if end > today else None
        if status == "cancelled":
            return None
        to = (CANCELLING if status == "active" and end and end > today
              and (row.get("source") or "") != "comp" else "cancelled")
        # Only from the state just read: a console cancel or a renewal that lands in
        # between must not be overwritten. On a lost race, read again.
        if raw_end is None:
            cur = cx.execute("UPDATE family_subscriptions SET status=? WHERE caregiver_email=? "
                             "AND status=? AND next_charge_at IS NULL", (to, email, status))
        else:
            cur = cx.execute("UPDATE family_subscriptions SET status=? WHERE caregiver_email=? "
                             "AND status=? AND next_charge_at=?", (to, email, status, raw_end))
        cx.commit()
        if (cur.rowcount or 0) == 1:
            return end if to == CANCELLING else None
    return None


def last_covered_day(end):
    """Cover stops at the start of its end date (Hilo), so the last covered day is the
    day before. 2027-08-12 -> 2027-08-11."""
    from datetime import date, timedelta
    return (date.fromisoformat(str(end)[:10]) - timedelta(days=1)).isoformat()


def long_date(iso):
    """2027-08-11 -> '11 August 2027', the way client mail writes a date."""
    from datetime import date
    d = date.fromisoformat(str(iso)[:10])
    return f"{d.day} {d:%B} {d.year}"


def term_ends(cx, caregiver_email, today=None):
    """The end date of a running one-time term (household annual); else None."""
    row = get(cx, caregiver_email)
    if not row or row.get("status") not in (*ACTIVE_STATUSES, CANCELLING) or not is_term(row):
        return None
    end = (row.get("next_charge_at") or "")[:10]
    return end if end > (today or _today()) else None


def activate_term(cx, caregiver_email, *, months, amount_cents, customer_id=None,
                  today=None):
    """Start or extend a one-time term, keeping no card. A term still running is
    extended from its end date. The write is conditional on the row just read, so two
    renewals landing at once each add their year (review round 2). Returns the new
    end date."""
    from dashboard.subscriptions import add_months
    email = _lc(caregiver_email)
    for _ in range(5):
        row = get(cx, email)
        start = renewal_start(cx, email, today)
        end = add_months(start, int(months))
        if row is None:
            cur = cx.execute(
                "INSERT INTO family_subscriptions (caregiver_email,amount_cents,"
                "stripe_customer_id,payment_method_id,status,source,started_at,"
                "next_charge_at,cadence_months,fail_count) VALUES (?,?,?,NULL,'active',"
                "'stripe',?,?,?,0) ON CONFLICT(caregiver_email) DO NOTHING",
                (email, int(amount_cents), customer_id, _now(), end, int(months)))
        else:
            prev = row.get("next_charge_at")
            cur = cx.execute(
                "UPDATE family_subscriptions SET amount_cents=?, stripe_customer_id=?, "
                "payment_method_id=NULL, status='active', source='stripe', "
                "next_charge_at=?, cadence_months=?, fail_count=0 WHERE caregiver_email=? "
                "AND status=? AND " + ("next_charge_at IS NULL" if prev is None
                                       else "next_charge_at=?"),
                (int(amount_cents), customer_id, end, int(months), email, row.get("status"),
                 *(() if prev is None else (prev,))))
        if (cur.rowcount or 0) == 1:
            return end
    raise RuntimeError("household term write kept losing a race")


def renewal_start(cx, caregiver_email, today=None):
    """Where a newly bought term starts: the end of a term still running, so an early
    renewal loses no paid days; otherwise today. A cancelled annual still inside its
    paid period counts as running too."""
    today = today or _today()
    row = get(cx, caregiver_email)
    if row and int(row.get("cadence_months") or 1) >= TERM_CADENCE_MONTHS \
            and (row.get("source") or "") != "comp" \
            and row.get("status") in (*ACTIVE_STATUSES, CANCELLING):
        end = (row.get("next_charge_at") or "")[:10]
        if end > today:
            return end
    return today


def terms_ending(cx, today, lead_days=3):
    """Running one-time terms whose cover ends within `lead_days` days, for the
    renewal reminder. A holder who cancelled is not reminded."""
    from datetime import date, timedelta
    horizon = (date.fromisoformat(today) + timedelta(days=lead_days)).isoformat()
    rows = cx.execute(
        "SELECT * FROM family_subscriptions WHERE status IN (?,?) "
        "AND coalesce(cadence_months,1) >= ? AND (source IS NULL OR source != 'comp') "
        "AND next_charge_at IS NOT NULL AND substr(next_charge_at,1,10) > ? "
        "AND substr(next_charge_at,1,10) <= ? ORDER BY next_charge_at",
        (*ACTIVE_STATUSES, TERM_CADENCE_MONTHS, today, horizon)).fetchall()
    return [dict(r) for r in rows]


def claim_reminder(cx, caregiver_email, today, end):
    """Claim today's reminder for a term still ending on `end`. last_reminder_at holds
    the Hawaii date of the last reminder, so it is one per calendar day. The claim is
    one conditional write, so two runs cannot both send (review round 2). Returns the
    previous value to restore if the send fails, or False when not claimed."""
    email = _lc(caregiver_email)
    row = get(cx, email)
    if not row:
        return False
    prev = row.get("last_reminder_at")
    cur = cx.execute(
        "UPDATE family_subscriptions SET last_reminder_at=? WHERE caregiver_email=? "
        "AND status IN (?,?) AND coalesce(cadence_months,1) >= ? "
        "AND substr(next_charge_at,1,10)=? "
        "AND (last_reminder_at IS NULL OR substr(last_reminder_at,1,10) <> ?)",
        (today, email, *ACTIVE_STATUSES, TERM_CADENCE_MONTHS, end, today))
    cx.commit()
    return prev if (cur.rowcount or 0) == 1 else False


def release_reminder(cx, caregiver_email, today, prev):
    """Undo a claim whose send failed, so a later run can try again."""
    cx.execute("UPDATE family_subscriptions SET last_reminder_at=? WHERE caregiver_email=? "
               "AND last_reminder_at=?", (prev, _lc(caregiver_email), today))
    cx.commit()


def access_ends(cx, caregiver_email, today=None):
    """The date a cancelled plan's cover ends, while it is still running; else None."""
    row = get(cx, caregiver_email)
    if not row or row.get("status") != CANCELLING:
        return None
    end = (row.get("next_charge_at") or "")[:10]
    return end if end > (today or _today()) else None


def covers(cx, email):
    """True when `email` is entitled — either they hold the plan, or a caregiver
    who holds it is linked to them via a household/caregiver link.

    Entitlement is INDEPENDENT of report-sharing consent: share_consent governs
    whether the caregiver can VIEW the member's reports (household.can_view), a
    separate axis from whether the member is covered by the caregiver's paid
    plan. A member linked to an active plan-holder is covered here even if they
    (or the caregiver) have not consented to report sharing.
    """
    email = _lc(email)
    if not email:
        return False
    if is_active(cx, email):
        return True
    from dashboard import household as _hh
    for cg in _hh.caregivers_for(cx, email):
        if is_active(cx, cg["primary_email"]):
            return True
    return False


def list_active(cx) -> list:
    """Every entitling family plan, newest first. The members board had no way to
    read these at all: it queried `subscriptions` and `memberships` and never
    `family_subscriptions`, so a family plan granted member pricing while the
    board showed nobody. Four of eight family-plan people were invisible on
    2026-09-12, including a plan HOLDER.

    ACTIVE_STATUSES includes `past_due` on purpose, because entitlement survives a
    failed charge. The board shows the real status so a past_due plan is visible
    rather than silently equal to a paid one.
    """
    rows = cx.execute(
        "SELECT caregiver_email, status, source, amount_cents, cadence_months, "
        "started_at, next_charge_at, fail_count FROM family_subscriptions "
        "WHERE " + _ENTITLED_SQL + " ORDER BY started_at DESC", _entitled_params()).fetchall()
    out = []
    for r in rows:
        d = dict(zip(("caregiver_email", "status", "source", "amount_cents",
                      "cadence_months", "started_at", "next_charge_at", "fail_count"), r))
        d["caregiver_email"] = _lc(d.get("caregiver_email"))
        out.append(d)
    return out


def active_plan_holders_for(cx, email):
    """Return active caregiver emails whose plan covers ``email``."""
    email = _lc(email)
    if not email:
        return set()
    holders = {email} if is_active(cx, email) else set()
    from dashboard import household as _hh
    for cg in _hh.caregivers_for(cx, email):
        caregiver = _lc(cg.get("primary_email"))
        if is_active(cx, caregiver):
            holders.add(caregiver)
    return holders


def shared_active_plan_holder(cx, emails):
    """Return one active plan holder shared by every nonblank email, else None."""
    normalized = [_lc(email) for email in emails if _lc(email)]
    if not normalized:
        return None
    shared = active_plan_holders_for(cx, normalized[0])
    for email in normalized[1:]:
        shared &= active_plan_holders_for(cx, email)
        if not shared:
            return None
    return sorted(shared)[0] if shared else None


def due(cx, today):
    """Billable subs whose next_charge_at has arrived. Includes both 'active' and
    'past_due': a past_due sub (a prior failed charge) still entitles the household
    (grace) and MUST be retried on each cron run so its fail_count can climb to the
    cancel threshold — otherwise a single failed payment would cover forever.
    Comped plans (source='comp', next_charge_at NULL) are never billable, excluded.
    A one-time term (the household annual) is never billable either."""
    rows = cx.execute(
        "SELECT * FROM family_subscriptions WHERE status IN ('active','past_due') "
        "AND next_charge_at IS NOT NULL AND next_charge_at <= ? "
        "AND coalesce(cadence_months,1) < ? "
        "AND (source IS NULL OR source != 'comp') ORDER BY next_charge_at",
        (today, TERM_CADENCE_MONTHS)).fetchall()
    return [dict(r) for r in rows]


def mark_charged(cx, caregiver_email, next_charge_at):
    """Advance a renewed plan. Only an active or past_due plan is renewed: a plan
    cancelled while its charge was in flight stays cancelled. Returns True when the
    plan was advanced, False when the caller must refund the charge by hand."""
    cur = cx.execute("UPDATE family_subscriptions SET next_charge_at=?, last_charged_at=?, "
                     "fail_count=0, status='active' WHERE caregiver_email=? "
                     "AND status IN (?,?) AND coalesce(cadence_months,1) < ?",
                     (next_charge_at, _now(), _lc(caregiver_email), *ACTIVE_STATUSES,
                      TERM_CADENCE_MONTHS))
    cx.commit()
    return (cur.rowcount or 0) == 1


def mark_failed(cx, caregiver_email, retry_at):
    """Record a failed charge: bump fail_count, set past_due, and schedule the next
    retry by moving next_charge_at to retry_at (so the daily cron re-attempts on that
    date, not every day — spacing the dunning retries). A later success resets
    fail_count via mark_charged; enough failed attempts get the plan cancelled by the
    cron. Advancing next_charge_at here is a retry schedule, not a paid-cycle advance
    (the charge captured no money), so it does not violate the paid-once invariant."""
    cx.execute("UPDATE family_subscriptions SET fail_count=fail_count+1, status='past_due', "
               "next_charge_at=? WHERE caregiver_email=? AND status IN (?,?) "
               "AND coalesce(cadence_months,1) < ?",
               (retry_at, _lc(caregiver_email), *ACTIVE_STATUSES, TERM_CADENCE_MONTHS))
    cx.commit()


def record_charge(cx, *, caregiver_email, amount_cents, pi_id, status):
    cx.execute("INSERT INTO family_sub_charges (caregiver_email,amount_cents,pi_id,status,charged_at) "
               "VALUES (?,?,?,?,?)", (_lc(caregiver_email), amount_cents, pi_id, status, _now()))
    cx.commit()
