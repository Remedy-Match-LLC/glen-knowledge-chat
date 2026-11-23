"""One statement per household per month: every delivery to its members.

Glen and Rae, 2026-11-22, for the Perdomo family: "One statement is even better."
Glen: "And that can itemize by which family member as well."

A household is the `households` record, and its members are the people tagged
`household:<slug>`. A delivery is a paid, not cancelled order for one of them. It is
dated by the owner's delivered date, or by the day it was paid in Hilo when no date is
set. Nothing here writes, emails or books anything: Rae opens and prints it.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

_HST = ZoneInfo("Pacific/Honolulu")
_MONTH_RE = re.compile(r"\d{4}-(0[1-9]|1[0-2])")


def _hst_day(iso):
    """The Hilo date of a stored UTC timestamp, YYYY-MM-DD, or '' when unreadable."""
    s = str(iso or "").strip()
    if not s:
        return ""
    try:
        t = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return s[:10]
    if t.tzinfo is None:              # stored timestamps are UTC (review round 2)
        t = t.replace(tzinfo=timezone.utc)
    return t.astimezone(_HST).date().isoformat()


def delivery_day(order):
    """The day an order counts as delivered on the statement."""
    d = str((order or {}).get("delivered_on") or "").strip()[:10]
    return d or _hst_day((order or {}).get("paid_at"))


def members(cx, slug):
    """[{id, name}] for the people tagged into this household, by name."""
    tag = f"household:{slug}"
    out = []
    for r in cx.execute("SELECT id, first_name, last_name, tags FROM people "
                        "WHERE tags LIKE ?", (f'%"{tag}"%',)).fetchall():
        try:
            tags = json.loads(r[3] or "[]")
        except (TypeError, ValueError):
            continue
        if tag not in tags:          # LIKE also matches a longer slug's prefix
            continue
        name = f"{r[1] or ''} {r[2] or ''}".strip() or f"Person {int(r[0])}"
        out.append({"id": int(r[0]), "name": name})
    return sorted(out, key=lambda m: (m["name"].lower(), m["id"]))


def _lines(items_json):
    try:
        items = json.loads(items_json or "[]")
    except (TypeError, ValueError):
        return []
    out = []
    for it in items if isinstance(items, list) else []:
        if not isinstance(it, dict):
            continue
        try:
            qty = int(it.get("qty") or 1)
        except (TypeError, ValueError):
            qty = 1
        out.append({"name": str(it.get("name") or it.get("slug") or "Item"), "qty": qty})
    return out


def build(cx, slug, month):
    """The statement for one household and one month (YYYY-MM).

    Returns {household, month, members, deliveries, total_cents}, where each delivery
    is {order_id, date, person_id, member, items: [{name, qty}], total_cents}, in date
    order. Raises ValueError for a bad month or an unknown household."""
    month = str(month or "").strip()
    if not _MONTH_RE.fullmatch(month):
        raise ValueError("month must be YYYY-MM")
    h = cx.execute("SELECT name FROM households WHERE slug=?", (slug,)).fetchone()
    if not h:
        raise ValueError(f"no household {slug!r}")
    people = members(cx, slug)
    names = {m["id"]: m["name"] for m in people}
    deliveries = []
    if names:
        marks = ",".join("?" for _ in names)
        rows = cx.execute(
            "SELECT id, person_id, items_json, total_cents, delivered_on, paid_at "
            f"FROM orders WHERE person_id IN ({marks}) AND pay_status='paid' "
            "AND COALESCE(status,'') <> 'cancelled' "
            "AND superseded_by_order_id IS NULL ORDER BY id",
            tuple(names)).fetchall()
        for r in rows:
            o = {"delivered_on": r[4], "paid_at": r[5]}
            day = delivery_day(o)
            if not day.startswith(month + "-"):
                continue
            deliveries.append({"order_id": int(r[0]), "date": day,
                               "person_id": int(r[1]), "member": names[int(r[1])],
                               "items": _lines(r[2]),
                               "total_cents": int(r[3] or 0)})
    deliveries.sort(key=lambda d: (d["date"], d["member"].lower(), d["order_id"]))
    return {"household": {"slug": slug, "name": h[0]}, "month": month,
            "members": people, "deliveries": deliveries,
            "total_cents": sum(d["total_cents"] for d in deliveries)}
