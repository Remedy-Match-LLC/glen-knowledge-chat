"""Tracking — parse USPS Click-N-Ship payment-confirmation emails into shipments.

Glen ships via USPS Click-N-Ship. Every label triggers a "Payment Confirmation"
email from noreply-ecns@usps.com that lists, per label:

    Priority Mail®
    <the IMpb barcode digits, linked to USPS tracking>
    Scheduled delivery date: MM/DD/YYYY
    Shipped To:
      <recipient name>
      <street>
      <city ST zip-4 US>

USPS is *supposed* to email the recipient directly but frequently doesn't, so we
parse these confirmations ourselves, match each recipient to a GHL contact, and
draft Glen's "tracking number" email for review.

This module is the pure parsing core — no network, no DB — so it's unit-testable
and safe to run in the cron container (stdlib only, mirrors dashboard.shipping).

Public surface:
    normalize_tracking(impb)        — IMpb barcode digits -> 22-digit USPS tracking
    tracking_url(tracking)          — the customer-facing TrackConfirm link
    parse_cns_confirmation(html)    — confirmation HTML -> {order_uuid, shipments:[...]}

Each shipment dict:
    {tracking, recipient_name, street, city, state, zip, service,
     delivery_date, address_block}
"""

from __future__ import annotations

import re
import sqlite3
import json
import unicodedata
from datetime import datetime, timezone
from html import escape, unescape
from typing import Dict, List, Optional

from dashboard import dbwrite


TRACK_URL = "https://tools.usps.com/go/TrackConfirmAction?tLabels={}"

# Glen's standard sign-off, mirroring the "tracking number" emails he sends today
# (drglenswartwout@gmail.com). Edit here if his signature changes. The tracking
# link is injected above this block by build_tracking_email().
SIGNATURE_HTML = (
    '<div><br></div>'
    '<div style="font-family:\'arial black\',sans-serif;font-size:large;color:#000">'
    'Dr. Glen Swartwout<br>'
    '(808) 217-9647<br>'
    'Healing Oasis<br>'
    '351 Wailuku Drive<br>'
    "Hilo, Kingdom of Hawai'i&nbsp; [96720]<br>"
    'Learn More with Our <b>Accelerated Self Healing&#8482;</b> Community at '
    '<a href="http://truly.vip/ASH">Truly.VIP/ASH</a><br><br>'
    'Video Channel: <a href="http://youtube.com/user/DoctorGlen">youtube.com/user/DoctorGlen</a><br>'
    'Author Page: <a href="http://amazon.com/default/e/B00AXTFZ26">amazon.com/default/e/B00AXTFZ26</a><br>'
    'LinkedIn: <a href="http://linkedin.com/in/drglen">linkedin.com/in/drglen</a><br>'
    'Fan Page: <a href="http://facebook.com/DrSwartwout">facebook.com/DrSwartwout</a><br>'
    'Remedies: <a href="https://remedymatch.com/">https://remedymatch.com/</a><br>'
    '&nbsp;&nbsp;&nbsp;&nbsp; Consultation: apply at bottom of page'
    '</div>'
)

SIGNATURE_TEXT = (
    "\n\n--\nDr. Glen Swartwout\n(808) 217-9647\nHealing Oasis\n351 Wailuku Drive\n"
    "Hilo, Kingdom of Hawai'i  [96720]\n"
    "Learn More with Our Accelerated Self Healing™ Community at Truly.VIP/ASH\n\n"
    "Video Channel: youtube.com/user/DoctorGlen\n"
    "Author Page: amazon.com/default/e/B00AXTFZ26\n"
    "LinkedIn: linkedin.com/in/drglen\nFan Page: facebook.com/DrSwartwout\n"
    "Remedies: https://remedymatch.com/\n     Consultation: apply at bottom of page\n"
)

EMAIL_SUBJECT = "tracking number"

