"""Progress at a glance in the local Biofield app's "Your authored tests" list.

Glen, 2026-10-23: "I want to be able to see at a glance on each line if the report is
completed, uploaded and emailed, as well as the invoice completed amount, uploaded,
emailed, paid." Plan: 00 System/pillar-inboxes/platform/
2026-10-23-authored-tests-progress-columns.md.

Where each fact comes from:

  Report done       biofield_narratives: a saved, non-empty narrative (Glen's ruling).
  Report uploaded   biofield_portal_published, written by Publish to Portal.
  Report emailed    biofield_test_notified.notified_at.
  Invoice amount    the production order's total_cents.
  Invoice uploaded  orders.portal_published.
  Invoice emailed   orders.invoice_sent_at.
  Paid              orders.pay_status and orders.paid_cents.

Which order belongs to a test: the order id stored in biofield_test_orders when the
invoice was made from that test. Only when no link is stored does the list fall back to
the old lookup by email (the client's newest order with a Biofield line), and that cell
is marked approximate, because a household or a client with two open orders can match
the wrong invoice.

All orders for the list come from ONE call to prod, never one per line.
"""
import datetime as _dt
import json as _json
import sqlite3
import urllib.parse
import urllib.request

LINK_TABLE = "biofield_test_orders"
BIOFIELD_SLUG = "biofield-analysis"
HST = _dt.timezone(_dt.timedelta(hours=-10))


# ── The stored test-to-order link (local SQLite) ─────────────────────────────

def ensure_schema(cx):
    cx.execute(f"CREATE TABLE IF NOT EXISTS {LINK_TABLE} ("
               "test_id TEXT PRIMARY KEY, order_id INTEGER NOT NULL, "
               "source TEXT NOT NULL DEFAULT 'invoice', linked_at TEXT NOT NULL)")


def link_order(cx, test_id, order_id, source="invoice", only_if_unlinked=False,
               resolve=None):
    """Remember that this test's invoice is this order. Returns (stored, reason).

    Refused when another test already holds this order: a household's second raise
    updates the same open order, and two tests trusting one order could each email it.
    With only_if_unlinked, refused when this test already has a link (the backfill
    must never overwrite one). Both checks run inside the write transaction.

    resolve(order_id) -> the order that stands for it now (see resolver). Two tests
    whose orders resolve to the same final order hold the same invoice, so the second
    is refused. Without it, only the same order number counts."""
    oid, tid = int(order_id), str(test_id)
    resolve = resolve or (lambda x: int(x))
    ensure_schema(cx)
    cx.commit()
    cx.execute("BEGIN IMMEDIATE")
    try:
        mine_final = resolve(oid)
        for other, o in cx.execute(f"SELECT test_id, order_id FROM {LINK_TABLE} "
                                   "WHERE test_id<>?", (tid,)).fetchall():
            if int(o) == oid or resolve(int(o)) == mine_final:
                cx.rollback()
                return False, (f"order #{oid} is already stored for test {other}"
                               + ("" if int(o) == oid else f" (through order #{int(o)})"))
        if only_if_unlinked:
            mine = cx.execute(f"SELECT order_id FROM {LINK_TABLE} WHERE test_id=?",
                              (tid,)).fetchone()
            if mine:
                cx.rollback()
                return False, f"test {tid} already has order #{mine[0]}"
        cx.execute(f"INSERT INTO {LINK_TABLE} (test_id, order_id, source, linked_at) "
                   "VALUES (?,?,?,?) ON CONFLICT(test_id) DO UPDATE SET "
                   "order_id=excluded.order_id, source=excluded.source, "
                   "linked_at=excluded.linked_at",
                   (tid, oid, source, _dt.datetime.now(_dt.timezone.utc).isoformat()))
        cx.commit()
        return True, ""
    except Exception:
        cx.rollback()
        raise


def _has_table(cx, name):
    return cx.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                      (name,)).fetchone() is not None


def _has_col(cx, table, col):
    return any(r[1] == col for r in cx.execute(f"PRAGMA table_info({table})").fetchall())


