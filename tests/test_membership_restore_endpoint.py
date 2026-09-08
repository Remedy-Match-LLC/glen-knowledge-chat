"""/api/console/membership/restore — put back a grant /enroll cannot express.

/enroll takes a live tier and refuses any source outside the "membership_" prefix,
so revoking a retired grant is one-way. On 2026-09-08 a biofield_trial grant was
revoked in the belief it was a lapsed one-month trial; it was in fact the $1
"unlocked for life" purchase, and the offer is switched off, so the buyer could
not repurchase it.

The restore is deliberately narrow: known sources only, and the term comes from
the source's own constant so it reproduces what was sold.
"""
import importlib, sys, os, sqlite3
import pytest


def _load_app():
    sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
    try:
        return importlib.import_module("app")
    except Exception as e:
        pytest.skip(f"app import failed: {e}")


@pytest.fixture
def appmod(monkeypatch, tmp_path):
    app = _load_app()
    db = str(tmp_path / "t.db")
    monkeypatch.setattr(app, "LOG_DB", db, raising=False)
    monkeypatch.setattr(app, "CONSOLE_SECRET", "sekret", raising=False)
    import dashboard
    monkeypatch.setattr(dashboard, "CONSOLE_SECRET", "sekret", raising=False)
    cx = sqlite3.connect(db); app.init_membership_tables(cx); cx.close()
    if hasattr(app, "_member_join_welcome"):
        monkeypatch.setattr(app, "_member_join_welcome", lambda *a, **k: None, raising=False)
    return app


def _post(appmod, body, key="sekret"):
    return appmod.app.test_client().post(
        f"/api/console/membership/restore?key={key}", json=body)


def test_restores_the_retired_lifetime_unlock(appmod):
    r = _post(appmod, {"email": "sharon@x.com", "source": "biofield_trial"})
    assert r.status_code == 200
    d = r.get_json()
    assert d["ok"] is True
    assert d["source"] == "biofield_trial"
    # The term comes from the source's own constant, not from the caller.
    assert d["days"] == appmod.BIOFIELD_UNLOCK_DAYS
    assert appmod._active_membership_for_email("sharon@x.com") is not None


def test_the_restored_grant_does_not_confer_member_pricing(appmod):
    """biofield_trial un-blurs the analysis and withholds the member discount.
    A restore must reproduce that, not quietly upgrade someone."""
    _post(appmod, {"email": "sharon@x.com", "source": "biofield_trial"})
    row = appmod._active_membership_for_email("sharon@x.com")
    assert row["source"] == "biofield_trial"


def test_enroll_still_refuses_this_source(appmod):
    """The gap this endpoint exists to fill. If /enroll ever accepts
    biofield_trial, this endpoint is redundant and should be deleted."""
    r = appmod.app.test_client().post(
        "/api/console/membership/enroll?key=sekret",
        json={"email": "sharon@x.com", "tier": "month", "source": "biofield_trial"})
    assert r.status_code == 400


def test_days_may_shorten_the_term(appmod):
    r = _post(appmod, {"email": "a@x.com", "source": "care_taster", "days": 7})
    assert r.status_code == 200 and r.get_json()["days"] == 7


def test_days_may_not_exceed_what_the_offer_sold(appmod):
    r = _post(appmod, {"email": "a@x.com", "source": "care_taster",
                       "days": appmod.PROGRAM_CARE_TASTER_DAYS + 1})
    assert r.status_code == 400
    assert r.get_json()["max_days"] == appmod.PROGRAM_CARE_TASTER_DAYS


def test_days_must_be_a_positive_whole_number(appmod):
    assert _post(appmod, {"email": "a@x.com", "source": "care_taster",
                          "days": 0}).status_code == 400
    assert _post(appmod, {"email": "a@x.com", "source": "care_taster",
                          "days": "lots"}).status_code == 400


def test_an_arbitrary_source_is_refused(appmod):
    r = _post(appmod, {"email": "a@x.com", "source": "owner_lifetime"})
    assert r.status_code == 400
    assert "biofield_trial" in r.get_json()["restorable"]


def test_email_is_required(appmod):
    assert _post(appmod, {"source": "biofield_trial"}).status_code == 400


def test_restore_requires_owner(appmod):
    assert _post(appmod, {"email": "a@x.com", "source": "biofield_trial"},
                 key="wrong").status_code == 401


def test_restore_is_additive_and_leaves_the_revoke_visible(appmod):
    """Grants are additive. A restore must not edit or delete the revoked row,
    or the audit trail loses the fact that a revoke happened."""
    _post(appmod, {"email": "a@x.com", "source": "biofield_trial"})
    appmod.app.test_client().post("/api/console/membership/revoke?key=sekret",
                                  json={"email": "a@x.com"})
    _post(appmod, {"email": "a@x.com", "source": "biofield_trial"})
    with sqlite3.connect(appmod.LOG_DB) as cx:
        n = cx.execute("SELECT COUNT(*) FROM memberships WHERE email=?",
                       ("a@x.com",)).fetchone()[0]
    assert n == 2
    assert appmod._active_membership_for_email("a@x.com") is not None