# USPS retail/Priority tracking numbers (the part Glen pastes) are 22 digits and
# begin with a service banner — 9405/9400/9407/9270/9361/9205 etc. The full IMpb
# printed on the label/email prepends a "420" + destination ZIP routing block, so
# the human-facing tracking number is the trailing 22 digits.
_TRACK_22 = re.compile(r"(9[0-9]{21})")


def normalize_tracking(impb: str) -> Optional[str]:
    """Reduce an IMpb barcode string to the 22-digit USPS tracking number.

    Handles the three forms seen in the wild:
        '9405530109355381515251'                     -> itself (already 22)
        '4208522452499405530109355381515251'         -> '9405530109355381515251'
        '4205 8522 4524 9 9405 5301 0935 5381 5152 51'-> stripped, trailing 22

    Returns None if no plausible tracking number is present.
    """
    digits = re.sub(r"\D", "", impb or "")
    if not digits:
        return None
    # Prefer an explicit 9xxxxxxxxxxxxxxxxxxxxx run anchored to the end.
    m = _TRACK_22.search(digits)
    if m and digits.endswith(m.group(1)):
        return m.group(1)
    if len(digits) >= 22:
        return digits[-22:]
    return None


def tracking_url(tracking: str) -> str:
    return TRACK_URL.format(tracking)


# One shipment = one "item-contents-column" cell. Inside it, the anchor text is
# the (clean) IMpb digit string and the Shipped-To <p> lines carry the recipient.
_BLOCK_RE = re.compile(
    r'item-contents-column.*?</td>\s*<td class="item-total-column"',
    re.I | re.S,
)
_IMPB_RE = re.compile(r"<a[^>]*>\s*([\d ]{20,48})\s*</a>", re.I)
_DELIVERY_RE = re.compile(r"Scheduled delivery date:\s*([0-9/]+)", re.I)
_SERVICE_RE = re.compile(r"<p[^>]*>\s*([^<]*?Mail[^<]*?)</p>", re.I)
_SHIPPED_TO_RE = re.compile(r"Shipped To:\s*</p>(.*?)$", re.I | re.S)
_P_LINE_RE = re.compile(r'<p[^>]*class="[^"]*pt-5[^"]*"[^>]*>(.*?)</p>', re.I | re.S)
# city ST zip(-4) [US]
_CSZ_RE = re.compile(r"^(.*?)[, ]+([A-Z]{2})\s+(\d{5}(?:-\d{4})?)(?:\s+US)?\s*$")

_ORDER_UUID_RE = re.compile(
    r"(?:orderUUID=?|/history/orders/)([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
    r"[0-9a-f]{4}-[0-9a-f]{12})",
    re.I,
)


def _clean(text: str) -> str:
    """Strip tags + collapse whitespace inside a captured fragment."""
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", "", text))).strip()


def _parse_block(block: str) -> Optional[dict]:
    impb_m = _IMPB_RE.search(block)
    if not impb_m:
        return None
    tracking = normalize_tracking(impb_m.group(1))
    if not tracking:
        return None

    lines = [_clean(p) for p in _P_LINE_RE.findall(block)]
    lines = [ln for ln in lines if ln]
    recipient_name = lines[0] if lines else ""
    addr_lines = lines[1:]

    city = state = zipcode = ""
    if addr_lines:
        csz = _CSZ_RE.match(addr_lines[-1])
        if csz:
            city, state, zipcode = csz.group(1).strip(), csz.group(2), csz.group(3)
    street = " ".join(addr_lines[:-1]) if len(addr_lines) > 1 else (
        addr_lines[0] if addr_lines and not _CSZ_RE.match(addr_lines[0]) else ""
    )

    svc_m = _SERVICE_RE.search(block)
    del_m = _DELIVERY_RE.search(block)
    return {
        "tracking": tracking,
        "recipient_name": recipient_name,
        "street": street,
        "city": city,
        "state": state,
        "zip": zipcode,
        "service": _clean(svc_m.group(1)) if svc_m else "",
        "delivery_date": del_m.group(1) if del_m else "",
        "address_block": " / ".join(lines[1:]),
    }


