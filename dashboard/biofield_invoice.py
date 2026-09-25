"""Assemble a client's hand-off invoice from the authored Biofield intake and raise it
on prod (Orders board). Pure line-assembly + injected prod calls; mirrors
biofield_fee.py. Prod is the pricing authority — this module sends [{slug,qty}] only.

"Hand-off" is the workflow step where Glen hands an authored analysis to Rae to
publish and invoice. It says nothing about delivery: the invoice carries the
Biofield Analysis fee (a service, no bottle, so it books no box) plus the physical
remedy lines, which prod quotes for shipping. Whether the client collects in person
is a separate, per-client choice made on the console — never assumed here.
"""
import json as _json
import os
import urllib.parse
import urllib.request

BIOFIELD_SLUG = "biofield-analysis"

DEFAULT_INVOICE_NOTE = "Biofield Analysis and remedies. Payable by Credit Card or Zelle."


def terrain_note(phase, location=""):
    """One-line terrain-phase note for the order (BSI 'phase P' + spoken location).
    '' when no phase was read, so an unread scan adds nothing. No em dashes (house style)."""
    from dashboard.terrain_phase import phase_display
    disp = phase_display(phase)
    if not disp:
        return ""
    loc = (location or "").strip()
    return f"Terrain Phase: {disp}." if not loc else f"Terrain Phase: {disp}. Location: {loc}."


def build_invoice_note(phase=None, location=""):
    """The order's invoice_note: the terrain-phase line (when a phase was read) followed
    by the standard payment text; just the standard text when no phase was read."""
    tn = terrain_note(phase, location)
    return f"{tn} {DEFAULT_INVOICE_NOTE}" if tn else DEFAULT_INVOICE_NOTE


def resolve_line_slug(name, catalog):
    """A remedy NAME -> a sellable catalog slug by EXACT (case-insensitive) match.
    No fuzzy matching: on an invoice a near-name substitution could bill the wrong
    SKU (ES1 vs ES13, Vitamin A vs Vitamin D). A non-exact name returns None and the
    caller lists it as skipped for manual add against the real catalog."""
    name = (name or "").strip().lower()
    if not name:
        return None
    for it in catalog or []:
        if (it.get("name") or "").strip().lower() == name:
            return it.get("slug") or None
    return None


def doses_per_day(freq_text):
    """A per-client frequency phrase -> doses/day, or None if unrecognized.
    Handles 'daily', 'a day', 'twice a day', 'two times a day', '3 times a day', '2x'."""
    import re
    t = (freq_text or "").strip().lower()
    if not t:
        return None
    m = re.search(r"(\d+)\s*(?:x|times?)\b", t)          # "3 times a day", "2x"
    if m:
        return int(m.group(1))
    words = {"once": 1, "one": 1, "twice": 2, "two": 2, "thrice": 3, "three": 3, "four": 4}
    for w, n in words.items():
        if re.search(rf"\b{w}\b", t) and re.search(r"\b(times?|x|a day|per day|daily|day)\b", t):
            return n
    if re.search(r"\b(daily|a day|per day|each day|every day)\b", t):
        return 1
    return None


def line_bottles(line):
    """Bottles to bill for one Biofield line: the line's own `bottles` field, else 1.

    Glen, 2026-09-25: "a single bottle gets entered on the invoice unless we specify
    otherwise, or change it on the invoice ... default would be 1 for most every
    product." It replaced a 30-day calculation (bottles_needed) that billed Peach
    Goddard 3 Candida Cleanse off a titrated six-a-day and Debra Herndon 3 IOP
    Syntropy. Anything blank, zero or unreadable is 1."""
    try:
        n = int(str((line or {}).get("bottles") or "").strip())
    except (TypeError, ValueError):
        return 1
    return n if n >= 1 else 1


def line_bottles_set(line):
    """The line's own Bottles value when it was set (an int >= 1), else None."""
    try:
        n = int(str((line or {}).get("bottles") or "").strip())
    except (TypeError, ValueError):
        return None
    return n if n >= 1 else None


