"""Group Coaching access for anyone who paid over $200 in the last 30 days.

Glen, 2026-11-04 (primary tab): "extend invitation to anyone who purchases over $200 in
the last 30 days, independent of biofield status at least for now." Readings confirmed by
Glen in the platform tab the same day ("yes"):

- They get Group Coaching access as well as the weekly invitation.
- "Over $200" is money paid on non-cancelled orders, summed over a rolling 30 days by
  payment date, after points and courtesies, services included. paid_cents is that
  figure: points and courtesies never reach it.
- Access ends when that 30-day sum drops to $200 or less. Nothing is stored, so it ends
  on its own.
- No member pricing or other membership benefit. Only the Group Coaching gate and the
  invitation read this.
"""
from datetime import datetime, timedelta, timezone

THRESHOLD_CENTS = 20000      # over $200, so exactly $200.00 does not qualify
WINDOW_DAYS = 30


def _naive_utc(value):
    """A stored paid_at as a naive UTC datetime, or None. Reads ISO text with or
    without a zone, and a datetime as Postgres may return it."""
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        try:
            dt = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def _canonical(cx, email, memo):
    """A merged address counts under its survivor, so spend split across two addresses
    adds up and the gate, the board and the sender all name the same person. Falls back
    to the address itself (review round 2, 2026-11-04)."""
    if email not in memo:
        try:
            from dashboard import person_aliases as _pal
            memo[email] = (_pal.canonical_email(cx, email) or email).strip().lower()
        except Exception:
            memo[email] = email
    return memo[email]


def ensure_tables(cx):
    """Create the two tables read here, for a fresh database. The board and the sender
    call it; the Group Coaching gate does not, because on Postgres the setup takes an
    exclusive lock on orders and the gate runs on every portal view (review round 1)."""
    from dashboard import order_payments as _op
    from dashboard import orders as _orders
    _orders.init_orders_table(cx)
    _op.ensure_table(cx)


def _refunds_by_order(cx):
    """{order_id: [(created_at, cents)]} for active refunds. A refund is a ledger row and
    leaves the order's paid_cents as it was, so a refund made after the order was paid is
    subtracted here (review round 1). One made before it is already netted out of
    paid_cents, which is written once, at full payment (review round 3)."""
    out = {}
    for oid, created_at, cents in cx.execute(
            "SELECT order_id, created_at, amount_cents FROM order_payments "
            "WHERE kind='refund' AND status='active'").fetchall():
        out.setdefault(oid, []).append((_naive_utc(created_at), int(cents or 0)))
    return out


def spend_by_email(cx, now=None):
    """{email: cents paid in the window} for every buyer with a paid order in it, keyed
    by the surviving address of a merged person."""
    now = now or datetime.utcnow()
    since = now - timedelta(days=WINDOW_DAYS)
    out, memo = {}, {}
    refunds = _refunds_by_order(cx)
    for oid, email, paid_cents, paid_at, status in cx.execute(
            "SELECT id, email, paid_cents, paid_at, status FROM orders "
            "WHERE paid_at IS NOT NULL").fetchall():
        if (status or "") == "cancelled":
            continue
        when = _naive_utc(paid_at)
        if when is None or when < since or when > now:
            continue
        key = (email or "").strip().lower()
        if not key:
            continue
        key = _canonical(cx, key, memo)
        later = sum(c for at, c in refunds.get(oid, ()) if at is None or at >= when)
        net = max(0, int(paid_cents or 0) - later)
        out[key] = out.get(key, 0) + net
    return out


def spenders(cx, now=None):
    """{email: cents} for each buyer over the threshold."""
    return {e: c for e, c in spend_by_email(cx, now).items() if c > THRESHOLD_CENTS}


def qualifies(cx, email, now=None):
    key = (email or "").strip().lower()
    if not key:
        return False
    return spend_by_email(cx, now).get(_canonical(cx, key, {}), 0) > THRESHOLD_CENTS
