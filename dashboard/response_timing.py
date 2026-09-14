"""Make a response take the same time whatever the answer.

WHY. `/portal/login-request` answers identically for a real address and an unknown one,
which is deliberate and correct. It does not take the same TIME.

Measured on production 2026-09-11, the found branch derived from the token timestamp and
the log line written straight after the send returns, on two real sends:

    found    ~0.9s end to end   (764ms and 749ms of it inside the mail send)
    missing  0.14 - 0.19s       (five direct measurements)

The found path writes a token and calls the mail API. The missing path does one lookup and
returns. **So anyone with a list of addresses can sort customers from strangers with a
stopwatch, and the identical message does not help.**

`/portal/password-reset-request` has the same shape. Its found branch is unmeasured,
because measuring it means mailing a real customer.

`/portal/password-login` is NOT in this file and does not need to be. It already runs the
password hash on the unknown-address branch specifically to flatten this, and it can only
reveal who has a password, which is ten people out of ten thousand.

THE APPROACH. Pad, do not restructure. Record the time at entry and wait, before
returning, until a fixed total has passed. Every answer then costs the same.

The better long-term fix is to move the send off the request path entirely, which removes
the difference instead of hiding it and makes the route faster for real people. That is a
bigger change needing its own delivery logging, and this is the small one that closes the
leak today.

ON THE COST. The app runs gunicorn with `--worker-class gevent`, so a waiting request
yields rather than occupying a worker. The wait is wall-clock for the caller and nearly
free for the server.

ON THE TARGET. It has to exceed the SLOWEST found branch or the tail still leaks. Anything
over it is reported rather than silently truncated, because a target that has drifted
below reality is the failure mode here and it is otherwise invisible.
"""

# Above the ~0.9s found branch with headroom for a slow mail call.
TARGET_SECONDS = 1.5


def remaining(elapsed, target=TARGET_SECONDS):
    """Seconds still to wait so the total reaches `target`. Never negative.

    Pure on purpose: the policy is then testable without a test that actually sleeps, and
    a test that sleeps is a test people delete.
    """
    try:
        elapsed = float(elapsed)
    except (TypeError, ValueError):
        return 0.0
    if elapsed != elapsed or elapsed < 0:      # NaN, or a clock that went backwards
        return 0.0
    return max(0.0, float(target) - elapsed)


def overran(elapsed, target=TARGET_SECONDS):
    """True when the work already took longer than the target, so this response leaked.

    One of these is not a problem. A steady stream of them means TARGET_SECONDS has fallen
    below the real found branch and the padding has quietly stopped equalising anything.
    """
    try:
        return float(elapsed) > float(target)
    except (TypeError, ValueError):
        return False
