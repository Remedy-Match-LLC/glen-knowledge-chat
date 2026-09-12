"""Rate limiting on the public auth routes.

Four things these pin, each of which was a measurement rather than a preference.

1. THE CUSTOMER MUST GET THROUGH. On 2026-09-10 judithannltom@yahoo.com sent 9 requests
   in a single minute and 14 across the day, all for HER OWN address, and succeeded three
   minutes later. A flat per-IP request count under 10 would have locked her out. Her
   timeline is replayed below and must never be refused.

2. A SWEEP MUST NOT. The asymmetry that makes this solvable: she hammered ONE address, a
   sweep hits MANY. The limiter counts distinct addresses, not requests.

3. THE LIMITER MUST NOT BECOME THE ORACLE. It must behave identically for a real account
   and an invented one, or it replaces a timing leak with a behavioural one. It never
   touches the database, so the tests below use addresses that exist and addresses that
   cannot, and require the same answer.

4. THE KEY MUST NOT BE ATTACKER-CONTROLLED. Measured on production 2026-09-11: a request
   carrying `X-Forwarded-For: 203.0.113.77` was recorded with ip_hash = sha256 of that
   value, and a request sending no header was recorded as the real client IP. Render
   appends exactly one element. Every existing auth route reads `.split(",")[0]`, which
   is the forged end.

Run:  python3 -m pytest tests/test_auth_limits.py -q
"""
import hashlib

import pytest

from dashboard.auth_limits import (
    EmailProbeLimiter, MAX_DISTINCT_EMAILS, MAX_REQUESTS, WINDOW_SECONDS,
)


class Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t

    def advance(self, secs):
        self.t += secs


# The key is NOT this file's business any more. The first attempt carried its own address
# helper, read X-Forwarded-For from the wrong end, and did not bind. That job now belongs to
# dashboard/client_address, which was verified against production, and is tested in
# tests/test_client_address.py. A second copy here is how two definitions drift apart.


# ── the customer, who is the acceptance test ──────────────────────────────────
JUDY = "judithannltom@yahoo.com"


def test_the_real_customers_worst_minute_is_never_refused():
    """Her actual 2026-09-10 07:52 burst: 9 requests inside one minute, one address."""
    clock = Clock()
    lim = EmailProbeLimiter(clock)
    for i in range(9):
        allowed, retry, reason = lim.note("66.8.150.120", JUDY)
        assert allowed, f"request {i + 1} of her burst was refused ({reason})"
        clock.advance(1.5)


def test_her_whole_day_is_never_refused():
    """14 requests across 19 hours, all one address. Also covers the day-long shape a
    window shorter than hers would break."""
    clock = Clock()
    lim = EmailProbeLimiter(clock)
    for i in range(14):
        allowed, _, reason = lim.note("66.8.150.120", JUDY)
        assert allowed, f"request {i + 1} of her day was refused ({reason})"
        clock.advance(60 * 90)


def test_she_is_not_refused_even_at_a_hundred_attempts():
    """The property that matters is that ONE address never exhausts the allowance, so
    there is no number of retries on her own account that locks her out."""
    lim = EmailProbeLimiter(Clock())
    for i in range(100):
        allowed, _, _ = lim.note("66.8.150.120", JUDY)
        assert allowed, f"refused on attempt {i + 1} against her own address"


# ── the sweep ─────────────────────────────────────────────────────────────────
def test_a_sweep_is_refused_at_the_limit():
    lim = EmailProbeLimiter(Clock())
    for i in range(MAX_DISTINCT_EMAILS):
        allowed, _, _ = lim.note("1.2.3.4", f"target{i}@example.com")
        assert allowed
    allowed, retry, reason = lim.note("1.2.3.4", "one-too-many@example.com")
    assert not allowed
    assert reason == "distinct_emails"
    assert 0 < retry <= WINDOW_SECONDS


def test_a_refused_sweep_does_not_block_a_different_client():
    lim = EmailProbeLimiter(Clock())
    for i in range(MAX_DISTINCT_EMAILS + 5):
        lim.note("1.2.3.4", f"target{i}@example.com")
    allowed, _, _ = lim.note("5.6.7.8", "someone@example.com")
    assert allowed, "one sweeping client must not lock out everybody else"


def test_the_window_expires():
    clock = Clock()
    lim = EmailProbeLimiter(clock)
    for i in range(MAX_DISTINCT_EMAILS):
        lim.note("1.2.3.4", f"target{i}@example.com")
    assert not lim.note("1.2.3.4", "next@example.com")[0]
    clock.advance(WINDOW_SECONDS + 1)
    assert lim.note("1.2.3.4", "next@example.com")[0], "the window must not be permanent"


def test_a_machine_gun_on_one_address_still_hits_the_hard_ceiling():
    """The customer's worst minute was 9. This ceiling is 120 an hour, so it protects the
    CPU without ever reaching a human."""
    lim = EmailProbeLimiter(Clock())
    allowed_count = 0
    for _ in range(MAX_REQUESTS + 10):
        if lim.note("1.2.3.4", JUDY)[0]:
            allowed_count += 1
    assert allowed_count == MAX_REQUESTS
    assert lim.note("1.2.3.4", JUDY)[2] == "requests"


