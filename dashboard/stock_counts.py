"""Bottle counts on the shelf, per product (Glen, 2026-11-06).

Plan: platform/plans/2026-11-06-stock-counts-in-the-app.md. Every rule below is his.

- Each product may carry a count of bottles on the shelf. A product with NO count row
  stays on sale: a missing count never blocks a sale, so nothing goes dark on day one.
- At 0 or below, the product is refused exactly like a hand-set `out_of_stock` flag, by
  the one predicate (practitioner_portal.waitlist_block), with the line
  "{name} is out of stock." A hand-set flag still wins over a count.
- A bottle leaves the count when an order is PAID. A cancelled order puts it back, and
  one made live again takes it off again. GrooveKart sends no paid signal, so its orders
  come off the count on arrival.
- Bundles take one bottle (times the component qty) from each product inside them.
- Made-to-order products (`made_to_order` in the catalog) are never counted. Nor are
  custom ones ("in Terrain Restore" in the name) or drop-shipped ones (DROP_SHIPPED).
- Every change is one row in `stock_log`: slug, delta, new count, who, when, why, order.
- Glen and Rae are emailed when a product falls to LOW_STOCK_AT bottles or fewer, and
  when it falls below one month of average sales. Once per crossing.

How an order moves stock: sync_order(). It locks the order row, re-reads its CURRENT
status, works out what the order should hold off the shelf now, and moves only the
difference. `stock_order_moves` holds, per (order, slug), `qty` (what the order has
accounted for) and `taken` (what it actually took off a count). So paying twice, a
pay/cancel race, a reinstated order, an edited order and a webhook retry all converge on
the same answer, and none double-counts. A sale made before a product's last count was
set is already reflected in that count, so it never comes off it.

The whole feature sits behind STOCK_COUNTS_ENABLED (default off). Off, nothing here reads
or writes the database and every caller behaves exactly as before. Turning it on with no
counts entered changes nothing either: no count means on sale, and nothing to decrement.
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dashboard import db

FLAG = "STOCK_COUNTS_ENABLED"
LOW_STOCK_AT = 3                  # Glen, 2026-11-06: "yes" to 3 bottles or fewer


def low_stock_at(p, avg) -> int:
    """The low-stock alert level for one product. Glen, 2026-11-06: "For Infoceuticals, set
    the alert level to zero, unless the average monthly use is 1 or more, then set it to
    1." Every other product alerts at LOW_STOCK_AT."""
    name = str((p or {}).get("name") or "").lower() if isinstance(p, dict) else ""
    if "infoceutical" in name:
        return 1 if float(avg or 0) >= 1 else 0
    return LOW_STOCK_AT


def month_alert_applies(p, avg) -> bool:
    """Whether the one-month-supply alert can fire. Glen, 2026-11-06: Infoceuticals skip
    it "if they sell less than 1 a month for now"."""
    name = str((p or {}).get("name") or "").lower() if isinstance(p, dict) else ""
    a = float(avg or 0)
    if "infoceutical" in name and a < 1:
        return False
    return a > 0
AVG_WINDOW_DAYS = 365             # average monthly sales over the last 12 months
DEDUPE_DAYS = 14                  # a line within 14 days of another source's line is one sale
RECONCILE_DAYS = 30               # the self-heal looks back this far
ZERO_CACHE_SECONDS = 5.0          # how stale the sold-out set may be in another worker
AVG_CACHE_SECONDS = 600.0
SEND_RETRY_AFTER_SECONDS = 3600   # a send left 'pending' this long (a crash) is retried
CONSOLE_URL = "https://illtowell.com/console/stock"
_DAYS_PER_MONTH = 30.4375

_DB_PATH_FN = None
_CATALOG_FN = None
_LOCK = threading.Lock()
_ZERO = {"path": None, "at": 0.0, "slugs": frozenset()}
_AVG = {"path": None, "at": 0.0, "avg": {}}


def enabled() -> bool:
    """Read live, so a Doppler flip on redeploy and a test monkeypatch both apply."""
    return os.environ.get(FLAG, "").strip().lower() in ("1", "true", "yes", "on")


def set_db_path_fn(fn):
    """app.py registers `lambda: LOG_DB`, so the predicate reads the orders database."""
    global _DB_PATH_FN
    _DB_PATH_FN = fn


def set_catalog_fn(fn):
    """app.py registers its in-memory catalog ({slug: product})."""
    global _CATALOG_FN
    _CATALOG_FN = fn


def db_path() -> str:
    if _DB_PATH_FN is not None:
        return str(_DB_PATH_FN())
    root = os.environ.get("DATA_DIR", str(Path(__file__).resolve().parent.parent))
    return str(Path(root) / "chat_log.db")


