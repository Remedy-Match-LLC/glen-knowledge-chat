"""Family plan as a membership category, and a trial that must not outrank it.

Both found 2026-09-12. Four of eight family-plan people were invisible on the
members board while every one of them priced as a member, and a lifetime trial row
was permanently cancelling genuine family-plan coverage.
"""
import pytest


@pytest.fixture
def app(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("PINECONE_API_KEY", "test-key")
    import app as appmod
    return appmod


class _FakeCx:
    """The board opens a real connection before any of the seams under test. In the
    full suite that connection is not usable, so these two tests stub it: they are
    about what the endpoint BUILDS, not about the database."""
    row_factory = None

    def execute(self, *a, **k):
        class _R:
            @staticmethod
            def fetchall():
                return []

            @staticmethod
            def fetchone():
                return None
        return _R()

    def commit(self):
        return None

    def close(self):
        return None

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _stub_board_db(app, monkeypatch):
    from dashboard import subscriptions as _subs
    monkeypatch.setattr(app.db, "connect", lambda *a, **k: _FakeCx())
    for fn in ("migrate_add_failed_count", "migrate_add_membership_columns",
               "migrate_add_term_cap_column", "migrate_add_attribution_column",
               "migrate_add_consent_column"):
        if hasattr(_subs, fn):
            monkeypatch.setattr(_subs, fn, lambda cx: None)
    monkeypatch.setattr(_subs, "list_active_memberships", lambda cx: [])
    monkeypatch.setattr(_subs, "list_membership_holders", lambda cx: [])
    monkeypatch.setattr(app, "CONSOLE_SECRET", "test-secret")
    monkeypatch.setattr(app.dashboard, "CONSOLE_SECRET", "test-secret")
    monkeypatch.setattr(app, "_member_name_for", lambda cx, e: "")

def test_a_trial_row_does_not_cancel_family_plan_coverage(app, monkeypatch):
    """J.C. Davis: a `biofield_trial` grant expiring in 2126 made every check read
    'trial' forever, so _is_paid_member returned False before the family plan was
    ever consulted, and she paid full price while covered by an active plan."""
    monkeypatch.setattr(app, "_active_membership_for_email", lambda e: {"source": "biofield_trial"})
    monkeypatch.setattr(app, "membership_category", lambda e: "trial")
    monkeypatch.setattr(app, "_family_plan_enabled", lambda: True)
    from dashboard import family_plan as _fp
    monkeypatch.setattr(_fp, "covers", lambda cx, e: True)
    assert app._is_paid_member("covered@example.com") is True


def test_a_trial_with_no_family_plan_is_still_not_a_member(app, monkeypatch):
    """The negative case. Falling through must not turn every trial into a member."""
    monkeypatch.setattr(app, "_active_membership_for_email", lambda e: {"source": "biofield_trial"})
    monkeypatch.setattr(app, "membership_category", lambda e: "trial")
    monkeypatch.setattr(app, "_family_plan_enabled", lambda: True)
    from dashboard import family_plan as _fp
    monkeypatch.setattr(_fp, "covers", lambda cx, e: False)
    assert app._is_paid_member("trialonly@example.com") is False


def test_a_real_membership_still_short_circuits(app, monkeypatch):
    """A non-trial membership must not start consulting the family plan."""
    monkeypatch.setattr(app, "_active_membership_for_email", lambda e: {"source": "membership_x"})
    monkeypatch.setattr(app, "membership_category", lambda e: "full")
    def _boom():
        raise AssertionError("family plan must not be consulted for a full member")
    monkeypatch.setattr(app, "_family_plan_enabled", _boom)
    assert app._is_paid_member("full@example.com") is True


def test_board_lists_family_plan_holders_and_covered_members(app, monkeypatch):
    """The board's job is to answer 'who gets member pricing'. Before this it could
    not see family plans at all."""
    _stub_board_db(app, monkeypatch)
    from dashboard import family_plan as _fp, household as _hh
    monkeypatch.setattr(_fp, "init_family_plan_table", lambda cx: None)
    monkeypatch.setattr(_hh, "init_household_tables", lambda cx: None)
    monkeypatch.setattr(_fp, "list_active", lambda cx: [{
        "caregiver_email": "karin@example.com", "status": "active", "source": "comp",
        "amount_cents": 14700, "cadence_months": 1, "started_at": "2026-07-09",
        "next_charge_at": None, "fail_count": 0}])
    monkeypatch.setattr(_hh, "members_for", lambda cx, e: [
        {"email": "spouse@example.com", "relationship": "spouse", "share_consent": 1}])

    d = app.app.test_client().get("/api/console/members",
                                  headers={"X-Console-Key": "test-secret"}).get_json()
    fam = {r["email"]: r for r in d["buckets"]["family"]}
    assert set(fam) == {"karin@example.com", "spouse@example.com"}
    assert fam["karin@example.com"]["role"] == "holder"
    assert fam["spouse@example.com"]["role"] == "covered"
    assert fam["spouse@example.com"]["plan_holder"] == "karin@example.com"
    assert fam["karin@example.com"]["plan_status"] == "active"
    assert d["counts"]["family"] == 2


def test_family_plan_read_failure_degrades_the_board_instead_of_500ing(app, monkeypatch):
    _stub_board_db(app, monkeypatch)
    from dashboard import family_plan as _fp
    def _boom(cx):
        raise RuntimeError("table missing")
    monkeypatch.setattr(_fp, "list_active", _boom)
    r = app.app.test_client().get("/api/console/members", headers={"X-Console-Key": "test-secret"})
    assert r.status_code == 200
    assert r.get_json()["buckets"]["family"] == []