def stored_links(cx):
    """{test_id: order_id}. Reads only: a missing table is no links, never a write."""
    if not _has_table(cx, LINK_TABLE):
        return {}
    return {str(t): int(o) for t, o in
            cx.execute(f"SELECT test_id, order_id FROM {LINK_TABLE}").fetchall()}


def linked_order(cx, test_id):
    if not _has_table(cx, LINK_TABLE):
        return None
    r = cx.execute(f"SELECT order_id FROM {LINK_TABLE} WHERE test_id=?",
                   (str(test_id),)).fetchone()
    return int(r[0]) if r else None


def local_steps(cx):
    """{test_id: {narrative_at, uploaded_at, emailed_at, no_charge}} from the local DB.

    Reads only. Each table may not exist yet on a fresh machine."""
    out = {}

    def put(tid, key, val):
        out.setdefault(str(tid), {})[key] = val

    if _has_table(cx, "biofield_narratives"):
        for tid, at in cx.execute(
                "SELECT test_id, updated_at FROM biofield_narratives "
                "WHERE TRIM(COALESCE(narrative,''))<>''").fetchall():
            put(tid, "narrative_at", at or "")
    if _has_table(cx, "biofield_portal_published"):
        for tid, at in cx.execute(
                "SELECT test_id, updated_at FROM biofield_portal_published").fetchall():
            put(tid, "uploaded_at", at or "")
    if _has_table(cx, "biofield_test_notified"):
        for tid, at in cx.execute(
                "SELECT test_id, notified_at FROM biofield_test_notified").fetchall():
            put(tid, "emailed_at", at or "")
    if _has_table(cx, "biofield_auth_tests") and _has_col(cx, "biofield_auth_tests", "no_charge"):
        for i, nc in cx.execute("SELECT id, no_charge FROM biofield_auth_tests").fetchall():
            if nc:
                put("a" + str(i), "no_charge", True)
    return out


# ── Orders from prod, in one call ────────────────────────────────────────────

def _console():
    import os
    key = os.environ.get("CONSOLE_SECRET")
    if not key:
        return None, None
    return os.environ.get("PUBLIC_BASE_URL", "https://illtowell.com").rstrip("/"), key


def fetch_orders(order_ids=()):
    """(orders, error). ONE GET to prod for the whole list.

    Prod returns every order carrying a Biofield line (the email fallback's candidates)
    plus each order id asked for, and any order that replaced one of them. Read-only.
    """
    base, key = _console()
    if not base:
        return [], "no console key configured on this machine"
    ids = sorted({int(i) for i in order_ids or () if i})
    url = f"{base}/api/console/biofield-progress-orders"
    if ids:
        url += "?ids=" + urllib.parse.quote(",".join(str(i) for i in ids))
    try:
        req = urllib.request.Request(url, headers={"X-Console-Key": key})
        with urllib.request.urlopen(req, timeout=10) as r:
            body = _json.loads(r.read().decode() or "{}")
        if not body.get("ok"):
            return [], body.get("error") or "the order lookup failed"
        return body.get("orders") or [], None
    except Exception as e:  # noqa: BLE001 - the list must render without orders
        code = getattr(e, "code", None)
        if code == 401:
            return [], ("the console key this app started with is no longer valid: "
                        "restart the local server to pick up the current one")
        if code == 404:
            return [], "prod does not have the progress endpoint yet"
        return [], f"the order lookup failed ({type(e).__name__})"


def fetch_order(order_ids):
    """(orders, error) for the named orders and whatever replaced them, re-read now.
    One GET, whatever the count."""
    base, key = _console()
    if not base:
        return [], "no console key configured on this machine"
    if isinstance(order_ids, (int, str)):
        order_ids = [order_ids]
    ids = sorted({int(i) for i in order_ids if i})
    url = (f"{base}/api/console/biofield-progress-orders?only=1&ids="
           + urllib.parse.quote(",".join(str(i) for i in ids)))
    try:
        req = urllib.request.Request(url, headers={"X-Console-Key": key})
        with urllib.request.urlopen(req, timeout=10) as r:
            body = _json.loads(r.read().decode() or "{}")
        if not body.get("ok"):
            return [], body.get("error") or "the order lookup failed"
        return body.get("orders") or [], None
    except Exception as e:  # noqa: BLE001
        return [], f"the order lookup failed ({type(e).__name__})"