def _catalog(catalog=None) -> dict:
    if catalog is not None:
        return catalog
    if _CATALOG_FN is not None:
        return _CATALOG_FN() or {}
    from dashboard import wholesale_pricing
    return wholesale_pricing._load_catalog()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _invalidate_zero():
    """A count changed: the sold-out set is stale. The average is not."""
    with _LOCK:
        _ZERO["path"] = None


def _invalidate():
    """Both caches (tests)."""
    with _LOCK:
        _ZERO["path"] = None
        _AVG["path"] = None


def _safe_rollback(cx):
    try:
        cx.rollback()
    except Exception:  # noqa: BLE001
        pass


# ── schema ──────────────────────────────────────────────────────────────────────

_INITED = set()


def ensure_tables(cx):
    """init_tables once per process per database path (review round 3)."""
    key = db_path()
    if key not in _INITED:
        init_tables(cx)
        _INITED.add(key)


def _forget_tables():
    """A missing table after ensure_tables: the database was replaced. Re-init next time."""
    _INITED.discard(db_path())


def init_tables(cx):
    # set_at: when the count was last SET by hand. Sales before it are in the count.
    cx.execute("""CREATE TABLE IF NOT EXISTS stock_counts (
        slug       TEXT PRIMARY KEY,
        count      INTEGER NOT NULL,
        set_at     TEXT NOT NULL,
        updated_at TEXT NOT NULL)""")
    # Append only. Nothing updates or deletes a row here.
    cx.execute("""CREATE TABLE IF NOT EXISTS stock_log (
        id        INTEGER PRIMARY KEY AUTOINCREMENT,
        slug      TEXT NOT NULL,
        delta     INTEGER NOT NULL,
        new_count INTEGER NOT NULL,
        who       TEXT NOT NULL DEFAULT '',
        logged_at TEXT NOT NULL,
        reason    TEXT NOT NULL DEFAULT '',
        order_id  INTEGER)""")
    cx.execute("CREATE INDEX IF NOT EXISTS ix_stock_log_slug ON stock_log(slug)")
    # Per (order, slug): qty the order has accounted for, and bottles it actually took
    # off a count. sync_order moves only the difference to what it should hold now.
    # sold_at: when the order first went live, never updated (a re-payment re-stamps
    # orders.paid_at; this does not move). Slug '' with qty 0 marks an order synced
    # with nothing to count, so the self-heal skips it.
    cx.execute("""CREATE TABLE IF NOT EXISTS stock_order_moves (
        order_id   INTEGER NOT NULL,
        slug       TEXT NOT NULL,
        qty        INTEGER NOT NULL DEFAULT 0,
        taken      INTEGER NOT NULL DEFAULT 0,
        sold_at    TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        PRIMARY KEY (order_id, slug))""")
    # A row means this alert's crossing is open. Deleted when the condition clears.
    cx.execute("""CREATE TABLE IF NOT EXISTS stock_alerts (
        slug     TEXT NOT NULL,
        kind     TEXT NOT NULL,
        fired_at TEXT NOT NULL,
        PRIMARY KEY (slug, kind))""")
    # Delivery per recipient: 'pending' while a send is in flight, then 'sent' or
    # 'failed'. A failed (or long-pending) one is retried by the next check.
    cx.execute("""CREATE TABLE IF NOT EXISTS stock_alert_sends (
        slug         TEXT NOT NULL,
        kind         TEXT NOT NULL,
        recipient    TEXT NOT NULL,
        state        TEXT NOT NULL,
        attempted_at TEXT NOT NULL,
        PRIMARY KEY (slug, kind, recipient))""")
    # GrooveKart product names that matched no catalog product, for the console page.
    cx.execute("""CREATE TABLE IF NOT EXISTS stock_unmatched_names (
        name       TEXT PRIMARY KEY,
        times      INTEGER NOT NULL DEFAULT 0,
        qty        INTEGER NOT NULL DEFAULT 0,
        first_seen TEXT NOT NULL,
        last_seen  TEXT NOT NULL,
        last_ref   TEXT NOT NULL DEFAULT '')""")
    # One row per GrooveKart delivery, so a redelivered webhook lists nothing twice.
    cx.execute("""CREATE TABLE IF NOT EXISTS stock_gk_deliveries (
        delivery_key TEXT PRIMARY KEY,
        seen_at      TEXT NOT NULL)""")
    cx.commit()


# ── the predicate ───────────────────────────────────────────────────────────────

# Never on the shelf, so never counted, and a count can never take them off sale.
# Glen, 2026-11-06: "15-plate water ionizers are not stocked, they are drop shipped, so
# zero on shelf. Available for sale." Then: "the 9-plate water ionizer is also not
# stocked, but available, so zero on shelf still means it is available for purchase."
DROP_SHIPPED = frozenset({"water-ionizer-15plate", "water-ionizer-9plate"})
# Counted, and alerted on, but a count at 0 never takes them off sale. Glen, 2026-11-06:
# "5-plate ionizer has stock paid for on the shelf at the manufacturer, starting with 5
# units ... always keep it showing available for sale."
ALWAYS_ON_SALE = frozenset({"water-ionizer-5plate"})
# Glen, 2026-11-06: "all items with names that include \"in terrain restore\" are custom."
# Then: "Products with \"HomeoEnergetic\" are custom made as needed."
CUSTOM_NAME_PARTS = ("in terrain restore", "homeoenergetic")