def parse_cns_confirmation(html: str) -> Dict[str, object]:
    """Parse a Click-N-Ship Payment Confirmation email body into shipments.

    Returns {order_uuid: str|None, shipments: [shipment_dict, ...]}.
    Order of shipments matches their order in the email.
    """
    if not html:
        return {"order_uuid": None, "shipments": []}

    uuid_m = _ORDER_UUID_RE.search(html)
    order_uuid = uuid_m.group(1).lower() if uuid_m else None

    shipments: List[dict] = []
    for block in _BLOCK_RE.findall(html):
        parsed = _parse_block(block)
        if parsed:
            shipments.append(parsed)
    return {"order_uuid": order_uuid, "shipments": shipments}


# ── Draft email (Glen's "tracking number" email, replicated) ─────────────────

def build_tracking_email(tracking: str, recipient_name: Optional[str] = None,
                         resolved_email: Optional[str] = None) -> dict:
    """Build the draft Glen reviews + sends. Mirrors his manual email exactly:
    an "Aloha <first name>" greeting (when we know the name), the tracking number
    as a live USPS link, then his standard sign-off.

    Returns {subject, html, text}. The watcher fills To: separately.

    resolved_email: the matched customer address (or None/blank when no confident
    GHL match). When it's missing AND we have a last name, a REVIEWER note carrying
    the recipient's FULL name is prepended so Glen can look up the address, fill
    To:, and delete the note before sending. The customer greeting stays first-name.
    """
    url = tracking_url(tracking)
    greeting_html = (
        f"<p>Aloha {escape(recipient_name.split()[0])},</p>" if recipient_name else ""
    )
    greeting_text = (
        f"Aloha {recipient_name.split()[0]},\n\n" if recipient_name else ""
    )
    # No email match: surface the full name (esp. the last name) for Glen's lookup.
    note_html = note_text = ""
    if not (resolved_email or "").strip() and recipient_name and len(recipient_name.split()) > 1:
        who = recipient_name.strip()
        note_html = (
            f'<p style="color:#b00020"><strong>[No email on file for {escape(who)} '
            f"&mdash; find their address, fill the To: field, then delete this "
            f"line before sending.]</strong></p>"
        )
        note_text = (
            f"[No email on file for {who} — find their address, fill the To: field, "
            f"then delete this line before sending.]\n\n"
        )
    html = (
        f"<div dir=\"ltr\">{note_html}{greeting_html}"
        f"<p>Your order is on its way. Here is your USPS tracking number:</p>"
        f'<h3><a href="{url}">{tracking}</a></h3>'
        f"{SIGNATURE_HTML}</div>"
    )
    text = (
        f"{note_text}{greeting_text}Your order is on its way. "
        f"Here is your USPS tracking number:\n\n{tracking}\n{url}"
        f"{SIGNATURE_TEXT}"
    )
    return {"subject": EMAIL_SUBJECT, "html": html, "text": text}


# ── Persistence: shipments table (idempotency + audit) ───────────────────────
#
# One row per tracking number. status:
#   'drafted'      — Gmail draft created, awaiting Glen/Rae review + send
#   'sent'         — delivered to GHL's outbound conversation-message API
#   'needs_review' — parsed, but no confident GHL email match (To: left blank)
#   'send_failed'  — GHL failed; a Gmail review draft was created, not sent
#
# tracking_number is UNIQUE so re-running the watcher over the same confirmation
# email is a no-op (we never double-draft).

