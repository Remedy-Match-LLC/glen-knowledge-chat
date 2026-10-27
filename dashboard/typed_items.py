"""Typed New Order items: a product typed into the New Order form that is not in the
catalog yet.

Glen, 2026-10-26, relayed by production: "a way to type in a new item to the list".
Glen, 2026-10-27, platform tab, "yes" to: a typed item always invoices at the typed
price, and Pure Powder rules apply when its name says Pure Powder.

A typed item is read ONLY by the console order list and the in-house invoice pricer.
It never reaches the store, the cart, the chat or the dispensary, because none of
those read this table. A catalog product with the same slug supersedes it: callers
look in the catalog first.

Pure functions over a sqlite/Postgres connection, mirroring invoice_snippets.
"""
import re
from datetime import datetime, timezone

MAX_NAME = 120
MAX_PRICE_CENTS = 1_000_000   # $10,000: a typo guard, not a policy


def _now():
    return datetime.now(timezone.utc).isoformat()


def slugify(name):
    """The slug the catalog would give this name: lowercase words joined by hyphens."""
    return re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")


def init_table(cx):
    cx.execute("""
        CREATE TABLE IF NOT EXISTS typed_order_items (
            slug TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            price_cents INTEGER NOT NULL,
            bottle_type TEXT,
            created_by TEXT,
            created_at TEXT NOT NULL
        )
    """)
    cx.commit()


def clean(name, price_cents):
    """(name, price_cents) checked, or raises ValueError with a message for the form."""
    n = " ".join((name or "").split())
    if not n:
        raise ValueError("Type the item's name.")
    if len(n) > MAX_NAME:
        raise ValueError("The name is longer than %d characters." % MAX_NAME)
    if not slugify(n):
        raise ValueError("The name needs at least one letter or number.")
    if isinstance(price_cents, bool) or (isinstance(price_cents, float)
                                         and not price_cents.is_integer()):
        raise ValueError("Type the item's price.")
    try:
        cents = int(price_cents)
    except (TypeError, ValueError):
        raise ValueError("Type the item's price.")
    if cents <= 0:
        raise ValueError("The price must be more than $0.")
    if cents > MAX_PRICE_CENTS:
        raise ValueError("The price is over $10,000. Check it.")
    return n, cents


def _row(r):
    return {"slug": r[0], "name": r[1], "price_cents": int(r[2]),
            "bottle_type": r[3] or None}


def get(cx, slug):
    r = cx.execute("SELECT slug, name, price_cents, bottle_type FROM typed_order_items "
                   "WHERE slug=?", ((slug or "").strip(),)).fetchone()
    return _row(r) if r else None


def list_all(cx):
    return [_row(r) for r in cx.execute(
        "SELECT slug, name, price_cents, bottle_type FROM typed_order_items "
        "ORDER BY name").fetchall()]


def _same_or_refuse(have, name, cents, bottle_type):
    """A repeat of the same item (a retry, a double click) returns it. A different
    price or package for the same name is refused: a price never changes silently,
    and the line price can be edited on the order instead."""
    if (have["name"] == name and have["price_cents"] == cents
            and (have["bottle_type"] or None) == bottle_type):
        return have
    raise ValueError("%s is already in the list at $%.2f." % (have["name"],
                                                             have["price_cents"] / 100))


def add(cx, name, price_cents, *, created_by=None, bottle_type=None):
    """Insert one typed item and return it. Raises ValueError when the input is
    invalid or the slug is already taken by a different item."""
    n, cents = clean(name, price_cents)
    slug = slugify(n)
    bt = (bottle_type or "").strip() or None
    have = get(cx, slug)
    if have:
        return _same_or_refuse(have, n, cents, bt)
    try:
        cx.execute("INSERT INTO typed_order_items (slug, name, price_cents, bottle_type, "
                   "created_by, created_at) VALUES (?,?,?,?,?,?)",
                   (slug, n, cents, bt, created_by, _now()))
        cx.commit()
    except Exception:
        # Two adds of one name at once: the primary key refuses the second insert.
        cx.rollback()
        have = get(cx, slug)
        if not have:
            raise
        return _same_or_refuse(have, n, cents, bt)
    return get(cx, slug)
