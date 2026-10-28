# tests/test_household_repertoire.py
"""The member repertoire discount reads the whole household.

Glen, 2026-10-28, sales tab: a household is one buyer, and "Steve has the
household/family membership, and that will extend to his elderly father even though he
lives in a different house." Prod, read-only: Michael Hill's repertoire held Spike Shield
and Steve Fox's did not, so Steve was priced $63 where the household rate was $50.

The buyer must still be a paid member. Shipping must not follow: "We'll still count him
in the household, except for shipping."
"""
import json
import sqlite3
from datetime import datetime, timedelta

import pytest

from dashboard import household

SLUG = "neuro-magnesium"
STEVE, MICHAEL, FATHER = "steve@example.com", "michael@example.com", "father@example.com"
OUTSIDER = "outsider@example.com"


@pytest.fixture
def appmod(monkeypatch, tmp_path):
    import app as appmod
    monkeypatch.setattr(appmod, "LOG_DB", str(tmp_path / "chat_log.db"))
    appmod._init_auth_tables()
    monkeypatch.setattr(appmod, "REPERTOIRE_ENABLED", True)
    appmod.app.config["TESTING"] = True
    with sqlite3.connect(appmod.LOG_DB) as cx:
        household.init_household_tables(cx)
        appmod.repertoire.init_repertoire_table(cx)
        appmod.init_membership_tables(cx)
    return appmod


def _member(appmod, email):
    expires = (datetime.utcnow() + timedelta(days=30)).isoformat() + "Z"
    with sqlite3.connect(appmod.LOG_DB) as cx:
        appmod.init_membership_tables(cx)
        cx.execute("INSERT INTO memberships (id, email, granted_at, expires_at, granted_by, "
                   "source) VALUES (?,?,?,?,?,?)",
                   (f"mem_{email}", email, datetime.utcnow().isoformat() + "Z", expires,
                    "test", "founding"))
        cx.commit()


def _rep(appmod, email, slugs):
    with sqlite3.connect(appmod.LOG_DB) as cx:
        appmod.repertoire.add_skus(cx, email, slugs)


def _link(appmod, primary, member, relationship="partner"):
    with sqlite3.connect(appmod.LOG_DB) as cx:
        household.add_member(cx, primary, member, relationship=relationship)
        cx.commit()


def _invoice_unit(appmod, email):
    priced = appmod._price_inhouse_invoice([{"slug": SLUG, "qty": 1}], email=email,
                                           pickup=True, ship={"country": "US", "state": "TX"})
    return priced["items_rec"][0]["unit_cents"]


def _cart_unit(appmod, email):
    return appmod._price_cart([{"slug": SLUG, "qty": 1}], ship={"country": "US", "state": "TX"},
                              email=email)["priced"]["lines"][0]["line_total_cents"]


def _regular(appmod):
    return appmod._get_product(SLUG)["price_cents"]


# ── the household's repertoire ──────────────────────────────────────────────
def test_a_member_gets_the_rate_for_a_product_only_another_member_has(appmod):
    _link(appmod, STEVE, MICHAEL)
    _member(appmod, STEVE)
    _rep(appmod, MICHAEL, [SLUG])
    assert SLUG in appmod._resolve_repertoire_slugs(STEVE)
    assert _invoice_unit(appmod, STEVE) < _regular(appmod)
    assert _invoice_unit(appmod, STEVE) == _cart_unit(appmod, STEVE)


def test_it_reads_both_ways_and_reaches_the_father(appmod):
    _link(appmod, STEVE, MICHAEL)
    _link(appmod, STEVE, FATHER, relationship="parent")
    _member(appmod, FATHER)
    _member(appmod, MICHAEL)
    _rep(appmod, MICHAEL, [SLUG])
    _rep(appmod, STEVE, ["spike-shield"])
    with sqlite3.connect(appmod.LOG_DB) as cx:
        assert appmod._household_repertoire_slugs(cx, FATHER) >= {SLUG, "spike-shield"}
        assert appmod._household_repertoire_slugs(cx, MICHAEL) >= {SLUG, "spike-shield"}
    assert _invoice_unit(appmod, FATHER) < _regular(appmod)


def test_steves_family_plan_covers_the_household_as_in_prod(appmod, monkeypatch):
    """Prod: Steve holds an active Family Plan; Michael and the father have no
    membership of their own and are covered through their link to him."""
    from dashboard import family_plan
    monkeypatch.setenv("FAMILY_PLAN_ENABLED", "1")
    _link(appmod, STEVE, MICHAEL)
    _link(appmod, STEVE, FATHER, relationship="parent")
    with sqlite3.connect(appmod.LOG_DB) as cx:
        family_plan.init_family_plan_table(cx)
        family_plan.activate(cx, STEVE, next_charge_at=None, source="comp")
        cx.commit()
    _rep(appmod, MICHAEL, [SLUG])
    for who in (STEVE, FATHER):
        assert appmod._is_paid_member(who), who
        assert _invoice_unit(appmod, who) < _regular(appmod), who
        assert _invoice_unit(appmod, who) == _cart_unit(appmod, who), who
    monkeypatch.setenv("FAMILY_PLAN_ENABLED", "")
    assert _invoice_unit(appmod, FATHER) == _regular(appmod)