def follow(order_id, orders):
    """The order that stands for order_id now: itself, or what replaced it. None when
    the chain cannot be followed in `orders`."""
    by_id = {int(o["id"]): o for o in orders or [] if o.get("id") is not None}
    o, seen = by_id.get(int(order_id)), set()
    while o and o.get("superseded_by_order_id"):
        if int(o["id"]) in seen:
            return None
        seen.add(int(o["id"]))
        o = by_id.get(int(o["superseded_by_order_id"]))
    return o


def resolver(orders):
    """resolve(order_id) -> the id that stands for it now, following replacements.
    An order not in `orders`, or a broken chain, stands for itself."""
    def resolve(oid):
        o = follow(oid, orders)
        return int(o["id"]) if o else int(oid)
    return resolve


def send_to(order):
    """The address prod's orders.send_invoice emails: the order's email. (recipient_email
    is the client on a practitioner drop-ship, never the invoice's addressee.)"""
    return (order.get("email") or "").strip().lower()


def has_biofield_line(items):
    return any((it or {}).get("slug") == BIOFIELD_SLUG or (it or {}).get("source") == "biofield"
               for it in items or [])


def is_biofield_order(o):
    """The progress endpoint sends a "biofield" flag; the board's order rows send items."""
    return bool(o["biofield"]) if "biofield" in o else has_biofield_line(o.get("items"))


def match_order(test, link_id, orders):
    """(order, basis). basis is "link", "email", or None when nothing matched.

    A stored link always wins. If the linked order was replaced, the replacement is
    shown. A stored link whose order prod did not return gives a stub order with
    only the id, so the line says which order it is waiting on rather than guessing.
    Without a link, this is the old lookup: the client's newest order with a Biofield
    line, skipping replaced orders, cancelled included (as the Orders board search
    returns them).
    """
    by_id = {int(o["id"]): o for o in orders or [] if o.get("id") is not None}
    if link_id:
        o = by_id.get(int(link_id))
        seen = set()
        while o and o.get("superseded_by_order_id") and int(o["id"]) not in seen:
            seen.add(int(o["id"]))
            nxt = by_id.get(int(o["superseded_by_order_id"]))
            if not nxt:
                break
            o = nxt
        return (o or {"id": int(link_id), "missing": True}), "link"
    email = (test.get("email") or "").strip().lower()
    if not email:
        return None, None
    best = None
    for o in orders or []:
        if (o.get("email") or "").strip().lower() != email:
            continue
        if o.get("superseded_by_order_id"):
            continue
        if not is_biofield_order(o):
            continue
        if best is None or int(o["id"]) > int(best["id"]):
            best = o
    return (best, "email") if best else (None, None)


def paid_state(order):
    """("full"|"part"|"refunded"|"claimed"|"open", paid_cents), as prod decides it.

    Full is pay_status "paid". Part paid is the ledger's net payments (as the Orders
    board reads them) above zero and below the total, falling back to paid_cents for
    an order the endpoint sent no ledger figure for."""
    total = int(order.get("total_cents") or 0)
    paid = int(order.get("paid_cents") or 0)
    status = (order.get("pay_status") or "").strip().lower()
    ledger = order.get("ledger_paid_cents")
    got = int(ledger) if ledger is not None else paid
    if status == "paid":                    # prod's word, whatever the amounts say
        return "full", got
    if ledger is not None and int(ledger) <= 0 and int(order.get("ledger_refund_cents") or 0) > 0:
        return "refunded", 0
    if 0 < got < total:
        return "part", got
    if status == "claimed":
        return "claimed", 0
    return "open", got


