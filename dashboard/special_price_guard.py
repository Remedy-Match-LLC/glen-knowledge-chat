"""Flag invoice lines billed ABOVE a client's own saved special price.

Debra Herndon's invoice was rebuilt at full retail: Biofield Analysis at $300
against a saved $0.00 courtesy, and formulations at $69.97 against a saved $50.00
flat rate. $443 became $809.13 and she was sent a link to pay it. She queried it;
a less attentive client pays the wrong number.

This is a WARNING, never a refusal. It sits beside the shared invoice pricer, and
an operator stop in shared pricing code once returned 400 for 79 products at
CLIENT checkout for six days. Charging a client above their saved rate is
sometimes right -- a rate can be stale, or a line genuinely different -- so the
operator is told, not blocked.

Pure: no database, no network. The caller supplies the client's saved prices and
(optionally) how to decide whether a slug is a Functional Formulation, since the
flat rate applies to those alone.
"""


def _cents(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def overpriced_lines(items, saved, *, ff_eligible=None):
    """Lines charged above this client's saved price.

    `saved` is {"ff_flat_cents": int|None, "sku": {slug: cents}}. A per-SKU price
    wins over the flat rate, being the more specific of the two. The flat rate is
    consulted only for slugs `ff_eligible` accepts, because it covers Functional
    Formulations and judging an ionizer by it would flag ordinary retail.
    """
    saved = saved or {}
    per_sku = saved.get("sku") or {}
    flat = _cents(saved.get("ff_flat_cents"))
    out = []
    for item in (items or []):
        if not isinstance(item, dict):
            continue
        slug = (item.get("slug") or "").strip()
        unit = _cents(item.get("unit_cents"))
        if not slug or unit is None:
            continue
        want = _cents(per_sku.get(slug))
        if want is None and flat is not None:
            if ff_eligible is None or ff_eligible(slug):
                want = flat
        if want is None or unit <= want:
            continue
        out.append({"slug": slug, "unit_cents": unit, "saved_cents": want})
    return out


def warning_for(found):
    """One operator-facing line naming each overcharge, or "" when there is none."""
    if not found:
        return ""
    bits = ["%s at $%.2f against their saved $%.2f"
            % (f["slug"], f["unit_cents"] / 100, f["saved_cents"] / 100)
            for f in found]
    return ("This client has saved special pricing that this invoice does not use: "
            + "; ".join(bits) + ". Check before sending it to them.")


def saved_prices(cx, email):
    """This client's saved rates as `overpriced_lines` wants them, or None.

    None means "this client has no special pricing" — distinct from a client whose
    every saved rate happens to be zero. Reads through client_prices' own accessors
    so the reserved FF-flat slug stays out of the per-SKU map.
    """
    from dashboard import client_prices
    if not (email or "").strip():
        return None
    client_prices.init_table(cx)  # normalises the email on every lookup
    saved = {"ff_flat_cents": client_prices.get_ff_flat(cx, email),
             "sku": client_prices.price_map(cx, email) or {}}
    if saved["ff_flat_cents"] is None and not saved["sku"]:
        return None
    return saved


def warning_for_client(cx, email, items, *, ff_eligible=None):
    """The whole check: look up the client's rates, compare, phrase it."""
    saved = saved_prices(cx, email)
    if saved is None:
        return ""
    return warning_for(overpriced_lines(items, saved, ff_eligible=ff_eligible))


def init_events(cx):
    """Same DDL idiom as client_prices, so pgcompat rewrites it for Postgres."""
    cx.execute("""
        CREATE TABLE IF NOT EXISTS special_price_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            order_id INTEGER,
            route TEXT NOT NULL,
            actor TEXT,
            slug TEXT NOT NULL,
            billed_cents INTEGER NOT NULL,
            saved_cents INTEGER NOT NULL
        )
    """)
    cx.execute("CREATE INDEX IF NOT EXISTS idx_spe_ts ON special_price_events(ts)")


def record_events(cx, found, *, order_id, route, actor):
    """Log each overcharge with the route that produced it.

    A single overcharge is a mystery -- Debra's cost a day of guessing which path
    rebuilt her invoice. Six rows that all name the same route is a bug report that
    points at the code to fix. Best-effort: this runs inside the invoice save, and a
    logging fault must never cost the save.
    """
    if not found:
        return
    from datetime import datetime, timezone
    try:
        init_events(cx)
        now = datetime.now(timezone.utc).isoformat()
        for f in found:
            cx.execute(
                "INSERT INTO special_price_events "
                "(ts, order_id, route, actor, slug, billed_cents, saved_cents) "
                "VALUES (?,?,?,?,?,?,?)",
                (now, order_id, route, actor, f["slug"],
                 int(f["unit_cents"]), int(f["saved_cents"])))
    except Exception as exc:
        print(f"[special-price-guard] event log skipped: {exc!r}", flush=True)


def recent_events(cx, limit=100):
    """Newest first. Empty on any fault -- a board must not 500 over its own log."""
    try:
        init_events(cx)
        rows = cx.execute(
            "SELECT ts, order_id, route, actor, slug, billed_cents, saved_cents "
            "FROM special_price_events ORDER BY id DESC LIMIT ?", (int(limit),)).fetchall()
    except Exception as exc:
        print(f"[special-price-guard] event read skipped: {exc!r}", flush=True)
        return []
    cols = ("ts", "order_id", "route", "actor", "slug", "billed_cents", "saved_cents")
    return [dict(zip(cols, r)) for r in rows]
