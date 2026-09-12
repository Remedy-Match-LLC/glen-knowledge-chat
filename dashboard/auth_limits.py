"""Rate limiting for the public auth routes. Flask-free and pure, so it unit-tests
in isolation.

WHAT THIS STOPS, and what it deliberately does not.

The public auth routes answer identically whether or not an address has an account,
which is correct and was done on purpose. They also answered **as many times as anyone
asked**: 12 rapid POSTs to /portal/login-request all returned 200 on 2026-09-11, with no
throttle, no backoff and no lockout. The per-account lockout in `portal_auth` only fires
for an address that HAS credentials, so it never fires during an enumeration sweep.

This module limits **distinct email addresses per client per window**, NOT request count.

WHY THAT SHAPE, AND IT IS THE WHOLE DESIGN. On 2026-09-10 a real customer, working her
way into her own account, sent **9 requests in a single minute** and 14 across the day.
She succeeded three minutes later. Every one of those was for **one address, her own**.

    a struggling customer  =  many requests,  ONE address
    an enumeration sweep   =  many requests,  MANY addresses

A flat per-IP request count cannot tell those apart and would have locked her out three
minutes before she got in. Counting distinct addresses separates them cleanly: she scores
1 against a limit of 10, forever, however hard she hammers.

THE LIMITER MUST NOT BECOME THE ORACLE IT REPLACES. `note()` is called with the address
BEFORE any lookup, so it behaves identically for a real account and an invented one. It
never consults the database. Same threshold, same 429, same work, either way. Replacing a
timing leak with a behavioural one would be no improvement.
"""
import hashlib
import ipaddress
import threading
import time

# Distinct addresses one client may ask about per window before being refused.
#
# 10 and one hour. The floor is set by real usage and the ceiling by what a sweep needs:
#   * the customer above scores 1, so there is a 10x margin on the case we know about
#   * a household, or Rae helping several clients from the office, plausibly reaches 3-5
#   * enumerating a customer list needs hundreds or thousands, so it dies at 10
# Raising this weakens it linearly. Lowering it walks toward the customer, not away.
MAX_DISTINCT_EMAILS = 10
WINDOW_SECONDS = 3600

# A hard ceiling on raw requests, far above any human. This is NOT the enumeration
# defence; it exists so one client cannot spin the CPU. The customer's worst minute was 9
# requests, so 120 an hour leaves her an 8x margin on top of the distinct-address margin.
MAX_REQUESTS = 120


def trusted_client_ip(xff: str, remote_addr: str, trusted_proxies: int = 1) -> str:
    """The client address, taken from the RIGHT-hand end of X-Forwarded-For.

    THE LEFT-HAND END IS WHATEVER THE CLIENT SENT. Measured against production on
    2026-09-11: a request carrying `X-Forwarded-For: 203.0.113.77` was recorded by
    `portal_auth._record_event` with `ip_hash` equal to sha256("203.0.113.77"), and a
    request sending no such header was recorded as the real client address. So Render
    APPENDS exactly one element and does not replace what it is given.

    Every existing auth route uses `.split(",")[0]`, which takes the forged element. A
    limiter keyed on that is a no-op: rotate the header and every request is a new
    identity, while the limiter looks like it is working.

    `trusted_proxies=1` is Render's shape, measured rather than assumed. If another proxy
    is ever put in front, this number goes up by that many, and getting it WRONG in the
    other direction is the dangerous case: too high reaches back into client-controlled
    text and hands an attacker the key again.
    """
    parts = [p.strip() for p in (xff or "").split(",") if p.strip()]
    if parts:
        idx = len(parts) - trusted_proxies
        raw = parts[idx] if 0 <= idx < len(parts) else parts[0]
    else:
        raw = (remote_addr or "").strip()
    if not raw:
        return "anon"
    try:
        ip = ipaddress.ip_address(raw)
    except ValueError:
        # Unparseable means client-supplied junk. Key on it verbatim rather than
        # discarding it, so a client cannot escape the limiter by sending garbage.
        return raw[:64]
    if ip.version == 6:
        # A single IPv6 allocation is enormous. Collapse to /64 or a sweep just walks
        # addresses within its own prefix, same as the chat limiter already does.
        return f"{ipaddress.ip_network(f'{raw}/64', strict=False).network_address}/64"
    return raw


