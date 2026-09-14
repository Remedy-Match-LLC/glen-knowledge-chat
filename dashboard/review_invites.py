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

HOLDS. Nothing on an order records a missing item or a complaint, so on
2026-09-13 a buyer was asked to review the bottle she had reported missing. A
hold is set by hand from the invoice editor: slug '' holds the whole order, a
slug holds one product on it. A hold never stamps the pair as invited, so
releasing it inside the window lets the invite go.

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
    cx.execute("CREATE TABLE IF NOT EXISTS review_invite_holds ("
               "order_id INTEGER NOT NULL, slug TEXT NOT NULL DEFAULT '', "
               "reason TEXT, held_at TEXT, PRIMARY KEY (order_id, slug))")
    cx.commit()


def _invited_pairs(cx):
    return {(r[0], r[1]) for r in
            cx.execute("SELECT email, slug FROM review_invites").fetchall()}


def _held_pairs(cx):
    return {(int(r[0]), r[1] or "") for r in
            cx.execute("SELECT order_id, slug FROM review_invite_holds").fetchall()}


def _client_address(email, name, source, practitioner_id, recipient_email,
                    address_json, switched_on):
    """(email, name) the invite goes to, or ('', '') for no invite.

    An order with no practitioner goes to its buyer, as before. A practitioner's
    order waits for that practitioner's switch. When the order names a client email,
    the invite goes there. A drop-ship without one sends nothing, because its
    `email` is the practitioner's own and they never used the product."""
    pid = str(practitioner_id).strip() if practitioner_id is not None else ""
    if not pid:
        return email, name
    if pid not in switched_on:
        return "", ""
    buyer = (email or "").strip().lower()
    client = (recipient_email or "").strip()
    if client:
        if client.lower() == buyer and source == "dropship":
            return "", ""
        try:
            ship_name = (json.loads(address_json or "{}") or {}).get("name") or ""
        except (TypeError, ValueError, AttributeError):
            ship_name = ""
        return client, (ship_name or name)
    if source == "dropship":
        return "", ""
    return email, name


def pending(cx, *, days, max_age_days=60, limit=200):
    """(email, name, slug, order_id) for every product on a shipped order that is
    between `days` and `max_age_days` old, has never been invited, and is not held."""
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
    from dashboard import practitioner_settings as _ps

    # Older databases may lack either column; select NULL rather than fail the run.
    pid_col = "practitioner_id" if _db.column_exists(cx, "orders", "practitioner_id") else "NULL"
    rcpt_col = "recipient_email" if _db.column_exists(cx, "orders", "recipient_email") else "NULL"
    rows = cx.execute(
        f"SELECT id, email, name, items_json, source, {pid_col}, {rcpt_col}, address_json "
        "FROM orders "
        f"WHERE status IN ({placeholders}) "
        "AND TRIM(COALESCE(email,'')) <> '' "
        "AND COALESCE(updated_at,'') <> '' "
        + window +
        "ORDER BY updated_at ASC",
        (*_SHIPPED_STATUSES, f"-{int(days)} days", f"-{int(max_age_days)} days")
    ).fetchall()

    _ps.init_settings_table(cx)
    switched_on = _ps.pids_with_client_review_emails(cx)
    already = _invited_pairs(cx)
    held = _held_pairs(cx)
    seen, out = set(), []
    for (order_id, email, name, items_json, source,
         practitioner_id, recipient_email, address_json) in rows:
        if (int(order_id), "") in held:
            continue
        email, name = _client_address(email, name, source, practitioner_id,
                                      recipient_email, address_json, switched_on)
        if not email:
            continue
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
            if (int(order_id), slug) in held:
                continue
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


def hold(cx, order_id, slug="", reason=""):
    """Stop review invites for one order (slug '') or one product on it.
    Holding again replaces the reason."""
    init_table(cx)
    from datetime import datetime, timezone

    cx.execute(
        "INSERT INTO review_invite_holds (order_id, slug, reason, held_at) "
        "VALUES (?,?,?,?) ON CONFLICT (order_id, slug) DO UPDATE SET "
        "reason = excluded.reason, held_at = excluded.held_at",
        (int(order_id), (slug or "").strip(), (reason or "").strip(),
         datetime.now(timezone.utc).isoformat()))
    cx.commit()


def release(cx, order_id, slug=""):
    """Remove a hold. Releasing one that does not exist is a no-op."""
    init_table(cx)
    cx.execute("DELETE FROM review_invite_holds WHERE order_id = ? AND slug = ?",
               (int(order_id), (slug or "").strip()))
    cx.commit()


def holds_for(cx, order_id):
    init_table(cx)
    rows = cx.execute(
        "SELECT slug, reason, held_at FROM review_invite_holds "
        "WHERE order_id = ? ORDER BY slug", (int(order_id),)).fetchall()
    return [{"slug": r[0] or "", "reason": r[1] or "", "held_at": r[2] or ""}
            for r in rows]
