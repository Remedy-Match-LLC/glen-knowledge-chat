from dashboard import member_access_policy as policy


def test_dr_glen_is_permanent_member_case_insensitively():
    assert policy.override_for("  DrGlenSwartwout@GMAIL.COM ") is True
    row = policy.permanent_member_row("drglenswartwout@gmail.com")
    assert row["lifetime"] is True
    assert row["expires_at"] is None
    assert row["source"] == "operator_test_policy"


def test_this_elf_is_explicitly_free_and_never_gets_member_row():
    assert policy.override_for("this.elf@gmail.com") is False
    assert policy.permanent_member_row("this.elf@gmail.com") is None


def test_other_accounts_defer_to_membership_database():
    assert policy.override_for("someone@example.com") is None


def test_app_membership_readers_enforce_both_test_accounts(monkeypatch):
    import app as appmod

    # Neither result may depend on whatever rows happen to exist in production.
    assert appmod._active_membership_for_email("drglenswartwout@gmail.com")["lifetime"] is True
    assert appmod.membership_category("drglenswartwout@gmail.com") == "full"
    assert appmod._is_paid_member("drglenswartwout@gmail.com") is True

    assert appmod._active_membership_for_email("this.elf@gmail.com") is None
    assert appmod.membership_category("this.elf@gmail.com") == "none"
    assert appmod._is_paid_member("this.elf@gmail.com") is False