def carry_open_quantities(lines, existing_items, explicit_slugs=()):
    """Keep an open order's bottle count where the Bottles field was left blank.

    Glen's rule bills 1 unless a count is set, but orders raised before the field
    existed carry intentional counts (Glen 2026-09-25: the unpaid multi-bottle orders
    "look right"). A re-raise or re-handoff must not cut them to 1. A count set on the
    line always wins; a blank line takes the open order's count for the same slug."""
    have = {}
    for item in existing_items or []:
        slug = (item.get("slug") or "").strip()
        try:
            q = int(item.get("qty") or 0)
        except (TypeError, ValueError):
            q = 0
        if slug and q > have.get(slug, 0):
            have[slug] = q
    explicit = set(explicit_slugs or ())
    out = []
    for line in lines or []:
        line = dict(line)
        slug = (line.get("slug") or "").strip()
        if (slug and slug != BIOFIELD_SLUG and slug not in explicit
                and have.get(slug, 0) > int(line.get("qty") or 1)):
            line["qty"] = have[slug]
        out.append(line)
    return out


def bottles_needed(freq_text, doses_per_bottle, program_days=30):
    """Bottles for the program = ceil(doses/day * days / doses_per_bottle), >= 1.
    Falls back to 1 when the frequency is unparseable OR doses_per_bottle is missing
    (e.g. infoceuticals carry no doses_per_bottle -> qty 1)."""
    import math
    try:
        dpb = int(doses_per_bottle)
    except (TypeError, ValueError):
        dpb = 0
    dpd = doses_per_day(freq_text)
    if not dpd or dpb <= 0:
        return 1
    return max(1, math.ceil(dpd * program_days / dpb))


def build_invoice_lines(client, remedies, catalog, include_fee=True):
    """Biofield Analysis is lines[0] (unless include_fee is False — e.g. the client
    already PAID for the analysis, so we invoice remedies only); then one line per
    resolvable remedy (order preserved). A remedy is a name string (qty 1) or a
    {"name","qty"} dict (qty = bottles needed). Unresolvable names go to 'skipped',
    never mispriced."""
    lines = [{"slug": BIOFIELD_SLUG, "qty": 1}] if include_fee else []
    skipped = []
    explicit_slugs = set()
    for r in remedies or []:
        explicit = False
        if isinstance(r, dict):
            name, qty = (r.get("name") or "").strip(), r.get("qty")
            explicit = bool(r.get("explicit"))
        else:
            name, qty = (r or "").strip(), 1
        if not name:
            continue
        try:
            qty = max(1, int(qty))
        except (TypeError, ValueError):
            qty = 1
        slug = resolve_line_slug(name, catalog)
        if not slug:
            skipped.append(name)
            continue
        # One remedy can serve several layers — Steve Fox's 15 September report put
        # Neuroprotect on three. That is still one product taken once, so it gets one
        # line carrying the LARGEST bottle count, never one line per layer and never
        # the counts added together. Without this the raise duplicated the line, and
        # a second raise duplicated it again.
        if explicit:
            explicit_slugs.add(slug)
        seen = next((l for l in lines if l["slug"] == slug), None)
        if seen is not None:
            seen["qty"] = max(seen["qty"], qty)
            continue
        # These remedies came from the practitioner's authored Biofield
        # analysis, so preserve that provenance through order creation and
        # into Edit Invoice instead of falling back to source='self'.
        lines.append({"slug": slug, "qty": qty, "source": "biofield"})
    return {"lines": lines, "skipped": skipped, "explicit_slugs": explicit_slugs}


def merge_manual_invoice_lines(new_lines, existing_items):
    """Preserve invoice-editor additions while refreshing Biofield schedule lines."""
    merged = [dict(line) for line in (new_lines or [])]
    present = {(line.get("slug") or "").strip() for line in merged}
    for item in existing_items or []:
        slug = (item.get("slug") or "").strip()
        if (not slug or slug == BIOFIELD_SLUG or
                (item.get("source") or "").strip().lower() == "biofield" or
                slug in present):
            continue
        line = {"slug": slug, "qty": max(1, int(item.get("qty") or 1))}
        for key in ("source", "note", "format"):
            if item.get(key):
                line[key] = item[key]
        if item.get("override") and item.get("unit_cents") is not None:
            line["unit_cents"] = item["unit_cents"]
        merged.append(line)
        present.add(slug)
    return merged


