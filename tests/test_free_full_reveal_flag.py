"""FREE_FULL_REVEAL_ENABLED: unblur the whole Remedy Match report for the free tier.

Glen, 2026-09-16: "Let's unblur the full Remedy Match report for the free tier."
Shipped behind a flag, default OFF, so the flip is a Doppler change and the revert
is too.

The three things that would make this change dangerous, each pinned here:

  1. It must not widen `paid`. `paid` means real membership and drives member
     pricing (`_member_price_cents`), so a free member picking it up would get
     member prices on every SKU.
  2. It must not bypass Glen's approval. `first_approved` still gates everything.
  3. With the flag off, behaviour must be byte-identical to before.
"""
import app as appmod


def _row(rid=7, approved=1, remedies=None):
    return {"id": rid, "first_approved": approved,
            "remedies": remedies if remedies is not None else [
                {"slug": "terrain-restore", "name": "Terrain Restore"},
                {"slug": "nous-energy", "name": "Nous Energy"},
                {"slug": "brain-boost", "name": "Brain Boost"},
            ]}


def _flags(monkeypatch, *, flag, member, approved=1):
    monkeypatch.setenv("FREE_FULL_REVEAL_ENABLED", "true" if flag else "")
    monkeypatch.setattr(appmod, "_active_membership_for_email",
                        lambda e: {"status": "active"} if member else None)
    return appmod._biofield_unlock_flags(_row(approved=approved), "pat@example.com")


def test_flag_off_leaves_a_free_member_blurred(monkeypatch):
    f = _flags(monkeypatch, flag=False, member=False)
    assert f["paid"] is False
    assert f["full_report"] is False


def test_flag_on_unblurs_a_free_member(monkeypatch):
    f = _flags(monkeypatch, flag=True, member=False)
    assert f["full_report"] is True


def test_flag_on_does_not_make_a_free_member_paid(monkeypatch):
    """The whole point. `paid` drives member pricing, so widening it would hand a
    free member member prices on every SKU."""
    f = _flags(monkeypatch, flag=True, member=False)
    assert f["paid"] is False, "the flag leaked into membership"
    assert f["full_report"] is True


def test_flag_on_cannot_publish_an_unapproved_reveal(monkeypatch):
    """Glen approves a reveal before anyone sees remedies. A visibility flag must
    not be a way around that."""
    f = _flags(monkeypatch, flag=True, member=False, approved=0)
    assert f["full_report"] is False, "the flag bypassed first_approved"


def test_a_paid_member_sees_everything_either_way(monkeypatch):
    for flag in (False, True):
        f = _flags(monkeypatch, flag=flag, member=True)
        assert f["paid"] is True
        assert f["full_report"] is True


def test_visible_slugs_follow_full_report_not_paid(monkeypatch):
    """`_biofield_visible_slugs` decides what a member may ORDER off the reveal.
    Under the flag a free member may order every matched remedy, which is the
    revenue reason for the change."""
    monkeypatch.setenv("FREE_FULL_REVEAL_ENABLED", "true")
    monkeypatch.setattr(appmod, "_active_membership_for_email", lambda e: None)
    slugs = appmod._biofield_visible_slugs(_row(), "pat@example.com")
    assert slugs == ["terrain-restore", "nous-energy", "brain-boost"]


def test_visible_slugs_with_the_flag_off_are_not_all(monkeypatch):
    """The control. A change that returns everything regardless of the flag is not
    a flagged change."""
    monkeypatch.setenv("FREE_FULL_REVEAL_ENABLED", "")
    monkeypatch.setattr(appmod, "_active_membership_for_email", lambda e: None)
    slugs = appmod._biofield_visible_slugs(_row(), "pat@example.com")
    assert slugs != ["terrain-restore", "nous-energy", "brain-boost"]


def test_the_flag_reads_the_environment_at_call_time(monkeypatch):
    """Read at call time, not at import, so flipping it in Doppler needs no
    redeploy. A module-level constant would make the flip a two-deploy change."""
    monkeypatch.setenv("FREE_FULL_REVEAL_ENABLED", "")
    assert appmod._free_full_reveal_enabled() is False
    monkeypatch.setenv("FREE_FULL_REVEAL_ENABLED", "on")
    assert appmod._free_full_reveal_enabled() is True