def test_someone_outside_the_household_pays_regular(appmod):
    _link(appmod, STEVE, MICHAEL)
    _member(appmod, OUTSIDER)
    _rep(appmod, MICHAEL, [SLUG])
    assert SLUG not in (appmod._resolve_repertoire_slugs(OUTSIDER) or set())
    assert _invoice_unit(appmod, OUTSIDER) == _regular(appmod)
    assert _cart_unit(appmod, OUTSIDER) == _regular(appmod)


def test_the_buyer_must_still_be_a_paid_member(appmod):
    _link(appmod, STEVE, MICHAEL)
    _member(appmod, MICHAEL)
    _rep(appmod, MICHAEL, [SLUG])
    assert appmod._resolve_repertoire_slugs(STEVE) is None
    assert _invoice_unit(appmod, STEVE) == _regular(appmod)
    assert _cart_unit(appmod, STEVE) == _regular(appmod)


def test_a_failed_household_lookup_keeps_the_buyers_own_repertoire(appmod, monkeypatch):
    _link(appmod, STEVE, MICHAEL)
    _member(appmod, STEVE)
    _rep(appmod, STEVE, [SLUG])
    _rep(appmod, MICHAEL, ["spike-shield"])

    def boom(*a, **k):
        raise RuntimeError("household table unreadable")
    monkeypatch.setattr(household, "household_emails", boom)
    with sqlite3.connect(appmod.LOG_DB) as cx:
        assert appmod._household_repertoire_slugs(cx, STEVE) == {SLUG}
    assert _invoice_unit(appmod, STEVE) < _regular(appmod)


def test_the_household_lookup_uses_its_own_connection_and_closes_it(appmod, monkeypatch):
    """Postgres aborts a transaction on a failed read; a leak drains the pool."""
    seen = []
    real = household.household_emails
    monkeypatch.setattr(household, "household_emails",
                        lambda cx, e: (seen.append(cx), real(cx, e))[1])
    _link(appmod, STEVE, MICHAEL)
    with sqlite3.connect(appmod.LOG_DB) as cx:
        appmod._household_repertoire_slugs(cx, STEVE)
        assert seen and all(c is not cx for c in seen)
    for c in seen:
        with pytest.raises(sqlite3.ProgrammingError):
            c.execute("SELECT 1")


def test_messy_email_still_reads_the_household(appmod):
    _link(appmod, STEVE, MICHAEL)
    _member(appmod, STEVE)
    _rep(appmod, MICHAEL, [SLUG])
    with sqlite3.connect(appmod.LOG_DB) as cx:
        assert SLUG in appmod._household_repertoire_slugs(cx, "  Steve@Example.com ")


# ── shipping does not follow the household ──────────────────────────────────
def test_a_household_link_alone_never_groups_shipping(appmod):
    """Glen: "We'll still count him in the household, except for shipping." Combined
    shipping groups by the person households (a people tag) and by address only."""
    from dashboard import combined_shipments as C
    from dashboard import orders as O
    cx = sqlite3.connect(appmod.LOG_DB)
    cx.row_factory = sqlite3.Row
    O.init_orders_table(cx)
    C.init_combined_shipments_table(cx)
    cx.execute("CREATE TABLE IF NOT EXISTS people (id INTEGER PRIMARY KEY, tags TEXT)")
    cx.execute("INSERT INTO people (id, tags) VALUES (1, '[]'), (2, '[]')")
    cx.commit()
    household.add_member(cx, STEVE, FATHER, relationship="parent")
    cx.commit()
    ids = []
    for ref, email, pid, street, zipc in (("S", STEVE, 1, "1 Steve St", "96720"),
                                          ("F", FATHER, 2, "9 Father Rd", "96740")):
        oid = O.upsert_order(cx, source="manual", external_ref=ref, name=ref, email=email,
                             items=[{"name": "M", "qty": 1, "slug": SLUG}], channel="retail",
                             address={"street": street, "city": "Hilo", "state": "HI",
                                      "zip": zipc, "country": "US"}, total_cents=5000)
        O.set_order_payment(cx, oid, method="Check", amount_cents=5000)
        cx.execute("UPDATE orders SET person_id=? WHERE id=?", (pid, oid))
        ids.append(oid)
    cx.commit()
    assert household.same_household(cx, STEVE, FATHER)
    assert [json.loads(r[0]) for r in cx.execute("SELECT tags FROM people ORDER BY id")] == [[], []]
    sug = C.suggest_combinable(cx, household_of=lambda pid: appmod._person_household_slug(cx, pid))
    assert not any(set(ids) <= {o["id"] for o in s["orders"]} for s in sug)
    cx.close()
