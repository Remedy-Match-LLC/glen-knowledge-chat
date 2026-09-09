"""Which paid orders have earned a post-purchase review invite, and which were sent.

The invite fires a delay after an order SHIPS, never on payment. A buyer cannot
review a product that has not arrived yet, and `orders.status` already carries
shipped / delivered / done.

`max_age_days` is the backlog guard. Without it, the first run after
REVIEWS_ENABLED is switched on would email every historical buyer at once.
The pif_gift_notes invite has the same guard for the same reason.

One invite per (email, slug), ever. Someone who reorders the same product is not
asked twice, and the primary key makes that true across processes rather than by
a check-then-write that two workers can both pass.

Item expansion happens in Python, not SQL: items_json is a text column and
neither backend can be relied on to index into it the same way.

KNOWN APPROXIMATION: `orders` has no shipped_at column, so `updated_at` stands in
for the ship date. Any later edit to the order moves it, which can delay an invite
or pull an old order back inside the window. It can never send a second invite,
because the (email, slug) key outranks the window. Add a real shipped_at if the
timing ever needs to be exact.
"""
import json

_SHIPPED_STATUSES = ("shipped", "delivered", "done")


def init_table(cx):
    cx.execute("CREATE TABLE IF NOT EXISTS review_invites ("
               "email TEXT NOT NULL, slug TEXT NOT NULL, order_id INTEGER, "
               "invited_at TEXT, PRIMARY KEY (email, slug))")
    cx.commit()


def _invited_pairs(cx):
    return {(r[0], r[1]) for r in
            cx.execute("SELECT email, slug FROM review_invites").fetchall()}


def pending(cx, *, days, max_age_days=60, limit=200):
    """(email, name, slug, order_id) for every product on a shipped order that is
    between `days` and `max_age_days` old and has never been invited."""
    init_table(cx)
    from dashboard import db as _db

    placeholders = ",".join("?" for _ in _SHIPPED_STATUSES)
    if _db.backend_of(cx) == "postgres":
        # datetime(col) has no Postgres equivalent; compare as real timestamps.
        window = ("AND updated_at::timestamptz <= now() + (?)::interval "
                  "AND updated_at::timestamptz >= now() + (?)::interval ")
    else:
        window = ("AND datetime(updated_at) <= datetime('now', ?) "
                  "AND datetime(updated_at) >= datetime('now', ?) ")
    rows = cx.execute(
        "SELECT id, email, name, items_json FROM orders "
        f"WHERE status IN ({placeholders}) "
        "AND TRIM(COALESCE(email,'')) <> '' "
        "AND COALESCE(updated_at,'') <> '' "
        + window +
        "ORDER BY updated_at ASC",
        (*_SHIPPED_STATUSES, f"-{int(days)} days", f"-{int(max_age_days)} days")
    ).fetchall()

    already = _invited_pairs(cx)
    seen, out = set(), []
    for order_id, email, name, items_json in rows:
        try:
            items = json.loads(items_json or "[]")
        except (TypeError, ValueError):
            continue  # a hand-edited or truncated row must not stop the run
        if not isinstance(items, list):
            continue
        for it in items:
            if not isinstance(it, dict):
                continue
            slug = (it.get("slug") or "").strip()
            if not slug:
                continue  # hand-typed line with no product behind it
            key = ((email or "").strip().lower(), slug)
            if key in already or key in seen:
                continue
            seen.add(key)
            out.append({"email": email, "name": name or "", "slug": slug,
                        "order_id": order_id})
            if len(out) >= int(limit):
                return out
    return out


def mark_invited(cx, email, slug, order_id):
    """Stamp the (email, slug) pair as invited. Safe to call twice."""
    init_table(cx)
    from dashboard import db as _db

    verb = ("INSERT INTO review_invites (email, slug, order_id, invited_at) "
            "VALUES (?,?,?,")
    now = ("now()" if _db.backend_of(cx) == "postgres"
           else "datetime('now')")
    conflict = " ON CONFLICT (email, slug) DO NOTHING"
    cx.execute(verb + now + ")" + conflict,
               ((email or "").strip().lower(), slug, order_id))
    cx.commit()