def test_the_hard_ceiling_is_far_above_the_real_customer():
    """If this ever stops being true, the ceiling has walked toward the customer."""
    assert MAX_REQUESTS >= 14 * 4, "must keep a wide margin over her real 14-request day"
    assert MAX_DISTINCT_EMAILS >= 5, "a household or Rae helping clients must fit"


# ── the limiter must not become the oracle ────────────────────────────────────
def test_a_real_address_and_an_invented_one_are_treated_identically():
    """The whole point. If these diverged, the limiter would answer the question the
    routes refuse to answer."""
    real, fake = JUDY, "definitely-not-a-customer-zz99@example.invalid"
    a = EmailProbeLimiter(Clock())
    b = EmailProbeLimiter(Clock())
    for _ in range(MAX_REQUESTS + 5):
        ra = a.note("1.2.3.4", real)
        rb = b.note("1.2.3.4", fake)
        assert ra == rb, f"divergence: real={ra} fake={rb}"


def test_the_limiter_never_touches_a_database():
    """It takes no connection and has no import that could reach one. A limiter that
    looked an address up would leak by timing even if its answer were identical."""
    import inspect
    import dashboard.auth_limits as m
    src = inspect.getsource(m)
    for forbidden in ("db.connect", "psycopg", "sqlite3", "SELECT ", "cx."):
        assert forbidden not in src, f"{forbidden!r} would make the limiter a lookup"


def test_addresses_are_not_held_in_memory_in_the_clear():
    lim = EmailProbeLimiter(Clock())
    lim.note("1.2.3.4", JUDY)
    stored = repr(lim._seen)
    assert JUDY not in stored
    assert hashlib.sha256(JUDY.encode()).hexdigest() in stored


def test_case_and_whitespace_do_not_multiply_the_allowance():
    """Otherwise a sweep gets MAX_DISTINCT_EMAILS times as many tries per address."""
    lim = EmailProbeLimiter(Clock())
    for variant in (JUDY, JUDY.upper(), f"  {JUDY}  ", JUDY.title()):
        assert lim.note("1.2.3.4", variant)[0]
    assert len({h for (_, h) in lim._seen["1.2.3.4"]}) == 1


def test_an_already_counted_address_stays_free_after_the_limit_is_reached():
    """Found by mutation testing, not by design: dropping the `eh not in distinct` check
    passed every other test in this file.

    The gap it leaves is real and it is the customer case again, on a SHARED address. An
    office or household IP can legitimately reach the distinct limit, and when it does,
    one of those same people retrying their OWN address must still get through. Without
    this, the limit becomes a hard wall for people it already knows.
    """
    lim = EmailProbeLimiter(Clock())
    seen = [f"member{i}@example.com" for i in range(MAX_DISTINCT_EMAILS)]
    for e in seen:
        assert lim.note("1.2.3.4", e)[0]
    assert not lim.note("1.2.3.4", "a-new-one@example.com")[0]   # the wall still stands
    for e in seen:
        allowed, _, reason = lim.note("1.2.3.4", e)
        assert allowed, f"{e} was already counted and must remain free ({reason})"


def test_the_customer_gets_through_even_from_a_saturated_shared_address():
    """The same property stated as the case that matters: she retries her own address
    from an IP that has already seen ten others, and must not be refused."""
    lim = EmailProbeLimiter(Clock())
    assert lim.note("1.2.3.4", JUDY)[0]
    for i in range(MAX_DISTINCT_EMAILS + 5):
        lim.note("1.2.3.4", f"other{i}@example.com")
    for i in range(9):
        allowed, _, _ = lim.note("1.2.3.4", JUDY)
        assert allowed, f"refused her on retry {i + 1} from a saturated shared address"


# ── a refusal must leave a trace, once ────────────────────────────────────────
def test_a_refusal_is_recordable_once_per_window():
    """The limiter was shipped unfalsifiable in the one direction that matters: it exists
    to avoid turning a real customer away, and nothing recorded when it did."""
    clock = Clock()
    lim = EmailProbeLimiter(clock)
    assert lim.should_record("1.2.3.4") is True
    for _ in range(50):
        assert lim.should_record("1.2.3.4") is False, "a sweep must not write 50 rows"
    clock.advance(WINDOW_SECONDS + 1)
    assert lim.should_record("1.2.3.4") is True, "a new window must record again"


def test_each_client_is_recorded_separately():
    lim = EmailProbeLimiter(Clock())
    assert lim.should_record("1.2.3.4") is True
    assert lim.should_record("5.6.7.8") is True, "one client must not mute another"


def test_pruning_forgets_recorded_refusals_too():
    """Otherwise the anti-amplification map is itself an unbounded leak."""
    clock = Clock()
    lim = EmailProbeLimiter(clock)
    lim.note("1.2.3.4", "a@example.com")
    lim.should_record("1.2.3.4")
    clock.advance(WINDOW_SECONDS + 1)
    lim.prune()
    assert lim._recorded == {}
    assert lim._seen == {}