def not_counted_why(p, slug=None):
    """Why a product carries no count ("a bundle", "made to order", "custom",
    "drop shipped"), or None when it can be counted."""
    p = p if isinstance(p, dict) else {}
    slug = slug or p.get("slug")
    if p.get("bundle"):
        return "a bundle"
    if p.get("made_to_order"):
        return "made to order"
    nm = " ".join(str(p.get("name") or "").lower().split())
    if any(part in nm or part in nm.replace(" ", "").replace("-", "")
           for part in CUSTOM_NAME_PARTS):
        return "custom"
    if slug in DROP_SHIPPED:
        return "drop shipped"
    return None


def countable(p, slug=None) -> bool:
    """Whether a product can carry a count. Made-to-order never does (Glen, question 3).
    Nor does a custom or drop-shipped one. A bundle does not either: its components are
    counted."""
    return not_counted_why(p, slug) is None


def zero_slugs() -> frozenset:
    """Slugs counted at 0 or below. Cached briefly; any read failure fails OPEN (an
    empty set), because a missing count never blocks a sale."""
    path = db_path()
    now = time.monotonic()
    with _LOCK:
        if _ZERO["path"] == path and now - _ZERO["at"] < ZERO_CACHE_SECONDS:
            return _ZERO["slugs"]
    slugs = frozenset()
    try:
        cx = db.connect(path)
        try:
            slugs = frozenset(r[0] for r in cx.execute(
                "SELECT slug FROM stock_counts WHERE count <= 0").fetchall())
        finally:
            cx.close()
    except Exception as e:  # noqa: BLE001  (no table yet, or the DB is unreachable)
        if "no such table" not in str(e) and "does not exist" not in str(e):
            print(f"[stock] sold-out read failed, nothing blocked: {e!r}", flush=True)
    with _LOCK:
        _ZERO.update(path=path, at=now, slugs=slugs)
    return slugs


def is_counted_out(slug, p) -> bool:
    """True when the flag is on and `slug` is counted at 0 or below. Flag off: False,
    with no database access at all."""
    if not enabled() or not slug or slug in ALWAYS_ON_SALE or not countable(p, slug):
        return False
    return slug in zero_slugs()


# ── reading ─────────────────────────────────────────────────────────────────────

def get_count(cx, slug):
    r = cx.execute("SELECT count FROM stock_counts WHERE slug=?", (slug,)).fetchone()
    return None if r is None else int(r[0])


def _count_row(cx, slug):
    """(count, set_at) or None."""
    r = cx.execute("SELECT count, set_at FROM stock_counts WHERE slug=?", (slug,)).fetchone()
    return None if r is None else (int(r[0]), r[1])


def log_for(cx, slug, limit=50):
    rows = cx.execute(
        "SELECT slug, delta, new_count, who, logged_at, reason, order_id FROM stock_log "
        "WHERE slug=? ORDER BY id DESC LIMIT ?", (slug, int(limit))).fetchall()
    keys = ("slug", "delta", "new_count", "who", "logged_at", "reason", "order_id")
    return [dict(zip(keys, tuple(r))) for r in rows]


# ── writing ─────────────────────────────────────────────────────────────────────

def _append_log(cx, slug, delta, new_count, who, reason, order_id=None):
    cx.execute(
        "INSERT INTO stock_log (slug, delta, new_count, who, logged_at, reason, order_id) "
        "VALUES (?,?,?,?,?,?,?)",
        (slug, int(delta), int(new_count), str(who or ""), _now(), str(reason or ""),
         (int(order_id) if order_id is not None else None)))


def _lock_count(cx, slug):
    """Lock one count row for this transaction. A no-op UPDATE: on Postgres it takes the
    row lock (as SELECT ... FOR UPDATE would); on SQLite it takes the write lock, so no
    other writer commits in between. Returns whether the row exists."""
    return cx.execute("UPDATE stock_counts SET updated_at=updated_at WHERE slug=?",
                      (slug,)).rowcount == 1


