"""Durable operator test-account membership policy.

These identities are deliberate production fixtures.  Keep the paid and free
accounts opposite so Dr. Glen can compare both experiences without depending on
a subscription row that may expire, be cancelled, or be recreated.
"""

PERMANENT_MEMBER_EMAILS = frozenset({"drglenswartwout@gmail.com"})
PERMANENT_FREE_EMAILS = frozenset({"this.elf@gmail.com"})


def normalized(email):
    return (email or "").strip().lower()


def override_for(email):
    """Return ``True`` (member), ``False`` (free), or ``None`` (use DB)."""
    email = normalized(email)
    if email in PERMANENT_MEMBER_EMAILS:
        return True
    if email in PERMANENT_FREE_EMAILS:
        return False
    return None


def permanent_member_row(email):
    """Synthetic lifetime row matching the membership-reader contract."""
    if override_for(email) is not True:
        return None
    return {
        "id": "permanent:dr-glen",
        "email": normalized(email),
        "source": "operator_test_policy",
        "expires_at": None,
        "lifetime": True,
        "days_remaining": None,
    }