def init_tracking_schema(cx: sqlite3.Connection) -> None:
    """Create the shipments table. Idempotent."""
    from dashboard import db
    pg = db.backend_of(cx) == "postgres"
    if pg:
        cx.execute("""
            CREATE TABLE IF NOT EXISTS shipments (
                id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                tracking_number TEXT    NOT NULL UNIQUE,
                order_uuid      TEXT,
                recipient_name  TEXT,
                address_block   TEXT,
                resolved_email  TEXT,
                match_confidence TEXT,
                ghl_contact_id  TEXT,
                draft_id        TEXT,
                status          TEXT    NOT NULL DEFAULT 'needs_review',
                source_msg_id   TEXT,
                scheduled_delivery_date TEXT,
                notification_channel TEXT,
                notification_sent_at TEXT,
                notification_error TEXT,
                delivered_at TEXT,
                coaching_opened INTEGER NOT NULL DEFAULT 0,
                easypost_tracker_id TEXT,
                order_link_status TEXT,
                order_link_reason TEXT,
                linked_order_ids TEXT,
                created_at      TEXT    NOT NULL DEFAULT (now()::text),
                updated_at      TEXT
            )
        """)
    else:
        cx.execute("""
            CREATE TABLE IF NOT EXISTS shipments (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                tracking_number TEXT    NOT NULL UNIQUE,
                order_uuid      TEXT,
                recipient_name  TEXT,
                address_block   TEXT,
                resolved_email  TEXT,
                match_confidence TEXT,
                ghl_contact_id  TEXT,
                draft_id        TEXT,
                status          TEXT    NOT NULL DEFAULT 'needs_review',
                source_msg_id   TEXT,
                scheduled_delivery_date TEXT,
                notification_channel TEXT,
                notification_sent_at TEXT,
                notification_error TEXT,
                delivered_at TEXT,
                coaching_opened INTEGER NOT NULL DEFAULT 0,
                easypost_tracker_id TEXT,
                order_link_status TEXT,
                order_link_reason TEXT,
                linked_order_ids TEXT,
                created_at      TEXT    NOT NULL DEFAULT (datetime('now')),
                updated_at      TEXT
            )
        """)
    cx.execute(
        "CREATE INDEX IF NOT EXISTS idx_shipments_status ON shipments(status)"
    )
    # Order-link audit fields. `orders.shipment_id` is the operational join;
    # these fields preserve why an automatic link was (or was not) made.
    for column in (
        "scheduled_delivery_date", "notification_channel",
        "notification_sent_at", "notification_error",
        "delivered_at", "easypost_tracker_id",
        "order_link_status", "order_link_reason", "linked_order_ids",
    ):
        if not db.column_exists(cx, "shipments", column):
            cx.execute(f"ALTER TABLE shipments ADD COLUMN {column} TEXT")
    if not db.column_exists(cx, "shipments", "coaching_opened"):
        cx.execute(
            "ALTER TABLE shipments ADD COLUMN coaching_opened INTEGER NOT NULL DEFAULT 0")
    cx.commit()


def shipment_exists(cx: sqlite3.Connection, tracking_number: str) -> bool:
    row = cx.execute(
        "SELECT 1 FROM shipments WHERE tracking_number = ? LIMIT 1",
        (tracking_number,),
    ).fetchone()
    return row is not None


def record_shipment(cx: sqlite3.Connection, **fields) -> Optional[int]:
    """Insert one shipment. No-op (returns None) if the tracking number already
    exists — this is what makes the watcher safe to re-run."""
    tn = fields.get("tracking_number")
    if not tn or shipment_exists(cx, tn):
        return None
    cols = [
        "tracking_number", "order_uuid", "recipient_name", "address_block",
        "resolved_email", "match_confidence", "ghl_contact_id", "draft_id",
        "status", "source_msg_id", "scheduled_delivery_date",
        "notification_channel", "notification_sent_at", "notification_error",
    ]
    vals = [fields.get(c) for c in cols]
    placeholders = ", ".join("?" for _ in cols)
    new_id = dbwrite.insert_returning_id(
        cx,
        f"INSERT INTO shipments ({', '.join(cols)}) VALUES ({placeholders})",
        vals,
    )
    cx.commit()
    return int(new_id)


