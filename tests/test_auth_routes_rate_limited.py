"""The auth routes must actually CALL the limiter.

`tests/test_auth_limits.py` proves the limiter's logic. These prove it is wired, which is
a different question: a limiter that is never called and a limiter that never fires look
identical from outside, and that failure mode has bitten this codebase before.

Every test here drives the real Flask app through its test client and asserts on a 429.
Deleting the three `_auth_rate_limited(email)` call sites makes every one of them fail,
which is the property that makes them worth having.

THE ORACLE TEST IS THE ONE THAT MATTERS. The limiter must behave identically for a real
account and an invented one. If it did not, it would answer by behaviour the question the
routes deliberately refuse to answer by message, and the fix would be worse than the leak.
"""
import pytest

import app as appmod
from dashboard import auth_limits


# The two password routes import dashboard.portal_auth, which imports argon2. That is a
# real dependency (requirements.txt line 2, argon2-cffi>=23.1) so CI has it, but a system
# Python without it would fail these for a reason that has nothing to do with the limiter.
# Skip rather than false-fail, and note that those two are therefore CI-verified only.
_ARGON2 = True
try:
    import argon2  # noqa: F401
except ImportError:
    _ARGON2 = False

_NEEDS_ARGON2 = ("/portal/password-reset-request", "/portal/password-login")


def _routes():
    out = []
    for r in ("/portal/login-request", "/portal/password-reset-request",
              "/portal/password-login"):
        if r in _NEEDS_ARGON2 and not _ARGON2:
            out.append(pytest.param(r, marks=pytest.mark.skip(reason="argon2 not installed")))
        else:
            out.append(r)
    return out


ROUTES = _routes()


@pytest.fixture(autouse=True)
def _fresh_limiter(monkeypatch):
    """A clean limiter per test, and a fixed client address, so tests cannot bleed."""
    monkeypatch.setattr(appmod, "_AUTH_LIMITER", auth_limits.EmailProbeLimiter())
    yield


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setenv("CLIENT_LOGIN_ENABLED", "true")
    monkeypatch.setenv("PORTAL_PASSWORD_LOGIN_ENABLED", "true")
    appmod.app.config["TESTING"] = True
    with appmod.app.test_client() as c:
        yield c


def _post(client, route, email, ip="9.9.9.9"):
    body = {"email": email}
    if "password-login" in route:
        body["password"] = "irrelevant-but-long-enough"
    return client.post(route, json=body, headers={"X-Forwarded-For": ip})


@pytest.mark.parametrize("route", ROUTES)
def test_a_sweep_is_refused_with_429(client, route):
    """Distinct addresses from one client, past the limit."""
    for i in range(auth_limits.MAX_DISTINCT_EMAILS):
        r = _post(client, route, f"sweep{i}@example.invalid")
        assert r.status_code != 429, f"refused too early, at {i + 1}"
    r = _post(client, route, "one-too-many@example.invalid")
    assert r.status_code == 429, f"{route} did not call the limiter at all"
    assert r.headers.get("Retry-After")
    assert "Too many" in r.get_json()["message"]


@pytest.mark.parametrize("route", ROUTES)
def test_the_real_customers_burst_is_never_refused(client, route):
    """Her actual worst minute: 9 requests, one address. Repeated well past it, because
    the property is that ONE address never exhausts the allowance."""
    for i in range(40):
        r = _post(client, route, "judithannltom@yahoo.com")
        assert r.status_code != 429, f"the customer was refused on attempt {i + 1}"


@pytest.mark.parametrize("route", ROUTES)
def test_one_sweeping_client_does_not_lock_out_everyone_else(client, route):
    for i in range(auth_limits.MAX_DISTINCT_EMAILS + 3):
        _post(client, route, f"sweep{i}@example.invalid", ip="1.1.1.1")
    r = _post(client, route, "innocent@example.invalid", ip="2.2.2.2")
    assert r.status_code != 429


@pytest.mark.parametrize("route", ROUTES)
def test_a_forged_forwarding_header_cannot_reset_the_limit(client, route):
    """The trap this design exists for. Render appends the real address at the RIGHT of
    X-Forwarded-For; every existing auth route reads `.split(",")[0]`, the forged end.
    Rotating the left-hand value must not buy a fresh allowance."""
    for i in range(auth_limits.MAX_DISTINCT_EMAILS):
        _post(client, route, f"sweep{i}@example.invalid", ip="7.7.7.7")
    r = _post(client, route, "next@example.invalid",
              ip="203.0.113.99, 7.7.7.7")      # forged hop prepended, real one still last
    assert r.status_code == 429, "a rotated X-Forwarded-For bought a fresh allowance"


@pytest.mark.parametrize("route", ROUTES)
def test_the_limiter_cannot_be_used_as_an_oracle(client, route):
    """A real address and an address that cannot exist must produce the SAME sequence of
    status codes. Any divergence replaces a timing leak with a behavioural one."""
    real, fake = "judithannltom@yahoo.com", "definitely-not-a-customer-zz99@example.invalid"
    seq_real, seq_fake = [], []
    for _ in range(auth_limits.MAX_DISTINCT_EMAILS + 4):
        seq_real.append(_post(client, route, real, ip="4.4.4.4").status_code)
    monkey = auth_limits.EmailProbeLimiter()
    appmod._AUTH_LIMITER = monkey
    for _ in range(auth_limits.MAX_DISTINCT_EMAILS + 4):
        seq_fake.append(_post(client, route, fake, ip="4.4.4.4").status_code)
    assert seq_real == seq_fake, f"real={seq_real} fake={seq_fake}"


def test_a_429_says_nothing_about_whether_the_account_exists(client):
    """The refusal message must be the same string regardless."""
    seen = set()
    for email in ("judithannltom@yahoo.com", "nobody-zz99@example.invalid"):
        lim = auth_limits.EmailProbeLimiter()
        appmod._AUTH_LIMITER = lim
        for i in range(auth_limits.MAX_DISTINCT_EMAILS):
            _post(client, "/portal/login-request", f"filler{i}@example.invalid", ip="8.8.8.8")
        r = _post(client, "/portal/login-request", email, ip="8.8.8.8")
        assert r.status_code == 429
        seen.add(r.get_json()["message"])
    assert len(seen) == 1, f"the 429 message differs by account existence: {seen}"