def build(authored, local, links, orders, orders_error=None):
    """One progress dict per authored test, in list order."""
    out = []
    # Who holds each order, counted on the order each link resolves to now: a test
    # stored on a replaced order and a test stored on its replacement hold one invoice.
    resolve = resolver(orders)
    holders = {}
    for _tid, _oid in (links or {}).items():
        holders.setdefault(resolve(_oid), set()).add(_tid)
    for t in authored or []:
        tid = str(t.get("test_id") or "")
        loc = (local or {}).get(tid) or {}
        p = {"test_id": tid,
             "report_done": loc.get("narrative_at") or "",
             "report_uploaded": loc.get("uploaded_at") or "",
             "report_emailed": loc.get("emailed_at") or "",
             "no_charge": bool(loc.get("no_charge")),
             "orders_error": orders_error or "",
             "order_id": None, "basis": None, "missing": False, "cancelled": False,
             "amount_cents": None, "invoice_uploaded": False, "invoice_emailed": "",
             "paid": "open", "paid_cents": 0, "paid_at": "", "send_to": "",
             "recipient_email": "", "shared_with": []}
        link_id = (links or {}).get(tid)
        if orders_error and not link_id:
            p["all_done"] = False
            out.append(p)
            continue
        o, basis = match_order(t, link_id, orders)
        if o:
            p["order_id"] = int(o["id"])
            # One order stored for two tests (a household): neither line may trust it.
            shared = (sorted(holders.get(resolve(link_id), set()) - {tid})
                      if link_id else [])
            if shared:
                basis = "shared"
                p["shared_with"] = shared
            p["basis"] = basis
            if o.get("missing"):
                p["missing"] = True
            else:
                p["cancelled"] = (o.get("status") or "") == "cancelled"
                p["amount_cents"] = int(o.get("total_cents") or 0)
                p["invoice_uploaded"] = bool(o.get("portal_published"))
                p["invoice_emailed"] = o.get("invoice_sent_at") or ""
                p["paid"], p["paid_cents"] = paid_state(o)
                p["paid_at"] = o.get("paid_at") or ""
                p["send_to"] = send_to(o)
                p["recipient_email"] = (o.get("recipient_email") or "").strip().lower()
        p["all_done"] = is_done(p)
        out.append(p)
    return out


def invoice_not_needed(p):
    """A no-charge test with no invoice has no invoice steps to finish."""
    return p.get("no_charge") and not p.get("order_id")


def is_done(p):
    """Every step done: the line turns grey and stays visible."""
    report = bool(p.get("report_done") and p.get("report_uploaded") and p.get("report_emailed"))
    if not report:
        return False
    if invoice_not_needed(p):
        return True
    if not p.get("order_id") or p.get("missing") or p.get("cancelled") or p.get("orders_error"):
        return False
    if p.get("basis") != "link":            # a guessed or shared order never greys a line
        return False
    # A paid order needs no invoice email.
    return bool(p.get("invoice_uploaded") and p.get("paid") == "full")


def short_date(ts):
    """'21 Oct' in Hawai'i time, from a UTC timestamp. '' when unreadable."""
    s = str(ts or "").strip()
    if not s:
        return ""
    try:
        d = _dt.datetime.fromisoformat(s.replace("Z", "+00:00").replace(" ", "T", 1))
    except ValueError:
        return s[:10]
    if d.tzinfo is None:
        d = d.replace(tzinfo=_dt.timezone.utc)
    d = d.astimezone(HST)
    return f"{d.day} {d.strftime('%b')}"


def open_ro(path):
    """A read-only connection, for callers that must never write."""
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)  # raw-sqlite-ok: the local Biofield app's own DB, opened read-only


# ── Backfill: propose a stored link for tests made before links existed ──────

def _hst_day(ts):
    s = str(ts or "").strip()
    if not s:
        return ""
    try:
        d = _dt.datetime.fromisoformat(s.replace("Z", "+00:00").replace(" ", "T", 1))
    except ValueError:
        return s[:10]
    if d.tzinfo is None:
        d = d.replace(tzinfo=_dt.timezone.utc)
    return d.astimezone(HST).date().isoformat()


def _day_before(day):
    try:
        return (_dt.date.fromisoformat(day) - _dt.timedelta(days=1)).isoformat()
    except ValueError:
        return day


