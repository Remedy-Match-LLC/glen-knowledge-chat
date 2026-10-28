"""Reorder loyalty treats a household as one buyer, and the preview prices as the invoice.

Glen, 2026-10-28, sales tab: "treat the household as one buyer", "make sure that rule
applies to all households", and "yes" to the scope in sales/plans/
2026-10-28-household-reorder-loyalty-brief.md. Anyone linked in household_members, both
ways, is the same buyer: a purchase or a paid Biofield by any member earns the loyalty
price for all of them.

The case: Michael Hill bought Spike Shield on three paid orders. His household primary,
Steve Fox, was priced $63 for it. Sales also found /api/orders/price-preview reading $63
where the invoice charged $50: the preview left out a member's repertoire rate.
"""
import json
import sqlite3

import pytest

import app as app_mod
from dashboard import biofield_store, cohorts, household

LOYALTY_CENTS = 5000
SLUG = "spike-shield"
STEVE, MICHAEL, KID, SIBLING = ("steve@example.com", "michael@example.com",
                                "kid@example.com", "sibling@example.com")
OUTSIDER = "outsider@example.com"


@pytest.fixture
def logdb(tmp_path, monkeypatch):
    path = str(tmp_path / "chat_log.db")
    monkeypatch.setattr(app_mod, "LOG_DB", path)
    monkeypatch.setenv("COHORT_PRICING_ENABLED", "1")
    cx = sqlite3.connect(path)
    cohorts.init_tables(cx)
    cohorts.upsert_cohort(cx, key="loyalty", name="Loyalty",
                          policy={"type": "reorder_loyalty", "ff_cents": LOYALTY_CENTS})
    biofield_store.init_table(cx)
    household.init_household_tables(cx)
    from dashboard import orders
    orders.init_orders_table(cx)
    cx.commit()
    yield cx
    cx.close()


def _paid_biofield(cx, email):
    cx.execute("INSERT INTO biofield_readiness(email, paid_at) VALUES(?, ?)",
               (email, "2026-01-01T00:00:00"))
    cx.commit()


def _order(cx, email, slug, *, pay_status="paid", status="done"):
    cols = {"email": email, "items_json": json.dumps([{"slug": slug, "qty": 1}]),
            "pay_status": pay_status, "status": status,
            "created_at": "2026-01-01", "updated_at": "2026-01-01"}
    for r in cx.execute("PRAGMA table_info(orders)").fetchall():
        if r[3] and r[4] is None and not r[5]:
            cols.setdefault(r[1], "test")
    cols["external_ref"] = f"test-{cx.execute('SELECT count(*) FROM orders').fetchone()[0]}"
    cx.execute(f"INSERT INTO orders({','.join(cols)}) VALUES({','.join('?' * len(cols))})",
               tuple(cols.values()))
    cx.commit()


def _link(cx, primary, member, relationship="partner"):
    household.add_member(cx, primary, member, relationship=relationship)
    cx.commit()


def _earned(cx, email):
    cx.row_factory = sqlite3.Row
    try:
        return app_mod._earned_reorder_slugs(cx, email)
    finally:
        cx.row_factory = None


# ── household_emails ────────────────────────────────────────────────────────

def test_the_household_is_read_both_ways_and_across_members(logdb):
    _link(logdb, STEVE, MICHAEL)
    _link(logdb, STEVE, KID, "child")
    whole = {STEVE, MICHAEL, KID}
    assert household.household_emails(logdb, STEVE) == whole
    assert household.household_emails(logdb, MICHAEL) == whole
    assert household.household_emails(logdb, KID) == whole
    assert household.household_emails(logdb, OUTSIDER) == {OUTSIDER}
    assert household.household_emails(logdb, "") == set()


def test_it_agrees_with_same_household(logdb):
    _link(logdb, STEVE, MICHAEL)
    _link(logdb, STEVE, KID)
    _link(logdb, MICHAEL, SIBLING)   # Michael heads another household too
    for e in (STEVE, MICHAEL, KID, SIBLING, OUTSIDER):
        got = household.household_emails(logdb, e)
        for other in (STEVE, MICHAEL, KID, SIBLING, OUTSIDER):
            assert (other in got) == household.same_household(logdb, e, other), (e, other)


def test_stored_case_and_spaces_still_link(logdb):
    logdb.execute("INSERT INTO household_members(primary_email, member_email, relationship) "
                  "VALUES(?, ?, 'partner')", (" Steve@Example.com ", "MICHAEL@example.com"))
    logdb.commit()
    assert household.household_emails(logdb, "michael@example.com") == {STEVE, MICHAEL}


# ── earning ─────────────────────────────────────────────────────────────────

