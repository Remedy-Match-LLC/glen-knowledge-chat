"""Tests for the one-shot admin trigger POST /api/console/repertoire-reseed.

Retroactively populates dashboard.repertoire for every CURRENTLY-PAID member
(active memberships row OR active kind='membership' subscription, gated
through the same _is_paid_member check the discount system uses) from their
purchase_history (365-day window). Console-gated only (require_console_key +
ok/fail — see /api/console/fmp-history-rebuild, tests/test_fmp_history_rebuild_route.py),
NOT feature-flag gated: this is a manual one-shot admin action, harmless
because the discount system already reads repertoire under REPERTOIRE_ENABLED.
"""
import sqlite3
from datetime import datetime, timedelta

import pytest


@pytest.fixture
def appmod(monkeypatch, tmp_path):
    import app as appmod
    import dashboard as _dashboard
    from dashboard import subscriptions as _subs

    monkeypatch.setattr(appmod, "LOG_DB", str(tmp_path / "chat_log.db"))
    monkeypatch.setattr(appmod, "CONSOLE_SECRET", "test-secret")
    monkeypatch.setattr(_dashboard, "CONSOLE_SECRET", "test-secret")

    with sqlite3.connect(appmod.LOG_DB) as cx:
        appmod.init_membership_tables(cx)
        _subs.init_subscriptions_table(cx)
        _subs.migrate_add_membership_columns(cx)
        cx.commit()

    appmod.app.config["TESTING"] = True
    return appmod


@pytest.fixture
def client(appmod):
    return appmod.app.test_client()


def _future_iso(days=30):
    return (datetime.utcnow() + timedelta(days=days)).isoformat() + "Z"


def _seed_active_membership(appmod, email, *, source="founding"):
    with sqlite3.connect(appmod.LOG_DB) as cx:
        cx.execute(
            "INSERT INTO memberships (id, email, granted_at, expires_at, granted_by, source) "
            "VALUES (?,?,?,?,?,?)",
            (f"mem_{email}", email, datetime.utcnow().isoformat() + "Z", _future_iso(),
             "test", source))
        cx.commit()


def _seed_purchase_history(appmod, email, slugs, *, days_ago=10):
    from dashboard import purchase_history as _ph
    purchased_at = (datetime.utcnow() - timedelta(days=days_ago)).isoformat() + "Z"
    with sqlite3.connect(appmod.LOG_DB) as cx:
        _ph.init_purchase_history_table(cx)
        for i, slug in enumerate(slugs):
            cx.execute(
                "INSERT OR IGNORE INTO purchase_history (email, slug, purchased_at, source, source_ref) "
                "VALUES (?,?,?,?,?)",
                (email, slug, purchased_at, "fmp", f"{email}-{i}"))
        cx.commit()


def _repertoire_slugs(appmod, email):
    from dashboard import repertoire as _rep
    with sqlite3.connect(appmod.LOG_DB) as cx:
        _rep.init_repertoire_table(cx)
        return _rep.repertoire_slugs(cx, email)


def test_reseed_requires_console_key(client):
    r = client.post("/api/console/repertoire-reseed")
    assert r.status_code == 401


def test_reseed_populates_repertoire_for_active_members_only(appmod, client):
    # Two ACTIVE paid members with purchase history in the 365-day window.
    _seed_active_membership(appmod, "alice@x.com")
    _seed_purchase_history(appmod, "alice@x.com", ["neuro-magnesium", "terrain-restore"])

    _seed_active_membership(appmod, "bob@x.com")
    _seed_purchase_history(appmod, "bob@x.com", ["terrain-restore"])

    # A NON-member with purchase history must NOT be reseeded.
    _seed_purchase_history(appmod, "carol@x.com", ["neuro-magnesium"])

    r = client.post("/api/console/repertoire-reseed", headers={"X-Console-Key": "test-secret"})
    assert r.status_code == 200
    body = r.get_json()
    assert body["ok"] is True
    data = body["data"]
    assert data["members_seen"] == 2
    assert data["members_reseeded"] == 2
    assert data["slugs_added"] == 3

    assert _repertoire_slugs(appmod, "alice@x.com") == {"neuro-magnesium", "terrain-restore"}
    assert _repertoire_slugs(appmod, "bob@x.com") == {"terrain-restore"}
    assert _repertoire_slugs(appmod, "carol@x.com") == set()