def set_count(cx, slug, count, *, who, reason, catalog=None):
    """Set a product's count (a shelf count, a delivery). Logs the exact change from the
    value it overwrote. Returns {"slug", "count", "delta"}. Raises ValueError on a bad
    slug, count or reason."""
    cat = _catalog(catalog)
    slug = (slug or "").strip()
    p = cat.get(slug)
    if not p:
        raise ValueError(f"unknown product: {slug}")
    if not countable(p, slug):
        raise ValueError(f"{p.get('name') or slug} is not counted ({not_counted_why(p, slug)})")
    try:
        n = int(count)
    except (TypeError, ValueError):
        raise ValueError("count must be a whole number")
    if n < 0:
        raise ValueError("count cannot be negative")
    reason = (reason or "").strip()
    if not reason:
        raise ValueError("a reason is required")
    ensure_tables(cx)
    now = _now()
    try:
        cur = cx.execute(
            "INSERT INTO stock_counts (slug, count, set_at, updated_at) VALUES (?,?,?,?) "
            "ON CONFLICT (slug) DO NOTHING", (slug, n, now, now))
        if cur.rowcount == 1:
            old = 0
        else:
            _lock_count(cx, slug)
            old = get_count(cx, slug)
            cx.execute("UPDATE stock_counts SET count=?, set_at=?, updated_at=? WHERE slug=?",
                       (n, now, now, slug))
        delta = n - old
        _append_log(cx, slug, delta, n, who, reason)
        cx.commit()
    except Exception:
        _safe_rollback(cx)
        raise
    _invalidate_zero()
    check_alerts(cx, [slug], catalog=cat)
    return {"slug": slug, "count": n, "delta": delta}


def _parse_qty(v, default):
    if v is None or v == "":
        return default
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return default


def _line_slug_qty(it):
    """(slug, qty) for one stored order line. A GrooveKart line carries its matched
    product as stock_slug and its quantity as stock_qty (set at ingestion). A line with
    no quantity is one bottle; an explicit 0 or less is none."""
    slug = str(it.get("slug") or it.get("stock_slug") or "").strip()
    raw = it.get("qty") if it.get("qty") not in (None, "") else it.get("stock_qty")
    return slug, _parse_qty(raw, 1)


def units_for_items(items, catalog) -> dict:
    """{slug: bottles} for an order's lines. Bundles expand to their ACTIVE components
    times each component's qty, at any depth, with a cycle guard (as waitlist_block
    walks them). A retired slug follows `superseded_by`. Membership lines and
    made-to-order products carry nothing. Lines with no slug are skipped."""
    from dashboard.products import superseded_slug
    out = {}

    def _add(slug, qty, seen):
        slug = superseded_slug(slug, catalog) if slug else slug
        p = catalog.get(slug) if slug else None
        if p and p.get("bundle"):
            for comp in (p.get("bundle_component_slugs") or []):
                cs = (comp or {}).get("slug")
                cp = catalog.get(cs) if cs else None
                if not cp or cp.get("inactive") or cs in seen:
                    continue
                cq = max(1, _parse_qty((comp or {}).get("qty"), 1))
                _add(cs, qty * cq, seen | {cs})
            return
        if p and not countable(p, slug):
            return
        out[slug] = out.get(slug, 0) + qty

    for it in (items or []):
        if not isinstance(it, dict):
            continue
        slug, qty = _line_slug_qty(it)
        if not slug or it.get("kind") == "membership" or slug.startswith("membership:"):
            continue
        if qty > 0:
            _add(slug, qty, {slug})
    return out


def _parse_items(raw):
    try:
        items = json.loads(raw or "[]")
    except (TypeError, ValueError):
        return []
    return items if isinstance(items, list) else []


def _apply(cx, slug, delta, *, who, reason, order_id):
    """Add `delta` to a count (negative takes bottles off) and log it. Caller holds the
    transaction."""
    cx.execute("UPDATE stock_counts SET count = count + ?, updated_at=? WHERE slug=?",
               (int(delta), _now(), slug))
    _append_log(cx, slug, delta, get_count(cx, slug), who, reason, order_id)


