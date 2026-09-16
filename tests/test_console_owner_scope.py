"""A console sign-in link must record the person who used it.

Rae could not open her console after CONSOLE_SECRET was rotated on 2026-09-16, because the
console compares a cookie against the current secret and hers held the old one. She has an
owner-role token of her own (scope workspace:rae) that a rotation never touches, but it had
never been used: last_used_at was NULL. She had been typing the shared master key instead.

Glen's fix, 2026-09-16: put her on the console owner allowlist so she signs in by one-time
link from her own machine, with no credential to type or carry.

That exposed a second problem. /console/login/verify minted every session with a hardcoded
scope of workspace:glen and the display name "Console Owner", so Rae signing in by link
would have been recorded AS GLEN. Attribution that names the wrong person is worse than
none: her actions in the console would have shown as his.

These tests pin the scope to the email that asked for the link.
"""
import importlib

import pytest


@pytest.fixture(scope="module")
def appmod():
    return importlib.import_module("app")


def test_glens_addresses_still_sign_in_as_glen(appmod):
    for email in ("drglenswartwout@gmail.com", "this.elf@gmail.com"):
        scope, display = appmod._console_owner_scope(email)
        assert scope == "workspace:glen", f"{email} must stay Glen's workspace"
        assert display == "Glen"


def test_rae_signs_in_as_rae_not_as_glen(appmod):
    scope, display = appmod._console_owner_scope("suerae1111@gmail.com")
    assert scope == "workspace:rae", "Rae's link session must be recorded as Rae"
    assert display == "Rae"


def test_rae_keeps_the_owner_role(appmod):
    """Correct attribution must not cost her any access. rae is an owner in SCOPE_ROLES."""
    from dashboard import rbac
    scope, _ = appmod._console_owner_scope("suerae1111@gmail.com")
    assert rbac.actor_for_scope(scope).role == rbac.OWNER


def test_the_address_is_matched_case_and_space_insensitively(appmod):
    scope, _ = appmod._console_owner_scope("  SueRae1111@Gmail.com  ")
    assert scope == "workspace:rae"


def test_an_unmapped_address_does_not_silently_become_an_owner(appmod):
    """CONSOLE_OWNER_EMAILS is an allowlist, not a grant of Glen's identity.

    Before this change ANY address on that allowlist minted a workspace:glen session. An
    address with no explicit scope now gets its own, which rbac resolves to VA.
    """
    from dashboard import rbac
    scope, display = appmod._console_owner_scope("newhire@example.com")
    assert scope == "workspace:newhire"
    assert display == "Newhire"
    assert rbac.actor_for_scope(scope).role == rbac.VA, (
        "an unmapped allowlisted address must not inherit owner"
    )


def test_a_hostile_local_part_cannot_forge_a_scope(appmod):
    """The scope string is built from the local part, so it must be sanitised."""
    scope, _ = appmod._console_owner_scope("glen:admin@example.com")
    assert scope == "workspace:glenadmin", "punctuation must not survive into the scope"
    assert scope != "workspace:glen"


def test_an_empty_address_falls_back_to_a_scoped_non_owner(appmod):
    from dashboard import rbac
    scope, _ = appmod._console_owner_scope("")
    assert scope == "workspace:scoped"
    assert rbac.actor_for_scope(scope).role == rbac.VA


def test_rae_is_on_the_allowlist_fallback_or_the_env_var(appmod, monkeypatch):
    """Setting CONSOLE_OWNER_EMAILS REPLACES the built-in pair, it does not add to it.

    So the deployed value must carry Glen's two addresses as well, or setting it to add Rae
    would lock Glen out of his own console. This pins that trap in a test rather than in a
    deploy note nobody reads.
    """
    monkeypatch.setenv("CONSOLE_OWNER_EMAILS", "suerae1111@gmail.com")
    importlib.reload(appmod) if False else None
    owners = appmod._console_owner_emails()
    assert owners == {"suerae1111@gmail.com"}, (
        "CONSOLE_OWNER_EMAILS replaces the default pair; the deployed value must list "
        "every owner address, Glen's included"
    )
