"""Bottle counts on the shelf, per product (Glen, 2026-11-06).

Plan: platform/plans/2026-11-06-stock-counts-in-the-app.md. Every rule below is his.

- Each product may carry a count of bottles on the shelf. A product with NO count row
  stays on sale: a missing count never blocks a sale, so nothing goes dark on day one.
- At 0 or below, the product is refused exactly like a hand-set `out_of_stock` flag, by
  the one predicate (practitioner_portal.waitlist_block), with the line
  "{name} is out of stock." A hand-set flag still wins over a count.
- A bottle leaves the count when an order is PAID. A cancelled order puts it back.
  GrooveKart sends no paid signal, so its orders come off the count on arrival.
- Bundles take one bottle (times the component qty) from each product inside them.
- Made-to-order products (`made_to_order` in the catalog) are never counted.
- Every change is one row in `stock_log`: slug, delta, new count, who, when, why, order.
- Each order moves a slug at most once per direction (`stock_order_moves` primary key),
  so a payment or a cancel recorded twice never double-counts.
- Glen and Rae are emailed when a product falls to LOW_STOCK_AT bottles or fewer, and
  when it falls below one month of average sales. Once per crossing.

The whole feature sits behind STOCK_COUNTS_ENABLED (default off). Off, nothing here reads
or writes the database and every caller behaves exactly as before. Turning it on with no
counts entered changes nothing either: no count means on sale, and nothing to decrement.

Callers pass `cx` where they have one. The predicate has none, so it opens its own
connection to the path app.py registers (set_db_path_fn), and caches the set of
sold-out slugs for ZERO_CACHE_SECONDS.
"""
from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dashboard import db

FLAG = "STOCK_COUNTS_ENABLED"
LOW_STOCK_AT = 3                  # Glen, 2026-11-06: "yes" to 3 bottles or fewer
AVG_WINDOW_DAYS = 365             # average monthly sales over the last 12 months
DEDUPE_DAYS = 14                  # a history line within 14 days of an app order is one sale
ZERO_CACHE_SECONDS = 5.0          # how stale the sold-out set may be in another worker
AVG_CACHE_SECONDS = 600.0
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


def _invalidate():
    with _LOCK:
        _ZERO["path"] = None
        _AVG["path"] = None


# ── schema ──────────────────────────────────────────────────────────────────────