def sync_order(cx, order_id, catalog=None):
    """Make the shelf reflect this order as it stands NOW. Never raises.

    Locks the order row (a no-op UPDATE: a row lock on Postgres, the write lock on
    SQLite), re-reads its status, and works out what it should hold off the shelf:
    its bottles when it is live (paid, or a GrooveKart order, and not cancelled), else
    none. Only the difference from what it already holds moves. A cancel committed
    first means a later pay hook finds it cancelled and takes nothing. Returns
    {slug: new_count} for counts that moved."""
    if not enabled():
        return {}
    try:
        cat = _catalog(catalog)
        ensure_tables(cx)
        if cx.execute("UPDATE orders SET updated_at=updated_at WHERE id=?",
                      (int(order_id),)).rowcount != 1:
            cx.commit()
            return {}
        r = cx.execute("SELECT status, pay_status, source, items_json, "
                       "COALESCE(paid_at, created_at) FROM orders WHERE id=?",
                       (int(order_id),)).fetchone()
        status, pay, source, items_json, now_sold = tuple(r)
        gk = source == "groovekart"
        live = (status or "") != "cancelled" and (pay == "paid" or gk)
        who = "groovekart" if gk else "order"
        want = units_for_items(_parse_items(items_json), cat) if live else {}
        rows = cx.execute("SELECT slug, qty, taken, sold_at FROM stock_order_moves "
                          "WHERE order_id=?", (int(order_id),)).fetchall()
        held = {row[0]: (int(row[1]), int(row[2])) for row in rows if row[0]}
        # When the order first went live, from this log, not orders.paid_at, which a
        # second payment re-stamps (review round 3).
        sold_at = min([str(row[3]) for row in rows] or [str(now_sold or "")])
        moved = {}
        for slug in sorted(set(want) | set(held)):
            q, t = held.get(slug, (0, 0))
            d = want.get(slug, 0)
            if d == q and (live or t == 0):
                continue
            counted = _lock_count(cx, slug)
            row = _count_row(cx, slug) if counted else None
            # An order first sold before the product's count was last set is already
            # in that count. It never takes from it, and a cancel never puts back what
            # it took before the count, which would inflate it (review round 3).
            pre = row is not None and sold_at < str(row[1] or "")
            new_q = d if live else 0
            if pre or not counted:
                new_t = 0
                apply = 0
            else:
                new_t = max(0, t + (d - q)) if live else 0
                apply = t - new_t
            if apply:
                ref = (f"cancelled order #{order_id}" if not live else
                       f"GrooveKart order #{order_id}" if gk else f"paid order #{order_id}")
                _apply(cx, slug, apply, who=who, reason=ref, order_id=order_id)
                moved[slug] = get_count(cx, slug)
            if slug in held:
                cx.execute("UPDATE stock_order_moves SET qty=?, taken=?, updated_at=? "
                           "WHERE order_id=? AND slug=?",
                           (new_q, new_t, _now(), int(order_id), slug))
            else:
                cx.execute("INSERT INTO stock_order_moves (order_id, slug, qty, taken, "
                           "sold_at, updated_at) VALUES (?,?,?,?,?,?)",
                           (int(order_id), slug, new_q, new_t, sold_at, _now()))
        if live and not rows and not want:
            # Live with nothing to count: mark it, so the self-heal skips it.
            cx.execute("INSERT INTO stock_order_moves (order_id, slug, qty, taken, "
                       "sold_at, updated_at) VALUES (?,?,?,?,?,?) "
                       "ON CONFLICT (order_id, slug) DO NOTHING",
                       (int(order_id), "", 0, 0, sold_at, _now()))
        cx.commit()
        if moved:
            _invalidate_zero()
            check_alerts(cx, list(moved), catalog=cat)
        return moved
    except Exception as e:  # noqa: BLE001
        _safe_rollback(cx)
        if "no such table" in str(e) or "does not exist" in str(e):
            _forget_tables()
        print(f"[stock] sync skipped for order #{order_id}: {e!r}", flush=True)
        return {}


def on_order_paid(cx, order_id, catalog=None):
    return sync_order(cx, order_id, catalog)


def on_order_cancelled(cx, order_id, catalog=None):
    return sync_order(cx, order_id, catalog)


# ── self-healing ───────────────────────────────────────────────────────────────

def reconcile(cx, *, days=RECONCILE_DAYS, catalog=None):
    """Heal a crash between an order's commit and its stock move. Syncs every order in
    the last `days` that is live with no move recorded, and every cancelled order that
    still holds bottles. Returns the order ids synced."""
    if not enabled():
        return []
    try:
        ensure_tables(cx)
        cutoff = (datetime.now(timezone.utc) - timedelta(days=int(days))).isoformat()
        ids = [int(r[0]) for r in cx.execute(
            "SELECT o.id FROM orders o WHERE COALESCE(o.status,'') <> 'cancelled' "
            "AND (o.pay_status='paid' OR o.source='groovekart') "
            "AND COALESCE(o.paid_at, o.created_at) >= ? "
            "AND NOT EXISTS (SELECT 1 FROM stock_order_moves m WHERE m.order_id=o.id)",
            (cutoff,)).fetchall()]
        ids += [int(r[0]) for r in cx.execute(
            "SELECT DISTINCT m.order_id FROM stock_order_moves m JOIN orders o "
            "ON o.id=m.order_id WHERE o.status='cancelled' AND m.taken > 0").fetchall()]
        cx.commit()
    except Exception as e:  # noqa: BLE001
        _safe_rollback(cx)
        print(f"[stock] reconcile skipped: {e!r}", flush=True)
        return []
    for oid in sorted(set(ids)):
        sync_order(cx, oid, catalog)
    return sorted(set(ids))


# ── GrooveKart ─────────────────────────────────────────────────────────────────

def groovekart_lines(products) -> list:
    """[(name, qty)] from a GrooveKart webhook's products[]. Quantity is
    `product_quantity` (the field groovekart_notify packs from); absent or bad is 1."""
    out = []
    for item in (products if isinstance(products, list) else []):
        if not isinstance(item, dict):
            continue
        name = str(item.get("product_name") or item.get("name") or "").strip()
        if not name:
            continue
        qty = _parse_qty(item.get("product_quantity") or item.get("quantity"), 1)
        out.append((name, max(1, qty)))
    return out