def _norm_match_text(value: object) -> str:
    """Case/punctuation/diacritic-insensitive text for identity comparisons."""
    raw = unicodedata.normalize("NFKD", str(value or ""))
    ascii_text = "".join(ch for ch in raw if not unicodedata.combining(ch))
    return "".join(ch.lower() for ch in ascii_text if ch.isalnum())


def _zip5(value: object) -> str:
    digits = re.sub(r"\D", "", str(value or ""))
    return digits[:5]


def _order_address(order: dict) -> dict:
    try:
        address = json.loads(order.get("address_json") or "{}")
    except (TypeError, ValueError):
        address = {}
    return address if isinstance(address, dict) else {}


def _address_key(address: dict) -> tuple:
    return (
        _norm_match_text(address.get("street") or address.get("address1")),
        _norm_match_text(address.get("city")),
        _norm_match_text(address.get("state")),
        _zip5(address.get("zip") or address.get("postal_code")),
    )


def _shipment_address_key(shipment: dict) -> tuple:
    return (
        _norm_match_text(shipment.get("street")),
        _norm_match_text(shipment.get("city")),
        _norm_match_text(shipment.get("state")),
        _zip5(shipment.get("zip")),
    )


def _audit_order_link(cx, shipment_id: int, status: str, reason: str,
                      order_ids: List[int]) -> dict:
    cx.execute(
        "UPDATE shipments SET order_link_status=?, order_link_reason=?, "
        "linked_order_ids=?, updated_at=? WHERE id=?",
        (status, reason, json.dumps(order_ids), _iso_now(), shipment_id),
    )
    cx.commit()
    return {"status": status, "order_ids": order_ids, "reason": reason}


