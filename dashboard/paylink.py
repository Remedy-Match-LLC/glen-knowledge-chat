"""Rae's pay-link sender: find a client's unpaid app orders, write the pay-link email.

Glen, 2026-11-22: "Rae needs a process for sending a payment link to somebody." Rae types
a name, sees that client's unpaid orders (and their household's), and one button opens a
ready email in her own mail. She sends it herself, then clicks "Mark sent". Nothing here
sends mail. App orders only for now (Glen, 2026-11-22): FileMaker and QuickBooks invoices
have no sent date here.

The email wording is Glen's, approved in the platform tab 2026-11-22/23: subject "Your
invoice from Remedy Match", signed "Rae & Dr. Glen". It carries the ship-to address so the
client can confirm it. Glen, 2026-11-23: "If we don't have their shipping address, ask them
for it. If they pick up locally, let them know that plan." Pickup is at Riverbend House.
"""
from __future__ import annotations

import json

SUBJECT = "Your invoice from Remedy Match"
PICKUP_PLACE = "Riverbend House, 351 Wailuku Drive in Hilo"
_OPEN_STATUSES = ("proposed", "confirmed")


def _addr(order):
    a = order.get("address")
    if a is None:
        try:
            a = json.loads(order.get("address_json") or "{}")
        except (TypeError, ValueError):
            a = {}
    return a if isinstance(a, dict) else {}


def ship_to(order, physical_units):
    """How this order reaches the client: {"kind": "none"|"pickup"|"ship"|"missing",
    "lines": [...]}. "none" means nothing ships (a service on its own)."""
    if not physical_units:
        return {"kind": "none", "lines": []}
    if (order.get("channel") or "") == "pickup":
        return {"kind": "pickup", "lines": [PICKUP_PLACE]}
    a = _addr(order)
    street = " ".join(s for s in (str(a.get("street") or "").strip(),
                                  str(a.get("address2") or "").strip()) if s)
    city = str(a.get("city") or "").strip()
    state = str(a.get("state") or "").strip()
    zp = str(a.get("zip") or "").strip()
    if not (street and city):
        return {"kind": "missing", "lines": []}
    place = ", ".join(s for s in (city, " ".join(s for s in (state, zp) if s)) if s)
    name = str(a.get("name") or order.get("name") or "").strip()
    lines = [s for s in (name, street, place) if s]
    country = str(a.get("country") or "").strip()
    if country and country.upper() not in ("US", "USA", "UNITED STATES"):
        lines.append(country)
    return {"kind": "ship", "lines": lines}


def first_name(order):
    n = str(order.get("name") or "").strip()
    return n.split()[0] if n else ""


def money(cents):
    return f"${int(cents or 0) / 100:,.2f}"


def email(order, link, owed_cents, ship):
    """(subject, body) for the pay-link email. Each sentence is its own paragraph."""
    fn = first_name(order)
    parts = [f"Aloha {fn}," if fn else "Aloha,",
             f"Your invoice for {money(owed_cents)} is ready.",
             f"You can view and pay it here: {link}",
             "The page takes a card, Zelle or Wise."]
    kind = ship.get("kind")
    if kind == "ship":
        parts.append("We'll send your order to:")
        parts.append("\n".join(ship["lines"]))
        parts.append("If it should go somewhere else, reply with the new address.")
    elif kind == "pickup":
        parts.append(f"You can arrange to pick up your order at {PICKUP_PLACE}.")
    elif kind == "missing":
        parts.append("Please reply with the address where you'd like your order sent.")
    if kind != "none":              # nothing ships: the order line would not fit
        parts.append("We'll get your order moving as soon as your payment comes through.")
    parts.append("Mahalo,\n\nRae & Dr. Glen")
    return SUBJECT, "\n\n".join(parts)


def state(order):
    """One plain state for Rae and Glen: not sent yet, sent and waiting, or claimed."""
    if (order.get("pay_status") or "") == "claimed":
        return "claimed"
    if str(order.get("invoice_sent_at") or "").strip():
        return "sent"
    return "not_sent"


def is_open(order):
    """An unpaid, live app order that owes something."""
    return order.get("status") in _OPEN_STATUSES \
        and (order.get("pay_status") or "unpaid") != "paid" \
        and not order.get("superseded_by_order_id") \
        and int(order.get("total_cents") or 0) > 0


def _household_emails(cx, emails):
    """Emails of everyone sharing a household with these emails. [] without a people table."""
    emails = {e.strip().lower() for e in emails if (e or "").strip()}
    if not emails:
        return set()
    try:
        rows = cx.execute("SELECT lower(trim(coalesce(email,''))), tags FROM people "
                          "WHERE coalesce(tags,'') LIKE '%household:%'").fetchall()
    except Exception:  # noqa: BLE001 - no people table here
        try:
            cx.rollback()
        except Exception:  # noqa: BLE001
            pass
        return set()
    by_tag, mine = {}, set()
    for e, tags in rows:
        try:
            tl = [t for t in json.loads(tags or "[]") if str(t).startswith("household:")]
        except (TypeError, ValueError):
            continue
        for t in tl:
            by_tag.setdefault(t, set()).add(e)
            if e in emails:
                mine.add(t)
    out = set()
    for t in mine:
        out |= by_tag.get(t, set())
    out.discard("")
    return out - emails


def find(cx, q, search):
    """Unpaid app orders for the client a search names, plus their household's.

    search(cx, q) is orders.search_client_orders. Passing it in keeps this module free of the app's import chain."""
    found = [o for o in search(cx, q) if o.get("search_match") != "ship_to"]
    seen = {int(o["id"]) for o in found}
    for e in sorted(_household_emails(cx, [o.get("email") or "" for o in found])):
        for o in search(cx, e):
            if int(o["id"]) not in seen:
                seen.add(int(o["id"]))
                o["household"] = True
                found.append(o)
    out = [o for o in found if is_open(o)]
    out.sort(key=lambda o: str(o.get("created_at") or ""), reverse=True)
    return out