def groovekart_items(products, catalog=None) -> list:
    """The order row's items for a GrooveKart order: [{"name", "stock_qty"}], plus
    "stock_slug" when the name matches a product EXACTLY (alias, name or title). The
    keys are stock_* so no other reader of items_json (packing, repertoire) changes."""
    from dashboard.practitioner_portal import exact_name_to_slug
    cat = _catalog(catalog)
    out = []
    for name, qty in groovekart_lines(products):
        it = {"name": name, "stock_qty": qty}
        slug = exact_name_to_slug(name, cat)
        if slug:
            it["stock_slug"] = slug
        out.append(it)
    return out


def delivery_key(payload) -> str:
    """The GrooveKart order id, else a hash of the payload."""
    p = payload if isinstance(payload, dict) else {}
    ref = p.get("id") or p.get("order_id") or p.get("reference")
    if ref not in (None, ""):
        return f"id:{ref}"
    blob = json.dumps(payload, sort_keys=True, default=str)
    return "sha256:" + hashlib.sha256(blob.encode()).hexdigest()


def list_unmatched(cx) -> list:
    keys = ("name", "times", "qty", "first_seen", "last_seen", "last_ref")
    return [dict(zip(keys, tuple(r))) for r in cx.execute(
        "SELECT name, times, qty, first_seen, last_seen, last_ref "
        "FROM stock_unmatched_names ORDER BY last_seen DESC").fetchall()]


def on_groovekart_delivery(cx, order_id, products, *, key, ref="", catalog=None):
    """A GrooveKart delivery: list its unmatched names once per delivery, then sync its
    order (if the row was written). The marker insert and the name counts commit
    together, and the names are counted only when the marker inserted. Never raises."""
    if not enabled():
        return {}
    try:
        cat = _catalog(catalog)
        ensure_tables(cx)
        cur = cx.execute("INSERT INTO stock_gk_deliveries (delivery_key, seen_at) "
                         "VALUES (?,?) ON CONFLICT (delivery_key) DO NOTHING", (key, _now()))
        if cur.rowcount == 1:
            now = _now()
            for it in groovekart_items(products, cat):
                if it.get("stock_slug"):
                    continue
                if cx.execute(
                        "UPDATE stock_unmatched_names SET times=times+1, qty=qty+?, "
                        "last_seen=?, last_ref=? WHERE name=?",
                        (it["stock_qty"], now, str(ref or ""), it["name"])).rowcount == 0:
                    cx.execute(
                        "INSERT INTO stock_unmatched_names (name, times, qty, first_seen, "
                        "last_seen, last_ref) VALUES (?,?,?,?,?,?)",
                        (it["name"], 1, it["stock_qty"], now, now, str(ref or "")))
        cx.commit()
    except Exception as e:  # noqa: BLE001
        _safe_rollback(cx)
        print(f"[stock] GrooveKart names skipped for {ref}: {e!r}", flush=True)
    return sync_order(cx, order_id, catalog) if order_id is not None else {}


# ── average monthly sales ──────────────────────────────────────────────────────

def _day(v):
    s = str(v or "")[:10]
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except ValueError:
        return None


def _table_exists(cx, name):
    try:
        cx.execute(f"SELECT 1 FROM {name} LIMIT 1").fetchall()
        return True
    except Exception:  # noqa: BLE001
        _safe_rollback(cx)
        return False