def _console():
    key = os.environ.get("CONSOLE_SECRET")
    if not key:
        return None, None
    base = os.environ.get("PUBLIC_BASE_URL", "https://illtowell.com").rstrip("/")
    return base, key


def default_fetch_catalog():
    base, key = _console()
    if not base:
        return []
    try:
        url = f"{base}/api/console/biofield-portal/catalog"
        req = urllib.request.Request(url, headers={"X-Console-Key": key})
        with urllib.request.urlopen(req, timeout=8) as r:
            resp = _json.loads(r.read().decode() or "{}")
        return resp.get("products") or []
    except Exception:
        return []


def default_create_order(customer, lines, replace_open=False, invoice_note=None,
                         update_order_id=None, idempotency_key=""):
    """Create the hand-off invoice on prod. With replace_open=True (a re-hand-off),
    prod first cancels the client's prior OPEN hand-off drafts (proposed, unpaid, not
    yet published) so a repeated hand-off UPDATES rather than piling up duplicates;
    published/paid orders are never touched."""
    base, key = _console()
    if not base:
        return {"ok": False, "error": "The console connection is not configured."}
    try:
        # No `pickup` flag: a hand-off is not a pickup. Shipping is computed from the
        # physical remedy lines; the analysis fee is a service and contributes no
        # bottle, so an analysis-only invoice still ships $0. Mark the order pickup
        # from the console when the client actually collects in person.
        body = {"customer": {"name": customer.get("name") or "", "email": customer.get("email") or ""},
                "lines": lines, "replace_open": bool(replace_open),
                "invoice_note": invoice_note or DEFAULT_INVOICE_NOTE}
        if update_order_id:
            body["update_order_id"] = int(update_order_id)
        # The authoring page mints one token per LOAD. Prod derives the order's
        # external_ref from it, and orders is UNIQUE(source, external_ref), so a second
        # click carrying the same token gets the first order back instead of raising a
        # second. Absent or blank, prod keeps its old random reference per request.
        if str(idempotency_key or "").strip():
            body["idempotency_key"] = str(idempotency_key).strip()
        url = f"{base}/api/orders/manual"
        req = urllib.request.Request(url, data=_json.dumps(body).encode(), method="POST",
                                     headers={"X-Console-Key": key, "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=20) as r:
            resp = _json.loads(r.read().decode() or "{}")
        if not resp.get("ok"):
            return {"ok": False, "error": resp.get("error") or "Order creation failed."}
        totals = resp.get("totals") or {}
        accepted = [ (l or {}).get("slug") for l in (resp.get("lines") or []) if (l or {}).get("slug") ]
        return {"ok": True, "order_id": resp.get("order_id"),
                "external_ref": resp.get("external_ref"),
                "total_cents": totals.get("total_cents"),
                "cancelled": resp.get("cancelled") or [],
                "accepted_slugs": accepted, "error": None}
    except Exception:
        return {"ok": False, "error": "Couldn't reach the console to create the order."}


def default_caregiver_billing(email, caregiver_email=None, on=None):
    """Bill with caregiver: who may be billed for this client, and who is remembered.
    With caregiver_email and on, sets the preference first. Returns {} on failure."""
    base, key = _console()
    if not base or not (email or "").strip():
        return {}
    try:
        if caregiver_email is not None and on is not None:
            req = urllib.request.Request(
                f"{base}/api/console/caregiver-billing", method="POST",
                data=_json.dumps({"email": email, "caregiver_email": caregiver_email,
                                  "on": bool(on)}).encode(),
                headers={"X-Console-Key": key, "Content-Type": "application/json"})
        else:
            req = urllib.request.Request(
                f"{base}/api/console/caregiver-billing?email=" + urllib.parse.quote(email),
                headers={"X-Console-Key": key})
        with urllib.request.urlopen(req, timeout=10) as r:
            return _json.loads(r.read().decode() or "{}")
    except Exception:
        return {}


def default_invoice_link(order_id):
    base, key = _console()
    if not base or not order_id:
        return {"ok": False, "error": "link unavailable"}
    try:
        url = f"{base}/api/console/order/{int(order_id)}/invoice-link"
        req = urllib.request.Request(url, headers={"X-Console-Key": key})
        with urllib.request.urlopen(req, timeout=10) as r:
            resp = _json.loads(r.read().decode() or "{}")
        if resp.get("ok") and resp.get("link"):
            return {"ok": True, "print_url": resp["link"], "error": None}
        return {"ok": False, "error": "link unavailable"}
    except Exception:
        return {"ok": False, "error": "link unavailable"}


def default_orders_link(order_id):
    """The prod Orders-board deep link for an order (Edit action), key-carried the
    same way the local console tools bounce to prod. '' when unconfigured."""
    base, key = _console()
    if not base or not order_id:
        return ""
    return f"{base}/console/orders?order={int(order_id)}&key={urllib.parse.quote(key)}"


def default_edit_order_link(order_id):
    """Direct Edit Invoice URL for a known order."""
    base, key = _console()
    if not base or not order_id:
        return ""
    return (f"{base}/orders/new?edit_order={int(order_id)}&key="
            f"{urllib.parse.quote(key)}")


def default_client_orders(email, limit=300):
    """This client's orders from the console board, for the dispensed-before panel.

    Same read the invoice lookup already uses. Returns [] on any failure: the
    panel is a reference, and a reference that breaks the page it sits on is
    worse than one that is empty.
    """
    rows, err = client_orders_with_status(email, limit)
    # A list that also remembers WHY it is empty.
    #
    # client_orders is an injected seam on create_app, and the author route's tests
    # assert it is called. Returning a tuple, or reaching past the seam to the concrete
    # function, breaks both the injection and those tests. A list subclass keeps the
    # existing contract exactly -- every current caller treats it as a list and a test
    # may still inject a plain one -- while letting the panel ask `getattr(rows,
    # "error", None)` and tell a failed lookup from an empty history.
    out = _OrdersResult(rows)
    out.error = err
    return out


class _OrdersResult(list):
    """A list of orders carrying the reason it may be empty. See default_client_orders."""
    error = None


def client_orders_with_status(email, limit=300):
    """(orders, error). The error is what makes an empty panel honest.

    Glen, 2026-09-18: "I'm still not seeing how many bottles of each product have been
    previously purchased", and "yes, make it show history unavailable".

    The panel had been dead for at least a day and looked exactly like a client with no
    purchases. The local app reads CONSOLE_SECRET at startup, its process predated the key
    rotation, and every lookup came back 401. Returning [] on failure meant the page said
    "No order history for this client yet" about a client who had plenty.

    So failure and emptiness are now different answers. A caller that cannot use the error
    still gets the old list-only contract from default_client_orders.
    """
    base, key = _console()
    if not base:
        return [], "no console key configured on this machine"
    if not (email or "").strip():
        return [], None                       # genuinely nothing to ask about
    try:
        url = f"{base}/api/orders?limit={int(limit)}&key=" + urllib.parse.quote(key)
        req = urllib.request.Request(url, headers={"X-Console-Key": key})
        with urllib.request.urlopen(req, timeout=10) as r:
            return _json.loads(r.read().decode() or "{}").get("data") or [], None
    except Exception as e:
        print(f"[dispensed] order lookup skipped: {e!r}", flush=True)
        code = getattr(e, "code", None)
        if code == 401:
            # Name the actual remedy. A stale key is the failure this panel has actually
            # had, and "unauthorized" alone sends the reader to the wrong place.
            why = ("the console key this app started with is no longer valid — "
                   "restart the local server to pick up the current one")
        elif code == 502:
            why = "the server was restarting (502); try again in a moment"
        elif isinstance(e, TimeoutError) or "timed out" in str(e).lower():
            why = "the order lookup timed out"
        else:
            why = f"the order lookup failed ({type(e).__name__})"
        return [], why


def default_latest_invoice(email):
    """Latest Biofield-authored invoice for this client, including remedy-only
    invoices raised after the analysis fee was already paid."""
    base, key = _console()
    if not base or not (email or "").strip():
        return {"ok": False, "error": "invoice unavailable"}
    try:
        url = f"{base}/api/orders?limit=300&key=" + urllib.parse.quote(key)
        req = urllib.request.Request(url, headers={"X-Console-Key": key})
        with urllib.request.urlopen(req, timeout=10) as r:
            rows = (_json.loads(r.read().decode() or "{}").get("data") or [])
        target = email.strip().lower()
        for order in rows:
            if (order.get("email") or "").strip().lower() != target:
                continue
            items = order.get("items") or []
            if any((it.get("slug") == BIOFIELD_SLUG or it.get("source") == "biofield")
                   for it in items):
                return {"ok": True, "order_id": order.get("id"),
                        "items": items, "status": order.get("status"),
                        "pay_status": order.get("pay_status"),
                        "portal_published": bool(order.get("portal_published"))}
        return {"ok": False, "error": "No Biofield invoice found for this client."}
    except Exception:
        return {"ok": False, "error": "Couldn't reach the console to find the invoice."}


def default_publish_invoice(order_id):
    """POST the prod publish-to-portal endpoint for an order, so it shows as a pay
    card on the client's portal. Returns {ok, link} or {ok:False, error}."""
    base, key = _console()
    if not base or not order_id:
        return {"ok": False, "error": "publish unavailable (no console config)"}
    try:
        url = f"{base}/api/console/order/{int(order_id)}/publish-to-portal"
        req = urllib.request.Request(url, data=b"{}", method="POST",
                                     headers={"Content-Type": "application/json", "X-Console-Key": key})
        with urllib.request.urlopen(req, timeout=10) as r:
            resp = _json.loads(r.read().decode() or "{}")
        return resp if isinstance(resp, dict) else {"ok": False, "error": "bad response"}
    except Exception:
        return {"ok": False, "error": "publish failed"}


def default_biofield_paid(email):
    """Has this client already PAID for a Biofield Analysis? Asks prod for a paid,
    non-cancelled order carrying the biofield-analysis line. Returns {paid, order_id,
    paid_at} (paid False when none / unreachable) so the raise can drop the fee line."""
    base, key = _console()
    if not base or not (email or "").strip():
        return {"paid": False}
    try:
        url = (f"{base}/api/console/biofield-analysis-paid?email="
               + urllib.parse.quote(email) + "&key=" + urllib.parse.quote(key))
        req = urllib.request.Request(url, headers={"X-Console-Key": key})
        with urllib.request.urlopen(req, timeout=8) as r:
            resp = _json.loads(r.read().decode() or "{}")
        return resp if isinstance(resp, dict) else {"paid": False}
    except Exception:
        return {"paid": False}


def default_handoff_push(email, name, content, scan_date=""):
    """Hand off to Rae: push a portal-seed `content` (built from the authored chain)
    to prod as a portal ai_draft (staged for Rae to review + publish from the console).
    Reuses /admin/portal/upsert. Returns {ok, ...} or {ok:False, error}."""
    base, key = _console()
    if not base or not (email or "").strip():
        return {"ok": False, "error": "handoff unavailable (no console config / email)"}
    payload = dict(content or {})
    payload["biofield_status"] = "ai_draft"
    # Stamp this hand-off's report as the client's CURRENT one so it wins over a stale
    # AI reveal (which owns its own per-scan report row) regardless of scan date.
    if (scan_date or "").strip():
        payload["current_scan_date"] = scan_date.strip()
    body = _json.dumps({"email": email, "name": name or "", "content": payload,
                        "scan_date": scan_date or "", "scan_id": ""}).encode("utf-8")
    try:
        req = urllib.request.Request(f"{base}/admin/portal/upsert", data=body, method="POST",
                                     headers={"Content-Type": "application/json", "X-Console-Key": key})
        with urllib.request.urlopen(req, timeout=20) as r:
            resp = _json.loads(r.read().decode() or "{}")
        return resp if isinstance(resp, dict) else {"ok": True}
    except Exception:
        return {"ok": False, "error": "handoff push failed"}