def link_shipment_to_orders(cx: sqlite3.Connection, shipment_id: int,
                            shipment: dict, resolved_email: str = "") -> dict:
    """Safely attach an ingested USPS tracking number to one open client order.

    Exact structured shipping address is authoritative. Email/name/ZIP may
    disambiguate or provide a fallback, but a non-unique result is never written.
    The order lifecycle is deliberately unchanged: buying a label is not carrier
    acceptance, and the tracking-status sync advances it once USPS reports motion.
    """
    existing_cur = cx.execute(
        "SELECT tracking_number, order_link_status, order_link_reason, "
        "linked_order_ids FROM shipments WHERE id=?", (shipment_id,)
    )
    existing = existing_cur.fetchone()
    if not existing:
        raise ValueError(f"shipment #{shipment_id} not found")
    prior = (dict(existing) if hasattr(existing, "keys") else
             dict(zip((d[0] for d in existing_cur.description), existing)))
    if prior.get("order_link_status") == "linked":
        try:
            ids = [int(v) for v in json.loads(prior.get("linked_order_ids") or "[]")]
        except (TypeError, ValueError):
            ids = []
        return {"status": "linked", "order_ids": ids,
                "reason": prior.get("order_link_reason") or "previously linked"}

    orders_cur = cx.execute(
        "SELECT id, email, name, address_json FROM orders "
        "WHERE status IN ('new','packed') "
        "AND (tracking_number IS NULL OR trim(tracking_number)='') "
        "ORDER BY id DESC"
    )
    rows = orders_cur.fetchall()
    if rows and hasattr(rows[0], "keys"):
        orders = [dict(row) for row in rows]
    else:
        columns = [d[0] for d in orders_cur.description]
        orders = [dict(zip(columns, row)) for row in rows]
    recipient = _norm_match_text(shipment.get("recipient_name"))
    target_email = str(resolved_email or "").strip().lower()
    ship_key = _shipment_address_key(shipment)

    def order_name(order):
        addr = _order_address(order)
        return _norm_match_text(addr.get("name") or order.get("name"))

    def email_matches(order):
        return bool(target_email and
                    str(order.get("email") or "").strip().lower() == target_email)

    # Require every structured component so a partial/blank address cannot look exact.
    exact_address = []
    if all(ship_key):
        exact_address = [o for o in orders if _address_key(_order_address(o)) == ship_key]
    if len(exact_address) == 1:
        chosen, reason = exact_address, "exact shipping address"
    elif len(exact_address) > 1:
        by_email = [o for o in exact_address if email_matches(o)]
        by_name = [o for o in exact_address if recipient and order_name(o) == recipient]
        if len(by_email) == 1:
            chosen, reason = by_email, "exact address + client email"
        elif len(by_name) == 1:
            chosen, reason = by_name, "exact address + recipient name"
        else:
            ids = [int(o["id"]) for o in exact_address]
            return _audit_order_link(cx, shipment_id, "ambiguous",
                                     "multiple open orders at exact address", ids)
    else:
        email_name = [o for o in orders if email_matches(o) and recipient
                      and order_name(o) == recipient]
        if len(email_name) == 1:
            chosen, reason = email_name, "exact client email + recipient name"
        elif len(email_name) > 1:
            ids = [int(o["id"]) for o in email_name]
            return _audit_order_link(cx, shipment_id, "ambiguous",
                                     "multiple open orders for client", ids)
        else:
            ship_zip = ship_key[3]
            name_zip = [o for o in orders if recipient and ship_zip
                        and order_name(o) == recipient
                        and _address_key(_order_address(o))[3] == ship_zip]
            if len(name_zip) == 1:
                chosen, reason = name_zip, "exact recipient name + ZIP"
            elif len(name_zip) > 1:
                ids = [int(o["id"]) for o in name_zip]
                return _audit_order_link(cx, shipment_id, "ambiguous",
                                         "multiple open orders for recipient + ZIP", ids)
            else:
                return _audit_order_link(cx, shipment_id, "unmatched",
                                         "no safe open-order match", [])

    order_id = int(chosen[0]["id"])
    cx.execute(
        "UPDATE orders SET tracking_number=?, shipment_id=?, updated_at=? WHERE id=?",
        (prior["tracking_number"], shipment_id, _iso_now(), order_id),
    )
    return _audit_order_link(cx, shipment_id, "linked", reason, [order_id])


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def normalize_delivery_date(value: str) -> str:
    """USPS MM/DD/YYYY scheduled date -> ISO YYYY-MM-DD; preserve unknown forms."""
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        return datetime.strptime(raw, "%m/%d/%Y").date().isoformat()
    except ValueError:
        return raw


def migrate_add_delivery_columns(cx) -> None:
    """Add delivery-tracking columns to shipments if missing. Safe on every startup."""
    from dashboard import db
    for column in ("delivered_at", "easypost_tracker_id"):
        if not db.column_exists(cx, "shipments", column):
            cx.execute(f"ALTER TABLE shipments ADD COLUMN {column} TEXT")
    if not db.column_exists(cx, "shipments", "coaching_opened"):
        cx.execute(
            "ALTER TABLE shipments ADD COLUMN coaching_opened INTEGER NOT NULL DEFAULT 0")
    cx.commit()


def shipment_by_tracking(cx, tracking_number):
    if not tracking_number:
        return None
    return cx.execute("SELECT * FROM shipments WHERE tracking_number=?",
                      (tracking_number,)).fetchone()


def mark_shipment_delivered(cx, shipment_id, delivered_at) -> bool:
    """Set delivered_at only if currently NULL. Returns True iff it set it now."""
    cur = cx.execute(
        "UPDATE shipments SET delivered_at=?, updated_at=? WHERE id=? AND delivered_at IS NULL",
        (delivered_at, delivered_at, shipment_id))
    cx.commit()
    return cur.rowcount > 0


def set_shipment_tracker(cx, shipment_id, tracker_id) -> bool:
    cur = cx.execute("UPDATE shipments SET easypost_tracker_id=?, updated_at=? WHERE id=?",
                     (tracker_id, _iso_now(), shipment_id))
    cx.commit()
    return cur.rowcount > 0