def monthly_sales(cx, catalog=None, *, now=None) -> dict:
    """{slug: average bottles sold per month} over the last AVG_WINDOW_DAYS, from every
    order source (Glen, 2026-11-06: "combine all order sources"):

    - `orders` lines: slug + qty (no qty is 1). Counted when paid, or
      shipped/delivered/done, and not cancelled. GrooveKart orders count unless
      cancelled, by stock_slug/stock_qty, else by an EXACT name match, 1 each.
    - `purchase_history` lines (FileMaker 'fmp', older 'groovekart'): no qty, 1 each.

    A line with the same lowercase email and slug as a line from ANOTHER source within
    DEDUPE_DAYS is the same sale, and counts once: orders vs history, and fmp vs
    groovekart history. Two lines from the same source are two sales. Bundles count
    against their components. Unmatched lines and made-to-order products are left out."""
    from dashboard.practitioner_portal import exact_name_to_slug
    cat = _catalog(catalog)
    today = (now or datetime.now(timezone.utc)).date()
    start = today - timedelta(days=AVG_WINDOW_DAYS)
    cutoff = (start - timedelta(days=DEDUPE_DAYS)).isoformat()
    seen = {}                   # (email, slug) -> [(day, source)]
    totals = {}

    def _count(slug, qty, d):
        if d < start or d > today:
            return
        for s, n in units_for_items([{"slug": slug, "qty": qty}], cat).items():
            if s in cat:
                totals[s] = totals.get(s, 0) + n

    if _table_exists(cx, "orders"):
        for r in cx.execute(
                "SELECT email, items_json, source, COALESCE(paid_at, created_at), status, "
                "pay_status FROM orders WHERE COALESCE(paid_at, created_at) >= ? "
                "AND COALESCE(status,'') <> 'cancelled'", (cutoff,)).fetchall():
            email, items_json, source, at, status, pay = tuple(r)
            gk = source == "groovekart"
            if not (gk or pay == "paid" or status in ("paid", "shipped", "delivered", "done")):
                continue
            d = _day(at)
            if not d:
                continue
            em = (email or "").strip().lower()
            for it in _parse_items(items_json):
                if not isinstance(it, dict):
                    continue
                slug, qty = _line_slug_qty(it)
                if not slug and gk:
                    slug = exact_name_to_slug(str(it.get("name") or ""), cat) or ""
                if slug and qty > 0:
                    seen.setdefault((em, slug), []).append((d, "orders"))
                    _count(slug, qty, d)
    if _table_exists(cx, "purchase_history"):
        for r in cx.execute(
                "SELECT email, slug, purchased_at, source FROM purchase_history "
                "WHERE source IN ('fmp','groovekart') AND purchased_at >= ? "
                "ORDER BY purchased_at", (cutoff,)).fetchall():
            em, slug, at, src = (str(r[0] or "").strip().lower(), str(r[1] or ""), r[2],
                                 str(r[3] or ""))
            d = _day(at)
            if not d or not slug:
                continue
            prior = seen.setdefault((em, slug), [])
            if any(s != src and abs((d - od).days) <= DEDUPE_DAYS for od, s in prior):
                continue
            prior.append((d, src))
            _count(slug, 1, d)
    months = AVG_WINDOW_DAYS / _DAYS_PER_MONTH
    return {s: n / months for s, n in totals.items()}


def monthly_sales_cached(cx, catalog=None) -> dict:
    path = db_path()
    now = time.monotonic()
    with _LOCK:
        if _AVG["path"] == path and now - _AVG["at"] < AVG_CACHE_SECONDS:
            return _AVG["avg"]
    avg = monthly_sales(cx, catalog)
    with _LOCK:
        _AVG.update(path=path, at=now, avg=avg)
    return avg


# ── alerts ─────────────────────────────────────────────────────────────────────

def _recipients():
    from dashboard.groovekart_notify import RECIPIENTS   # Glen and Rae, one To each
    return RECIPIENTS


def _send_one(to, subject, body) -> bool:
    """True when the mail went. Under pytest inbox.send_email sends nothing."""
    from dashboard import inbox
    try:
        r = inbox.send_email(to, subject, body)
    except Exception as e:  # noqa: BLE001
        print(f"[stock] alert to {to} failed: {e!r}", flush=True)
        return False
    return not (isinstance(r, dict) and r.get("error"))


def _spawn(fn):
    """Sends run in the background: an order never waits on, or fails on, mail."""
    threading.Thread(target=fn, daemon=True).start()


def _deliver(slug, kind, to, subject, body):
    ok = _send_one(to, subject, body)
    try:
        cx = db.connect(db_path())
        try:
            cx.execute("UPDATE stock_alert_sends SET state=?, attempted_at=? "
                       "WHERE slug=? AND kind=? AND recipient=? AND state='pending'",
                       ("sent" if ok else "failed", _now(), slug, kind, to))
            cx.commit()
        finally:
            cx.close()
    except Exception as e:  # noqa: BLE001
        print(f"[stock] alert state not saved for {slug}/{kind}/{to}: {e!r}", flush=True)


def _alert_text(kind, name, count, avg, low_at=LOW_STOCK_AT):
    if kind == "low":
        return (f"Low stock: {name}, {count} left",
                f"{name} is down to {count} bottle{'s' if count != 1 else ''}.\n\n"
                f"The alert goes out at {low_at} or fewer. "
                f"About {avg:.1f} sell in a month.\n\nStock page: {CONSOLE_URL}")
    return (f"Under one month of {name}: {count} left",
            f"{name} has {count} bottle{'s' if count != 1 else ''} left. "
            f"About {avg:.1f} sell in a month, so this is less than one month's supply.\n\n"
            f"Stock page: {CONSOLE_URL}")