def init_tables(cx):
    cx.execute("""CREATE TABLE IF NOT EXISTS stock_counts (
        slug       TEXT PRIMARY KEY,
        count      INTEGER NOT NULL,
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
    # One move per (order, slug, direction): the idempotency key. direction is
    # 'out' (paid, or GrooveKart arrival) or 'back' (cancelled).
    cx.execute("""CREATE TABLE IF NOT EXISTS stock_order_moves (
        order_id  INTEGER NOT NULL,
        slug      TEXT NOT NULL,
        direction TEXT NOT NULL,
        qty       INTEGER NOT NULL,
        moved_at  TEXT NOT NULL,
        PRIMARY KEY (order_id, slug, direction))""")
    # A row means the alert has fired for this crossing. Deleted when the condition
    # clears, which re-arms it.
    cx.execute("""CREATE TABLE IF NOT EXISTS stock_alerts (
        slug     TEXT NOT NULL,
        kind     TEXT NOT NULL,
        fired_at TEXT NOT NULL,
        PRIMARY KEY (slug, kind))""")
    # GrooveKart product names that matched no catalog product, for the console page.
    cx.execute("""CREATE TABLE IF NOT EXISTS stock_unmatched_names (
        name       TEXT PRIMARY KEY,
        times      INTEGER NOT NULL DEFAULT 0,
        qty        INTEGER NOT NULL DEFAULT 0,
        first_seen TEXT NOT NULL,
        last_seen  TEXT NOT NULL,
        last_ref   TEXT NOT NULL DEFAULT '')""")
    cx.commit()


# ── the predicate ───────────────────────────────────────────────────────────────

def countable(p) -> bool:
    """Whether a product can carry a count. Made-to-order never does (Glen, question 3).
    A bundle does not either: its components are counted."""
    p = p if isinstance(p, dict) else {}
    return not p.get("made_to_order") and not p.get("bundle")


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
    if not enabled() or not slug or not countable(p):
        return False
    return slug in zero_slugs()


# ── reading ─────────────────────────────────────────────────────────────────────

def get_count(cx, slug):
    r = cx.execute("SELECT count FROM stock_counts WHERE slug=?", (slug,)).fetchone()
    return None if r is None else int(r[0])


def all_counts(cx) -> dict:
    return {r[0]: int(r[1]) for r in cx.execute(
        "SELECT slug, count FROM stock_counts").fetchall()}


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


def set_count(cx, slug, count, *, who, reason, catalog=None):
    """Set a product's count (a shelf count, a delivery). Logs the change. Returns
    {"slug", "count", "delta"}. Raises ValueError on a bad slug or count."""
    cat = _catalog(catalog)
    slug = (slug or "").strip()
    p = cat.get(slug)
    if not p:
        raise ValueError(f"unknown product: {slug}")
    if not countable(p):
        raise ValueError(f"{p.get('name') or slug} is not counted "
                         f"({'a bundle' if p.get('bundle') else 'made to order'})")
    try:
        n = int(count)
    except (TypeError, ValueError):
        raise ValueError("count must be a whole number")
    if n < 0:
        raise ValueError("count cannot be negative")
    reason = (reason or "").strip()
    if not reason:
        raise ValueError("a reason is required")
    init_tables(cx)
    old = get_count(cx, slug)
    now = _now()
    if old is None:
        cx.execute("INSERT INTO stock_counts (slug, count, updated_at) VALUES (?,?,?)",
                   (slug, n, now))
    else:
        cx.execute("UPDATE stock_counts SET count=?, updated_at=? WHERE slug=?",
                   (n, now, slug))
    delta = n - (old or 0)
    _append_log(cx, slug, delta, n, who, reason)
    cx.commit()
    _invalidate()
    check_alerts(cx, [slug], catalog=cat)
    return {"slug": slug, "count": n, "delta": delta}


def _move(cx, order_id, slug, qty, direction, *, who, reason):
    """Claim (order, slug, direction) and apply it. Returns the new count, or None when
    this move was already made (or the slug has no count). Caller commits."""
    if get_count(cx, slug) is None:
        return None
    cur = cx.execute(
        "INSERT INTO stock_order_moves (order_id, slug, direction, qty, moved_at) "
        "VALUES (?,?,?,?,?) ON CONFLICT (order_id, slug, direction) DO NOTHING",
        (int(order_id), slug, direction, int(qty), _now()))
    if cur.rowcount != 1:
        return None
    delta = -int(qty) if direction == "out" else int(qty)
    cx.execute("UPDATE stock_counts SET count = count + ?, updated_at=? WHERE slug=?",
               (delta, _now(), slug))
    new = get_count(cx, slug)
    _append_log(cx, slug, delta, new, who, reason, order_id)
    return new


def _safe_rollback(cx):
    try:
        cx.rollback()
    except Exception:  # noqa: BLE001
        pass


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
                try:
                    cq = max(1, int((comp or {}).get("qty") or 1))
                except (TypeError, ValueError):
                    cq = 1
                _add(cs, qty * cq, seen | {cs})
            return
        if p and p.get("made_to_order"):
            return
        out[slug] = out.get(slug, 0) + qty

    for it in (items or []):
        if not isinstance(it, dict):
            continue
        slug = str(it.get("slug") or "").strip()
        if not slug or it.get("kind") == "membership" or slug.startswith("membership:"):
            continue
        try:
            qty = int(it.get("qty") or 0)
        except (TypeError, ValueError):
            qty = 0
        if qty > 0:
            _add(slug, qty, {slug})
    return out


def _order_items(cx, order_id):
    r = cx.execute("SELECT items_json FROM orders WHERE id=?", (int(order_id),)).fetchone()
    if not r:
        return []
    try:
        items = json.loads(r[0] or "[]")
    except (TypeError, ValueError):
        return []
    return items if isinstance(items, list) else []


def on_order_paid(cx, order_id, catalog=None):
    """Take a paid order's bottles off the count, once. Never raises: payment must
    never fail because stock keeping did. Returns {slug: new_count} for moves made."""
    if not enabled():
        return {}
    try:
        cat = _catalog(catalog)
        init_tables(cx)
        units = units_for_items(_order_items(cx, order_id), cat)
        moved = {}
        for slug, qty in sorted(units.items()):
            new = _move(cx, order_id, slug, qty, "out", who="order",
                        reason=f"paid order #{order_id}")
            if new is not None:
                moved[slug] = new
        cx.commit()
        if moved:
            _invalidate()
            check_alerts(cx, list(moved), catalog=cat)
        return moved
    except Exception as e:  # noqa: BLE001
        _safe_rollback(cx)
        print(f"[stock] paid-order decrement skipped for #{order_id}: {e!r}", flush=True)
        return {}


def on_order_cancelled(cx, order_id, catalog=None):
    """Put back every bottle this order took off, once. Reads the moves log rather
    than items_json, so an order edited after payment returns what it actually took.
    Never raises. Returns {slug: new_count}."""
    if not enabled():
        return {}
    try:
        init_tables(cx)
        rows = cx.execute(
            "SELECT slug, qty FROM stock_order_moves WHERE order_id=? AND direction='out'",
            (int(order_id),)).fetchall()
        moved = {}
        for slug, qty in sorted((r[0], int(r[1])) for r in rows):
            new = _move(cx, order_id, slug, qty, "back", who="order",
                        reason=f"cancelled order #{order_id}")
            if new is not None:
                moved[slug] = new
        cx.commit()
        if moved:
            _invalidate()
            check_alerts(cx, list(moved), catalog=catalog)
        return moved
    except Exception as e:  # noqa: BLE001
        _safe_rollback(cx)
        print(f"[stock] cancel restore skipped for #{order_id}: {e!r}", flush=True)
        return {}


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
        try:
            qty = int(float(item.get("product_quantity") or item.get("quantity") or 1))
        except (TypeError, ValueError):
            qty = 1
        out.append((name, max(1, qty)))
    return out


def record_unmatched(cx, name, qty, ref):
    now = _now()
    cur = cx.execute(
        "UPDATE stock_unmatched_names SET times=times+1, qty=qty+?, last_seen=?, last_ref=? "
        "WHERE name=?", (int(qty), now, str(ref or ""), name))
    if cur.rowcount == 0:
        cx.execute(
            "INSERT INTO stock_unmatched_names (name, times, qty, first_seen, last_seen, "
            "last_ref) VALUES (?,?,?,?,?,?) ON CONFLICT (name) DO NOTHING",
            (name, 1, int(qty), now, now, str(ref or "")))


def list_unmatched(cx) -> list:
    keys = ("name", "times", "qty", "first_seen", "last_seen", "last_ref")
    return [dict(zip(keys, tuple(r))) for r in cx.execute(
        "SELECT name, times, qty, first_seen, last_seen, last_ref "
        "FROM stock_unmatched_names ORDER BY last_seen DESC").fetchall()]


def on_groovekart_order(cx, order_id, products, catalog=None, ref=""):
    """A GrooveKart order comes off the count when it arrives: GrooveKart never says
    when it is paid. Names are matched with practitioner_portal.name_to_slug. A name
    that matches nothing is recorded for the console. Total: never raises.
    `order_id` may be None (the order row was not written); then names are still
    matched and recorded, but nothing is decremented, since there is no key to make
    a retry idempotent."""
    if not enabled():
        return {}
    try:
        from dashboard.practitioner_portal import name_to_slug
        cat = _catalog(catalog)
        init_tables(cx)
        items, seen_unmatched = [], False
        for name, qty in groovekart_lines(products):
            slug = name_to_slug(name, cat)
            if slug:
                items.append({"slug": slug, "qty": qty})
            elif order_id is None or not _already_moved(cx, order_id):
                record_unmatched(cx, name, qty, ref)
                seen_unmatched = True
        moved = {}
        if order_id is not None:
            for slug, qty in sorted(units_for_items(items, cat).items()):
                new = _move(cx, order_id, slug, qty, "out", who="groovekart",
                            reason=f"GrooveKart order {ref or order_id}")
                if new is not None:
                    moved[slug] = new
            # A marker so a webhook retry does not count its unmatched names twice.
            cx.execute(
                "INSERT INTO stock_order_moves (order_id, slug, direction, qty, moved_at) "
                "VALUES (?,?,?,?,?) ON CONFLICT (order_id, slug, direction) DO NOTHING",
                (int(order_id), "", "gk-seen", 0, _now()))
        cx.commit()
        if moved:
            _invalidate()
            check_alerts(cx, list(moved), catalog=cat)
        return moved if not seen_unmatched else dict(moved, _unmatched=True)
    except Exception as e:  # noqa: BLE001
        _safe_rollback(cx)
        print(f"[stock] GrooveKart decrement skipped for {ref or order_id}: {e!r}", flush=True)
        return {}


def _already_moved(cx, order_id):
    return cx.execute(
        "SELECT 1 FROM stock_order_moves WHERE order_id=? AND direction='gk-seen'",
        (int(order_id),)).fetchone() is not None


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

    - `orders` lines: slug + qty. Counted when paid, or shipped/delivered/done, and not
      cancelled. GrooveKart orders carry names only: matched with name_to_slug, 1 each.
    - `purchase_history` lines (FileMaker 'fmp', older 'groovekart'): no qty, 1 each.

    A purchase_history line with the same lowercase email and slug as an `orders` line
    within DEDUPE_DAYS is the same sale, and counts once. Bundles count against their
    components. Unmatched lines and made-to-order products are left out."""
    from dashboard.practitioner_portal import name_to_slug
    cat = _catalog(catalog)
    today = (now or datetime.now(timezone.utc)).date()
    start = today - timedelta(days=AVG_WINDOW_DAYS)
    cutoff = (start - timedelta(days=DEDUPE_DAYS)).isoformat()
    order_lines = []            # (email, slug, day, qty)
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
            try:
                items = json.loads(items_json or "[]")
            except (TypeError, ValueError):
                items = []
            for it in (items if isinstance(items, list) else []):
                if not isinstance(it, dict):
                    continue
                slug = str(it.get("slug") or "").strip()
                if not slug and gk:
                    slug = name_to_slug(str(it.get("name") or ""), cat) or ""
                try:
                    qty = int(it.get("qty") or (1 if gk else 0))
                except (TypeError, ValueError):
                    qty = 0
                if slug and qty > 0 and d:
                    order_lines.append(((email or "").strip().lower(), slug, d, qty))
    seen = {}
    for em, slug, d, _q in order_lines:
        seen.setdefault((em, slug), []).append(d)
    totals = {}

    def _count(slug, qty, d):
        if d < start or d > today:
            return
        for s, n in units_for_items([{"slug": slug, "qty": qty}], cat).items():
            if s in cat:
                totals[s] = totals.get(s, 0) + n

    for em, slug, d, qty in order_lines:
        _count(slug, qty, d)
    if _table_exists(cx, "purchase_history"):
        for r in cx.execute(
                "SELECT email, slug, purchased_at FROM purchase_history "
                "WHERE source IN ('fmp','groovekart') AND purchased_at >= ?",
                (cutoff,)).fetchall():
            em, slug, at = (str(r[0] or "").strip().lower(), str(r[1] or ""), r[2])
            d = _day(at)
            if not d or not slug:
                continue
            if any(abs((d - od).days) <= DEDUPE_DAYS for od in seen.get((em, slug), ())):
                continue
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


def _send_all(subject, body):
    from dashboard import inbox
    for to in _recipients():
        try:
            inbox.send_email(to, subject, body)
        except Exception as e:  # noqa: BLE001
            print(f"[stock] alert to {to} failed: {e!r}", flush=True)


def _notify(subject, body):
    """Sent in the background: an order must never wait on, or fail on, mail. Under
    pytest inbox.send_email sends nothing (PYTEST_CURRENT_TEST guard)."""
    threading.Thread(target=_send_all, args=(subject, body), daemon=True).start()


def _alert_text(kind, name, count, avg):
    if kind == "low":
        return (f"Low stock: {name}, {count} left",
                f"{name} is down to {count} bottle{'s' if count != 1 else ''}.\n\n"
                f"The alert goes out at {LOW_STOCK_AT} or fewer. "
                f"About {avg:.1f} sell in a month.\n\nStock page: {CONSOLE_URL}")
    return (f"Under one month of {name}: {count} left",
            f"{name} has {count} bottle{'s' if count != 1 else ''} left. "
            f"About {avg:.1f} sell in a month, so this is less than one month's supply.\n\n"
            f"Stock page: {CONSOLE_URL}")


def check_alerts(cx, slugs, catalog=None):
    """Fire each alert once per crossing: low (count <= LOW_STOCK_AT) and one month
    (count < average monthly sales). An alert re-arms when its condition clears.
    Never raises. Returns the [(slug, kind)] that fired."""
    fired = []
    try:
        cat = _catalog(catalog)
        avg_map = monthly_sales_cached(cx, cat)
        for slug in sorted(set(slugs or ())):
            count = get_count(cx, slug)
            if count is None:
                continue
            avg = float(avg_map.get(slug) or 0.0)
            for kind, cond in (("low", count <= LOW_STOCK_AT),
                               ("month", avg > 0 and count < avg)):
                if cond:
                    cur = cx.execute(
                        "INSERT INTO stock_alerts (slug, kind, fired_at) VALUES (?,?,?) "
                        "ON CONFLICT (slug, kind) DO NOTHING", (slug, kind, _now()))
                    if cur.rowcount == 1:
                        fired.append((slug, kind, count, avg))
                else:
                    cx.execute("DELETE FROM stock_alerts WHERE slug=? AND kind=?",
                               (slug, kind))
        cx.commit()
    except Exception as e:  # noqa: BLE001
        _safe_rollback(cx)
        print(f"[stock] alert check skipped: {e!r}", flush=True)
        return []
    for slug, kind, count, avg in fired:
        name = (cat.get(slug) or {}).get("name") or slug
        _notify(*_alert_text(kind, name, count, avg))
    return [(s, k) for s, k, _c, _a in fired]


# ── console ────────────────────────────────────────────────────────────────────

def console_rows(cx, catalog=None) -> list:
    """Every counted product, lowest count first."""
    cat = _catalog(catalog)
    init_tables(cx)
    avg = monthly_sales_cached(cx, cat)
    rows = []
    for r in cx.execute("SELECT slug, count, updated_at FROM stock_counts").fetchall():
        slug, count, updated = r[0], int(r[1]), r[2]
        a = round(float(avg.get(slug) or 0.0), 2)
        rows.append({"slug": slug, "name": (cat.get(slug) or {}).get("name") or slug,
                     "count": count, "avg_monthly": a,
                     "months_supply": (round(count / a, 1) if a > 0 else None),
                     "updated_at": updated})
    rows.sort(key=lambda x: (x["count"], x["name"].lower()))
    return rows


def countable_products(catalog=None) -> list:
    """[{slug, name}] a count can be entered for: on sale, shippable, not a bundle,
    not made to order."""
    cat = _catalog(catalog)
    out = []
    for slug, p in cat.items():
        if not isinstance(p, dict) or p.get("inactive") or p.get("info_only"):
            continue
        if p.get("service") or p.get("digital") or not countable(p):
            continue
        out.append({"slug": slug, "name": p.get("name") or slug})
    out.sort(key=lambda x: x["name"].lower())
    return out