def propose_links(authored, links, orders_by_email):
    """Proposals for tests with no stored link. Pure: writes nothing.

    A test's window runs from the day before its date up to the day before the next
    test for the same email, in Hawai'i time. The day's slack is measured: orders 231,
    233 and 269 were raised the evening before their tests' dates, Hawai'i time.
    Its candidates are that client's orders with a Biofield line, neither replaced
    nor cancelled, made in the window. Exactly one candidate is a proposal. None is
    "none" (often a caregiver who paid under their own email). Two or more is
    "ambiguous" and is never written: Glen picks. An order proposed for two tests is
    a "conflict" for both, and is never written either.

    orders_by_email: {email: [orders]} or {email: None} when that lookup failed.
    Returns a list of {test_id, status, order_id, candidates, reason}.
    """
    by_email = {}
    for t in authored or []:
        e = (t.get("email") or "").strip().lower()
        by_email.setdefault(e, []).append(t)
    out = []
    for t in authored or []:
        tid = str(t.get("test_id") or "")
        if tid in (links or {}):
            out.append({"test_id": tid, "status": "linked", "order_id": links[tid],
                        "candidates": [], "reason": "already stored"})
            continue
        email = (t.get("email") or "").strip().lower()
        day = str(t.get("date") or "")[:10]
        if not email or not day:
            out.append({"test_id": tid, "status": "none", "order_id": None, "candidates": [],
                        "reason": "no email or no test date"})
            continue
        orders = orders_by_email.get(email)
        if orders is None:
            out.append({"test_id": tid, "status": "error", "order_id": None, "candidates": [],
                        "reason": "order lookup failed"})
            continue
        later = sorted(str(o.get("date") or "")[:10] for o in by_email.get(email, [])
                       if str(o.get("date") or "")[:10] > day)
        start = _day_before(day)
        end = _day_before(later[0]) if later else "9999-12-31"
        same_day = [o for o in by_email.get(email, [])
                    if str(o.get("date") or "")[:10] == day and o is not t]
        cands = sorted(int(o["id"]) for o in orders
                       if (o.get("email") or "").strip().lower() == email
                       and not o.get("superseded_by_order_id")
                       and (o.get("status") or "") != "cancelled"
                       and is_biofield_order(o)
                       and start <= _hst_day(o.get("created_at")) < end)
        if same_day and cands:
            out.append({"test_id": tid, "status": "ambiguous", "order_id": None,
                        "candidates": cands, "reason": "two tests for this email on one day"})
        elif len(cands) == 1:
            out.append({"test_id": tid, "status": "propose", "order_id": cands[0],
                        "candidates": cands, "reason": "only Biofield order in the window"})
        elif cands:
            out.append({"test_id": tid, "status": "ambiguous", "order_id": None,
                        "candidates": cands, "reason": "several Biofield orders in the window"})
        else:
            out.append({"test_id": tid, "status": "none", "order_id": None, "candidates": [],
                        "reason": "no Biofield order for this email in the window"})
    taken = {}
    for p in out:
        if p["status"] in ("propose", "linked"):
            taken.setdefault(p["order_id"], []).append(p)
    for oid, ps in taken.items():
        if len(ps) > 1:
            for p in ps:
                if p["status"] == "propose":
                    p["status"], p["reason"] = "conflict", f"order #{oid} also fits another test"
                    p["order_id"] = None
                    p["candidates"] = [oid]
    return out


def apply_links(cx, proposals, out=print, resolve=None):
    """Write the 'propose' rows only, and only where the test still has no link and
    no other test holds the order: both re-checked inside the write transaction, so
    a link made after the dry run is never overwritten. resolve follows replaced
    orders (see resolver), so an order another test reaches through its chain is
    refused too. Returns how many were stored, each read back."""
    n = 0
    for p in proposals:
        if p["status"] != "propose":
            continue
        ok, why = link_order(cx, p["test_id"], p["order_id"], source="backfill",
                             only_if_unlinked=True, resolve=resolve)
        if not ok:
            out(f"skipped {p['test_id']}: {why}")
            continue
        if linked_order(cx, p["test_id"]) != int(p["order_id"]):
            raise RuntimeError(f"{p['test_id']}: link did not land")
        n += 1
    return n