def check_alerts(cx, slugs, catalog=None):
    """Low (count <= LOW_STOCK_AT) and one month (count < average monthly sales), once
    per crossing, to each recipient. Each slug's count row is locked and re-read before
    deciding, so a stale checker cannot re-arm or fire on an old count. A recipient is
    claimed 'pending' before its send and marked 'sent' only when the send succeeds; a
    failed one is retried by the next check. A cleared condition re-arms the alert.
    Never raises. Returns the [(slug, kind, recipient)] sends started."""
    starts = []
    try:
        cat = _catalog(catalog)
        avg_map = monthly_sales_cached(cx, cat)
        retry_before = (datetime.now(timezone.utc)
                        - timedelta(seconds=SEND_RETRY_AFTER_SECONDS)).isoformat()
        for slug in sorted(set(slugs or ())):
            if not _lock_count(cx, slug):
                continue
            count = get_count(cx, slug)
            avg = float(avg_map.get(slug) or 0.0)
            for kind, cond in (("low", count <= low_stock_at(cat.get(slug), avg)),
                               ("month", month_alert_applies(cat.get(slug), avg)
                                and count < avg)):
                if not cond:
                    cx.execute("DELETE FROM stock_alerts WHERE slug=? AND kind=?", (slug, kind))
                    cx.execute("DELETE FROM stock_alert_sends WHERE slug=? AND kind=?",
                               (slug, kind))
                    continue
                cx.execute("INSERT INTO stock_alerts (slug, kind, fired_at) VALUES (?,?,?) "
                           "ON CONFLICT (slug, kind) DO NOTHING", (slug, kind, _now()))
                for to in _recipients():
                    got = cx.execute(
                        "INSERT INTO stock_alert_sends (slug, kind, recipient, state, "
                        "attempted_at) VALUES (?,?,?,'pending',?) "
                        "ON CONFLICT (slug, kind, recipient) DO NOTHING",
                        (slug, kind, to, _now())).rowcount == 1
                    if not got:
                        got = cx.execute(
                            "UPDATE stock_alert_sends SET state='pending', attempted_at=? "
                            "WHERE slug=? AND kind=? AND recipient=? AND (state='failed' "
                            "OR (state='pending' AND attempted_at < ?))",
                            (_now(), slug, kind, to, retry_before)).rowcount == 1
                    if got:
                        starts.append((slug, kind, to, count, avg,
                                       low_stock_at(cat.get(slug), avg)))
        cx.commit()
    except Exception as e:  # noqa: BLE001
        _safe_rollback(cx)
        print(f"[stock] alert check skipped: {e!r}", flush=True)
        return []
    for slug, kind, to, count, avg, low_at in starts:
        name = (cat.get(slug) or {}).get("name") or slug
        subject, body = _alert_text(kind, name, count, avg, low_at)
        _spawn(lambda s=slug, k=kind, t=to, su=subject, b=body: _deliver(s, k, t, su, b))
    return [(s, k, t) for s, k, t, _c, _a, _l in starts]


# ── console ────────────────────────────────────────────────────────────────────

def console_rows(cx, catalog=None) -> list:
    """Every counted product, lowest count first."""
    cat = _catalog(catalog)
    ensure_tables(cx)
    avg = monthly_sales_cached(cx, cat)
    rows = []
    for r in cx.execute("SELECT slug, count, updated_at FROM stock_counts").fetchall():
        slug, count, updated = r[0], int(r[1]), r[2]
        a = round(float(avg.get(slug) or 0.0), 2)
        rows.append({"slug": slug, "name": (cat.get(slug) or {}).get("name") or slug,
                     "count": count, "avg_monthly": a,
                     "low_at": low_stock_at(cat.get(slug), a),
                     "months_supply": (round(count / a, 1) if a > 0 else None),
                     "updated_at": updated})
    rows.sort(key=lambda x: (x["count"], x["name"].lower()))
    return rows


def last_set(cx, catalog=None):
    """The product whose count was entered most recently by hand, so Glen can keep his
    place on the shelf. Glen, 2026-11-06: "If the stock counts can show the name of the
    item inventoried last, that would help me keep my place." Order deductions carry an
    order id and are skipped. Returns {slug, name, count, logged_at} or None."""
    cat = _catalog(catalog)
    ensure_tables(cx)
    r = cx.execute("SELECT l.slug, l.new_count, l.logged_at FROM stock_log l "
                   "JOIN stock_counts c ON c.slug = l.slug "
                   "WHERE l.order_id IS NULL ORDER BY l.id DESC LIMIT 1").fetchone()
    if not r:
        return None
    return {"slug": r[0], "name": (cat.get(r[0]) or {}).get("name") or r[0],
            "count": int(r[1]), "logged_at": r[2]}


def countable_products(catalog=None) -> list:
    """[{slug, name}] a count can be entered for: on sale, shippable, not a bundle,
    not made to order."""
    cat = _catalog(catalog)
    out = []
    for slug, p in cat.items():
        if not isinstance(p, dict) or p.get("inactive") or p.get("info_only"):
            continue
        if p.get("service") or p.get("digital") or not countable(p, slug):
            continue
        out.append({"slug": slug, "name": p.get("name") or slug})
    out.sort(key=lambda x: x["name"].lower())
    return out