def test_reseed_is_idempotent_on_rerun(appmod, client):
    _seed_active_membership(appmod, "dana@x.com")
    _seed_purchase_history(appmod, "dana@x.com", ["neuro-magnesium"])

    r1 = client.post("/api/console/repertoire-reseed", headers={"X-Console-Key": "test-secret"})
    assert r1.get_json()["data"]["slugs_added"] == 1

    r2 = client.post("/api/console/repertoire-reseed", headers={"X-Console-Key": "test-secret"})
    body2 = r2.get_json()["data"]
    assert body2["members_seen"] == 1
    assert body2["slugs_added"] == 0
    assert body2["members_reseeded"] == 0


def test_reseed_candidate_from_subscription_and_grant_is_not_double_counted(appmod, client):
    """A member who shows up in BOTH candidate sources (an active kind='membership'
    subscription row AND an active memberships grant row for the same email) must
    be counted once, not twice, by the DISTINCT union. (_is_paid_member itself is
    gated on the memberships-grant table — see reseed-report.md "concerns" — so a
    subscription-only member with no grant row would not pass the paid-member
    filter; this fixture gives 'erin' both so the union's dedup is exercised
    without depending on that separate gate quirk.)"""
    from dashboard import subscriptions as _subs
    with sqlite3.connect(appmod.LOG_DB) as cx:
        cx.execute(
            "INSERT INTO subscriptions (email, stripe_customer_id, stripe_payment_method_id, "
            "kind, status, cadence_months, order_count, next_charge_date, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            ("erin@x.com", "cus_1", "pm_1", "membership", "active", 1, 1,
             _future_iso(), _subs._now_iso(), _subs._now_iso()))
        cx.commit()
    _seed_active_membership(appmod, "erin@x.com")
    _seed_purchase_history(appmod, "erin@x.com", ["terrain-restore"])

    r = client.post("/api/console/repertoire-reseed", headers={"X-Console-Key": "test-secret"})
    body = r.get_json()["data"]
    assert body["members_seen"] == 1
    assert body["members_reseeded"] == 1
    assert body["slugs_added"] == 1
    assert _repertoire_slugs(appmod, "erin@x.com") == {"terrain-restore"}


def _seed_order(appmod, email, slugs, *, days_ago=10, status="paid", ref=None):
    """A modern purchase: orders table only, nothing in purchase_history --
    which is what every portal, funnel and current-storefront sale looks like."""
    import json
    from dashboard.orders import init_orders_table
    created = (datetime.utcnow() - timedelta(days=days_ago)).isoformat() + "Z"
    with sqlite3.connect(appmod.LOG_DB) as cx:
        init_orders_table(cx)
        cx.execute(
            "INSERT INTO orders (created_at, source, external_ref, channel, email, "
            "items_json, total_cents, status) VALUES (?,?,?,?,?,?,?,?)",
            (created, "portal-reorder", ref or f"o-{email}-{days_ago}-{slugs[0]}",
             "retail", email, json.dumps([{"slug": s, "qty": 1} for s in slugs]),
             1000, status))
        cx.commit()