def test_a_member_earns_a_product_only_another_member_bought(logdb):
    _link(logdb, STEVE, MICHAEL)
    _paid_biofield(logdb, STEVE)
    for _ in range(3):
        _order(logdb, MICHAEL, SLUG)
    assert SLUG in _earned(logdb, STEVE)
    assert SLUG in _earned(logdb, MICHAEL)


def test_a_paid_biofield_by_any_member_counts(logdb):
    _link(logdb, STEVE, MICHAEL)
    _paid_biofield(logdb, MICHAEL)
    _order(logdb, MICHAEL, SLUG)
    assert SLUG in _earned(logdb, STEVE)


def test_someone_outside_the_household_does_not_earn_it(logdb):
    _link(logdb, STEVE, MICHAEL)
    _paid_biofield(logdb, STEVE)
    _paid_biofield(logdb, OUTSIDER)
    _order(logdb, MICHAEL, SLUG)
    assert _earned(logdb, OUTSIDER) == set()


def test_no_paid_biofield_anywhere_earns_nothing(logdb):
    _link(logdb, STEVE, MICHAEL)
    _order(logdb, MICHAEL, SLUG)
    assert _earned(logdb, STEVE) == set()


@pytest.mark.parametrize("status", ["proposed", "pending", "new", "confirmed"])
def test_an_unpaid_order_earns_nothing(logdb, status):
    _link(logdb, STEVE, MICHAEL)
    _paid_biofield(logdb, STEVE)
    _order(logdb, MICHAEL, SLUG, pay_status="unpaid", status=status)
    assert _earned(logdb, STEVE) == set()


# ── the invoice and the preview ─────────────────────────────────────────────

def _preview(email, lines):
    app_mod.app.config["TESTING"] = True
    r = app_mod.app.test_client().post(
        "/api/orders/price-preview", json={"email": email, "lines": lines},
        headers={"X-Console-Key": app_mod.CONSOLE_SECRET})
    assert r.status_code == 200, r.data
    return {l["slug"]: l["effective_unit_cents"] for l in r.get_json()["lines"]}


def _invoice(email, lines):
    priced = app_mod._price_inhouse_invoice(lines, email=email, pickup=True, ship=None)
    return {r["slug"]: r["unit_cents"] for r in priced["items_rec"]}


def test_steve_gets_the_loyalty_price_on_the_invoice_and_the_preview(logdb):
    _link(logdb, STEVE, MICHAEL)
    _paid_biofield(logdb, STEVE)
    _order(logdb, MICHAEL, SLUG)
    lines = [{"slug": SLUG, "qty": 1}]
    assert _invoice(STEVE, lines)[SLUG] == LOYALTY_CENTS
    assert _preview(STEVE, lines)[SLUG] == LOYALTY_CENTS
    # Outside the household: the ordinary price, above the loyalty rate.
    assert _invoice(OUTSIDER, lines)[SLUG] > LOYALTY_CENTS
    assert _preview(OUTSIDER, lines)[SLUG] > LOYALTY_CENTS


def test_the_preview_applies_the_repertoire_rate_as_the_invoice_does(logdb, monkeypatch):
    lines = [{"slug": SLUG, "qty": 1}, {"slug": "microbiome", "qty": 1}]
    before = _invoice(OUTSIDER, lines)
    monkeypatch.setattr(app_mod, "_resolve_repertoire_slugs",
                        lambda email: {SLUG} if email == OUTSIDER else None)
    inv, pre = _invoice(OUTSIDER, lines), _preview(OUTSIDER, lines)
    assert inv[SLUG] < before[SLUG]     # the repertoire rate applies to the invoice
    assert pre == inv                   # and the preview shows the same prices


def test_a_messy_primary_still_links_its_other_members(logdb):
    for member in ("MICHAEL@example.com", "kid@example.com"):
        logdb.execute("INSERT INTO household_members(primary_email, member_email, relationship) "
                      "VALUES(?, ?, 'partner')", (" Steve@Example.com ", member))
    logdb.commit()
    assert household.household_emails(logdb, KID) == {STEVE, MICHAEL, KID}


def test_an_order_stored_with_capitals_still_earns(logdb):
    _link(logdb, STEVE, MICHAEL)
    _paid_biofield(logdb, STEVE)
    _order(logdb, "Michael@Example.com", SLUG)
    assert SLUG in _earned(logdb, STEVE)


@pytest.mark.parametrize("pay_status,status", [("paid", "confirmed"), ("unpaid", "shipped"),
                                               ("unpaid", "delivered"), ("unpaid", "done")])
def test_a_paid_or_fulfilled_order_earns(logdb, pay_status, status):
    _link(logdb, STEVE, MICHAEL)
    _paid_biofield(logdb, STEVE)
    _order(logdb, MICHAEL, SLUG, pay_status=pay_status, status=status)
    assert SLUG in _earned(logdb, STEVE)