class EmailProbeLimiter:
    """Per-client sliding window over DISTINCT addresses asked about.

    In-memory and per-process, matching `chat_limits.VelocityLimiter`. That is a real
    limitation and it is stated rather than hidden.

    THE DEPLOYED CEILING IS TWICE THE CONSTANT. `render.yaml` and the live service both
    run `gunicorn app:app --workers 2`, on 1 instance, so a client is limited by whichever
    of 2 processes happens to serve each request. **The effective allowance is about 20
    distinct addresses an hour, not 10.** A restart forgets everything as well.

    Neither changes the verdict: a sweep needs hundreds or thousands of addresses and dies
    at either number. It is written here so nobody reads the constant as the guarantee.
    Raising the worker count raises the ceiling proportionally, and a shared store would
    replace these internals without touching the call sites.
    """

    def __init__(self, clock=time.time):
        self._clock = clock
        self._seen = {}          # key -> list[(timestamp, email_hash)]
        self._recorded = {}      # key -> timestamp of the refusal we wrote a row for
        self._lock = threading.Lock()

    @staticmethod
    def _eh(email: str) -> str:
        """Hash the address. The limiter never needs to read one back, and this keeps
        addresses out of process memory the way `_record_event` keeps them out of the
        events table."""
        return hashlib.sha256((email or "").strip().lower().encode()).hexdigest()

    def note(self, key: str, email: str):
        """Record one probe and report whether it is allowed.

        Returns (allowed, retry_after_seconds, reason). Call this BEFORE looking the
        address up, always, for every address. Skipping it on the missing branch would
        make the limiter itself answer the question the routes refuse to answer.
        """
        now = self._clock()
        eh = self._eh(email)
        with self._lock:
            hits = [(t, h) for (t, h) in self._seen.get(key, ()) if now - t < WINDOW_SECONDS]
            distinct = {h for (_, h) in hits}
            # An address already counted this window is free: this is what lets the
            # customer above retry her own address as often as she needs to.
            if eh not in distinct and len(distinct) >= MAX_DISTINCT_EMAILS:
                self._seen[key] = hits
                oldest = min(t for (t, _) in hits)
                return (False, max(1, int(WINDOW_SECONDS - (now - oldest))), "distinct_emails")
            if len(hits) >= MAX_REQUESTS:
                self._seen[key] = hits
                oldest = min(t for (t, _) in hits)
                return (False, max(1, int(WINDOW_SECONDS - (now - oldest))), "requests")
            hits.append((now, eh))
            self._seen[key] = hits
            return (True, 0, "")

    def should_record(self, key) -> bool:
        """True the FIRST time this key is refused in a window, False after.

        A refusal has to leave a trace or the limiter is unfalsifiable: it was built
        entirely around not turning a real customer away, and without a record there is no
        way to find out that it did. The whole point of shipping it was Judy getting in.

        But a sweep generates thousands of refusals, and writing a row for each would let
        an attacker fill the events table through the very guard meant to stop them. One
        row per client per window answers "was anyone refused" without that.
        """
        now = self._clock()
        with self._lock:
            last = self._recorded.get(key)
            if last is not None and now - last < WINDOW_SECONDS:
                return False
            self._recorded[key] = now
            return True

    def prune(self):
        """Drop expired keys so a long-lived process does not grow without bound."""
        now = self._clock()
        with self._lock:
            for key in list(self._seen):
                hits = [(t, h) for (t, h) in self._seen[key] if now - t < WINDOW_SECONDS]
                if hits:
                    self._seen[key] = hits
                else:
                    del self._seen[key]
            for key, t in list(self._recorded.items()):
                if now - t >= WINDOW_SECONDS:
                    del self._recorded[key]
