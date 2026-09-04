"""What a client has been dispensed before, most often first.

A reference panel in Biofield Intake: the products this client has actually had,
as a percentage of their own recent orders, so a familiar remedy is one glance
away rather than a memory exercise.

Scoped to the individual client (Glen, 2026-09-04), over up to 50 of their
orders. A ten-order window was measured against real data first and rejected:
27 of 36 products tied on the same count, so ranking would have been noise.

A product counts ONCE per order. Three bottles in one order is one order that
contained it; counting units would rank a bulk buy as the thing they take most.

Total by construction: this feeds a reference panel, and a panel that raises is
worse than a panel that is empty.
"""

# Not dispensed products: the analysis fee rides on half of all orders and would
# sit permanently at the top of a list of things to hand someone.
_NOT_A_PRODUCT = ("biofield-analysis",)
_NOT_A_PREFIX = ("membership:",)

DEFAULT_LIMIT = 50


def _norm_email(value):
    return str(value or "").strip().lower()


def _is_product(item):
    if not isinstance(item, dict):
        return False
    if item.get("service") or item.get("info_only"):
        return False
    slug = str(item.get("slug") or "").strip().lower()
    if slug in _NOT_A_PRODUCT or slug.startswith(_NOT_A_PREFIX):
        return False
    return bool(str(item.get("name") or item.get("slug") or "").strip())


def _label(item):
    return str(item.get("name") or item.get("slug") or "").strip()


def frequency(orders, email, limit=DEFAULT_LIMIT):
    """[{name, slug, count, orders_considered, pct}] for this client, ranked.

    Ties break by name so the list is stable: a reference that reshuffles on
    every load cannot be used as one.
    """
    target = _norm_email(email)
    if not target:
        return []
    mine = []
    for o in (orders or []):
        if not isinstance(o, dict):
            continue
        if _norm_email(o.get("email")) != target:
            continue
        if str(o.get("status") or "").strip().lower() == "cancelled":
            continue
        mine.append(o)
    if not mine:
        return []
    mine.sort(key=lambda o: str(o.get("created_at") or ""), reverse=True)
    try:
        mine = mine[:max(1, int(limit))]
    except (TypeError, ValueError):
        mine = mine[:DEFAULT_LIMIT]

    counts, slugs = {}, {}
    for o in mine:
        items = o.get("items")
        seen = set()
        for item in (items if isinstance(items, list) else []):
            if not _is_product(item):
                continue
            name = _label(item)
            seen.add(name)
            slugs.setdefault(name, str(item.get("slug") or "").strip())
        for name in seen:
            counts[name] = counts.get(name, 0) + 1

    total = len(mine)
    rows = [{"name": n, "slug": slugs.get(n, ""), "count": c,
             "orders_considered": total, "pct": int(round(100.0 * c / total))}
            for n, c in counts.items()]
    rows.sort(key=lambda r: (-r["count"], r["name"].lower()))
    return rows


# --- FileMaker history ---------------------------------------------------------
#
# Most of a long-standing client's orders predate this system. Debra Herndon had
# 2 orders here and 8 in FileMaker, so ranking on ours alone was not thin, it was
# wrong (Glen, 2026-09-04).

import re as _re

# Not products: a discount line and the analysis fee, which FileMaker also
# carries numbered ("Biofield Analysis #1").
_FMP_NOT_A_PRODUCT = _re.compile(
    r"^(courtesy|biofield analysis(\s*#\s*\d+)?|shipping|postage|tax|discount)$", _re.I)

# "Chelation in cello" and "Chelation in bottle" are one remedy in two
# packagings. Left alone each ranks at half the frequency it earned.
_FMP_PACKAGING = _re.compile(r"\s+in\s+(cello|bottle|jar|dropper|tube|caps?|capsules?)$", _re.I)


def _fmp_product(description):
    """A product name from an FMP line, or None when the line is not a product."""
    text = _FMP_PACKAGING.sub("", str(description or "").strip()).strip()
    if not text or _FMP_NOT_A_PRODUCT.match(text):
        return None
    return text


def _same_person(a, b):
    return " ".join(str(a or "").lower().split()) == " ".join(str(b or "").lower().split())


def fmp_orders_for(history, name, email):
    """FileMaker orders for ONE person, shaped like board orders.

    Matched on NAME, not just email. chakamom1@gmail.com is five different
    Herndons in FileMaker, so an email match would file a daughter's order as her
    mother's. An unmatched name returns nothing: showing one client another's
    history is worse than showing none of it.
    """
    out = []
    for record in (history or []):
        if not isinstance(record, dict):
            continue
        client = record.get("client")
        if not isinstance(client, dict) or not _same_person(client.get("name"), name):
            continue
        orders = record.get("orders")
        for o in (orders if isinstance(orders, list) else []):
            if not isinstance(o, dict):
                continue
            items = []
            for item in (o.get("items") if isinstance(o.get("items"), list) else []):
                if not isinstance(item, dict):
                    continue
                product = _fmp_product(item.get("description"))
                if product:
                    items.append({"name": product, "slug": ""})
            out.append({"email": email, "created_at": str(o.get("date") or ""),
                        "status": str(o.get("status") or "done"), "items": items})
    return out