def test_a_member_whose_purchases_are_only_in_orders_is_reseeded(client, appmod):
    """THE DEFECT. purchase_history's only writers are the fmp and groovekart
    backfills, so nothing bought through the portal, the funnel or the current
    storefront is ever in it. This route read purchase_history and `continue`d
    when it was empty, so for any member whose buying began after the migration
    it seeded NOTHING -- every day, via the daily cron, reporting success.
    Repertoire drives member reorder pricing, so those members were being
    under-credited on every order."""
    from dashboard import repertoire as _rep
    email = "modernmember@example.com"
    _seed_active_membership(appmod, email)
    _seed_order(appmod, email, ["terrain-restore", "nous-energy"])

    r = client.post("/api/console/repertoire-reseed",
                    headers={"X-Console-Key": "test-secret"})
    assert r.status_code == 200
    data = r.get_json()["data"]
    assert data["members_seen"] == 1
    assert data["members_reseeded"] == 1, "the modern member was skipped again"
    assert data["slugs_added"] == 2

    with sqlite3.connect(appmod.LOG_DB) as cx:
        assert _rep.repertoire_slugs(cx, email) == {"terrain-restore", "nous-energy"}


def test_both_sources_are_unioned_not_one_or_the_other(client, appmod):
    """A member who bought before the migration AND since gets both. Guards
    against 'fixing' this by swapping purchase_history out for orders, which
    would silently drop every legacy purchase from member pricing."""
    from dashboard import repertoire as _rep
    email = "bothsources@example.com"
    _seed_active_membership(appmod, email)
    _seed_purchase_history(appmod, email, ["wholomega"])
    _seed_order(appmod, email, ["terrain-restore"])

    client.post("/api/console/repertoire-reseed", headers={"X-Console-Key": "test-secret"})
    with sqlite3.connect(appmod.LOG_DB) as cx:
        assert _rep.repertoire_slugs(cx, email) == {"wholomega", "terrain-restore"}


def test_a_cancelled_order_does_not_earn_repertoire_credit(client, appmod):
    """Repertoire grants a price discount. A cancelled order is not a purchase."""
    from dashboard import repertoire as _rep
    email = "cancelledorder@example.com"
    _seed_active_membership(appmod, email)
    _seed_order(appmod, email, ["terrain-restore"], status="cancelled")

    client.post("/api/console/repertoire-reseed", headers={"X-Console-Key": "test-secret"})
    with sqlite3.connect(appmod.LOG_DB) as cx:
        assert _rep.repertoire_slugs(cx, email) == set()


def test_a_non_member_is_never_reseeded_from_orders(client, appmod):
    """The orders half must respect the same paid-member gate as the history
    half; repertoire is member pricing, not a purchase log."""
    from dashboard import repertoire as _rep
    email = "notamember@example.com"
    _seed_order(appmod, email, ["terrain-restore"])

    r = client.post("/api/console/repertoire-reseed", headers={"X-Console-Key": "test-secret"})
    assert r.get_json()["data"]["members_seen"] == 0
    with sqlite3.connect(appmod.LOG_DB) as cx:
        assert _rep.repertoire_slugs(cx, email) == set()


# ---------------------------------------------------------------------------
# ?dry_run=1 -- added because I sized this route's impact from the wrong table
# ---------------------------------------------------------------------------
# I read /api/console/members (2 members, both trial), concluded the blast
# radius was zero, and said so. The real run moved 11 members and 162 SKUs: the
# board reads `subscriptions` alone, the route's candidates are
# `subscriptions UNION memberships`. Repertoire drives member reorder pricing,
# so the question "how many does this move" must be answerable WITHOUT writing.

def _post(client, qs=""):
    return client.post("/api/console/repertoire-reseed" + qs,
                       headers={"X-Console-Key": "test-secret"}).get_json()


def test_dry_run_writes_nothing(client, appmod):
    from dashboard import repertoire as _rep
    email = "dryrun@example.com"
    _seed_active_membership(appmod, email)
    _seed_order(appmod, email, ["terrain-restore", "nous-energy"])

    d = _post(client, "?dry_run=1")["data"]
    assert d["dry_run"] is True
    assert d["slugs_added"] == 2, "the preview must still COUNT what it would do"
    with sqlite3.connect(appmod.LOG_DB) as cx:
        assert _rep.repertoire_slugs(cx, email) == set(), "dry run wrote to repertoire"