@pytest.mark.parametrize("pay_status,status", [("paid", "cancelled"), ("paid", "canceled"),
                                               ("paid", "refunded"), ("refunded", "done"),
                                               ("paid", "Cancelled"), ("Refunded", "shipped")])
def test_a_cancelled_or_refunded_order_earns_nothing(logdb, pay_status, status):
    """It bought nothing (review round 2). Prod held 3 paid, cancelled orders."""
    _link(logdb, STEVE, MICHAEL)
    _paid_biofield(logdb, STEVE)
    _order(logdb, MICHAEL, SLUG, pay_status=pay_status, status=status)
    assert _earned(logdb, STEVE) == set()


def test_a_failed_household_lookup_keeps_the_buyers_own_history(logdb, monkeypatch):
    _link(logdb, STEVE, MICHAEL)
    _paid_biofield(logdb, STEVE)
    _order(logdb, STEVE, SLUG)
    _order(logdb, MICHAEL, "microbiome")

    def boom(*a, **k):
        raise RuntimeError("household table unreadable")
    monkeypatch.setattr(household, "household_emails", boom)
    assert _earned(logdb, STEVE) == {SLUG}


def test_the_household_lookup_leaves_the_callers_connection_alone(logdb, monkeypatch):
    """Postgres aborts a transaction on a failed read, and apply-plan saves on cx."""
    seen = []
    real = household.household_emails
    monkeypatch.setattr(household, "household_emails",
                        lambda cx, e: (seen.append(cx), real(cx, e))[1])
    _link(logdb, STEVE, MICHAEL)
    _paid_biofield(logdb, STEVE)
    _earned(logdb, STEVE)
    assert seen and all(c is not logdb for c in seen)
    for c in seen:  # a leaked connection drains the Postgres pool
        with pytest.raises(sqlite3.ProgrammingError):
            c.execute("SELECT 1")


def test_a_blank_email_has_no_household(logdb):
    logdb.execute("INSERT INTO household_members(primary_email, member_email, relationship) "
                  "VALUES('', ?, 'partner')", (MICHAEL,))
    logdb.commit()
    assert household.household_emails(logdb, "") == set()
    assert household.household_emails(logdb, "  ") == set()


def test_any_other_failure_earns_nothing(logdb, monkeypatch):
    _link(logdb, STEVE, MICHAEL)
    _paid_biofield(logdb, STEVE)
    _order(logdb, STEVE, SLUG)
    assert _earned(logdb, STEVE) == {SLUG}  # it earns until the store fails

    def boom(e):
        raise RuntimeError("biofield store unreadable")
    monkeypatch.setattr(app_mod, "_has_paid_biofield", boom)
    assert _earned(logdb, STEVE) == set()


def test_a_member_with_spaces_still_links(logdb):
    logdb.execute("INSERT INTO household_members(primary_email, member_email, relationship) "
                  "VALUES(?, ?, 'partner')", (STEVE, " michael@example.com "))
    logdb.commit()
    assert household.household_emails(logdb, MICHAEL) == {STEVE, MICHAEL}


def test_a_blank_member_row_never_matches_blank_email_orders(logdb):
    logdb.execute("INSERT INTO household_members(primary_email, member_email, relationship) "
                  "VALUES(?, '  ', 'partner')", (STEVE,))
    logdb.commit()
    _paid_biofield(logdb, STEVE)
    _order(logdb, "", SLUG)
    assert household.household_emails(logdb, STEVE) == {STEVE}
    assert _earned(logdb, STEVE) == set()


def test_the_preview_normalises_the_email_for_the_repertoire(logdb, monkeypatch):
    lines = [{"slug": SLUG, "qty": 1}]
    monkeypatch.setattr(app_mod, "_resolve_repertoire_slugs",
                        lambda email: {SLUG} if email == OUTSIDER else None)
    assert _preview(" Outsider@Example.com ", lines) == _preview(OUTSIDER, lines)


def test_no_loyalty_when_cohort_pricing_is_off(logdb, monkeypatch):
    _link(logdb, STEVE, MICHAEL)
    _paid_biofield(logdb, STEVE)
    _order(logdb, MICHAEL, SLUG)
    monkeypatch.delenv("COHORT_PRICING_ENABLED")
    assert _preview(STEVE, [{"slug": SLUG, "qty": 1}])[SLUG] > LOYALTY_CENTS


def test_a_failed_client_price_lookup_is_logged_not_fatal(logdb, monkeypatch, capsys):
    from dashboard import client_prices

    def boom(*a, **k):
        raise RuntimeError("client prices unreadable")
    monkeypatch.setattr(client_prices, "price_map", boom)
    _preview(STEVE, [{"slug": SLUG, "qty": 1}])
    assert "[price-preview] client-price lookup skipped" in capsys.readouterr().out