def test_the_preview_agrees_with_the_write_it_previews(client, appmod):
    """The whole point. A preview that disagrees with the real run is worse than
    no preview, because it gets believed -- which is exactly how the wrong number
    reached Glen. Both paths select through repertoire.would_add, so this pins
    that they cannot drift."""
    email = "agree@example.com"
    _seed_active_membership(appmod, email)
    _seed_purchase_history(appmod, email, ["wholomega"])
    _seed_order(appmod, email, ["terrain-restore", "nous-energy"])

    preview = _post(client, "?dry_run=1")["data"]
    real = _post(client)["data"]
    for k in ("members_seen", "members_reseeded", "slugs_added"):
        assert preview[k] == real[k], f"{k}: preview {preview[k]} != real {real[k]}"


def test_the_preview_names_the_slugs_not_just_a_count(client, appmod):
    """A bare number is what let me be confidently wrong. The breakdown makes the
    claim checkable before it is believed."""
    email = "detail@example.com"
    _seed_active_membership(appmod, email)
    _seed_order(appmod, email, ["terrain-restore"])

    d = _post(client, "?dry_run=1")["data"]
    rows = {r["email"]: r for r in d["detail"]}
    assert rows[email]["would_add"] == ["terrain-restore"]


def test_a_second_preview_after_a_real_run_reports_nothing_left(client, appmod):
    email = "settled@example.com"
    _seed_active_membership(appmod, email)
    _seed_order(appmod, email, ["terrain-restore"])

    _post(client)
    d = _post(client, "?dry_run=1")["data"]
    assert d["members_reseeded"] == 0 and d["slugs_added"] == 0
    assert d["members_seen"] == 1, "it must still SEE the member, just have nothing to add"


def test_a_real_run_reports_dry_run_false_and_no_preview_detail(client, appmod):
    email = "notdry@example.com"
    _seed_active_membership(appmod, email)
    _seed_order(appmod, email, ["terrain-restore"])
    d = _post(client)["data"]
    assert d["dry_run"] is False and d["detail"] == []


def test_a_retired_slug_is_previewed_and_stored_as_its_live_twin(client, appmod):
    """A dead slug in repertoire silently costs a member their reorder discount:
    pricing tests `slug in repertoire_slugs` against the RESOLVED cart slug, so an
    unresolved row never matches. The preview must resolve exactly as the write
    does -- otherwise the number Glen reads describes SKUs that will never price.
    Left untested, removing the resolve from would_add kept every test green."""
    from dashboard import repertoire as _rep
    # Resolve through the SAME function the code path uses. My first version
    # computed `live` with app._superseded, which passes app's IN-MEMORY catalog,
    # while would_add goes through repertoire._default_resolve -> products.
    # superseded_slug() on the module default. Locally the two agree; in the full
    # CI suite another test had mutated the in-memory catalog and they diverged,
    # so this passed alone and failed in CI. Expectation and code must read one
    # source. The guard also has to compare -- superseded_slug returns the slug
    # UNCHANGED when it is live or unknown, never None, so the old
    # `if _superseded(s)` was truthy for every candidate and never skipped.
    resolve = _rep._default_resolve
    retired = next((s for s in ("relax", "dental-regen-powder",
                                "connective-tissue-support")
                    if (resolve(s) or s) != s), None)
    if not retired:
        pytest.skip("no retired slug with a superseded_by pointer in the catalog")
    live = resolve(retired)

    email = "retiredslug@example.com"
    _seed_active_membership(appmod, email)
    _seed_order(appmod, email, [retired])

    d = _post(client, "?dry_run=1")["data"]
    rows = {r["email"]: r for r in d["detail"]}
    assert rows[email]["would_add"] == [live], (
        f"preview offered {rows[email]['would_add']} not the live twin {live!r}")

    _post(client)
    with sqlite3.connect(appmod.LOG_DB) as cx:
        assert _rep.repertoire_slugs(cx, email) == {live}
