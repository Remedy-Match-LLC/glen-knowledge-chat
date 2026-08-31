"""Send bulk/onboarding email via GHL v2 (LeadConnector) → the location's connected
Mailgun sending domain. Keeps the consumer Gmail account (drglenswartwout@gmail.com,
~500 sends/day) off the bulk path so a flood can't starve a client invoice. See
the gmail-send-cap note.

Auth: a GHL v2 Private Integration Token (`GHL_PIT`) — created in GHL Settings →
Private Integrations with scopes `contacts.write` + `conversations/message.write` —
plus `GHL_LOCATION_ID`. Optional `GHL_EMAIL_FROM` overrides the from-identity
("Dr. Glen <hi@mg.illtowell.com>"); omitted → the location's default sending address.

send_via_ghl() upserts the contact, then sends an Email message. Raises on any
failure so the caller (inbox.send_bulk) falls back to Gmail — nothing is lost.

send_sms_via_ghl() is the SMS sibling: same upsert, same endpoint, message
type "SMS" instead of "Email". Never raises -- see its docstring.
"""
from __future__ import annotations

import os

import requests

_V2 = "https://services.leadconnectorhq.com"
_VERSION = "2021-07-28"
_UA = "RemedyMatch/1.0 (+https://illtowell.com)"


def is_configured() -> bool:
    return bool(os.environ.get("GHL_PIT") and os.environ.get("GHL_LOCATION_ID"))


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {os.environ['GHL_PIT']}",
        "Version": _VERSION,
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": _UA,
    }


def _upsert_contact(email: str, name: str = "", phone: str = "") -> str:
    # LIVE CRM WRITE. Private on purpose: its callers are send_via_ghl and
    # send_sms_via_ghl, both of which carry the pytest guard. Keep it that
    # way -- a new caller must guard itself.
    body = {"locationId": os.environ["GHL_LOCATION_ID"], "email": email}
    if name:
        body["name"] = name
    if phone:
        body["phone"] = phone
    r = requests.post(f"{_V2}/contacts/upsert", headers=_headers(), json=body, timeout=20)
    r.raise_for_status()
    d = r.json() or {}
    cid = ((d.get("contact") or {}).get("id")) or d.get("id")
    if not cid:
        raise RuntimeError(f"GHL upsert returned no contact id: {d}")
    return cid


def send_via_ghl(to_email: str, subject: str, *, html: str | None = None,
                 text: str | None = None, from_name: str | None = None) -> dict:
    """Send one Email via GHL v2. Returns {'id', 'via': 'ghl'}. Raises on failure."""
    if not is_configured():
        raise RuntimeError("GHL v2 not configured (GHL_PIT / GHL_LOCATION_ID)")
    # Never touch the live CRM or send real email under pytest. This path has TWO live
    # mutations -- _upsert_contact writes a real contact, then the POST below sends a real
    # message -- so the guard sits above both. Mirrors the guard on the shared Gmail
    # transport in inbox.send_email. Until now this was safe only by accident: GHL_PIT is
    # absent from the dev config, so is_configured() was False. Adding creds there would
    # have made a full-suite run write to the CRM.
    # Deliberately placed AFTER the is_configured() check so behaviour is unchanged when
    # creds are absent (the case every current test runs in); it only bites when they exist.
    # Returns rather than raises: raising would make inbox.send_bulk log a misleading
    # "GHL send failed" and take the Gmail fallback to reach the same no-op.
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return {"id": None, "via": "ghl", "skipped": "pytest"}
    contact_id = _upsert_contact(to_email, from_name and "" or "")
    body = {"type": "Email", "contactId": contact_id, "subject": subject}
    if html:
        body["html"] = html
    if text:
        body["message"] = text
    frm = os.environ.get("GHL_EMAIL_FROM", "").strip()
    if frm:
        body["emailFrom"] = frm  # else GHL uses the location's default sending identity
    r = requests.post(f"{_V2}/conversations/messages", headers=_headers(), json=body, timeout=20)
    r.raise_for_status()
    d = r.json() or {}
    return {"id": d.get("messageId") or d.get("conversationId"), "via": "ghl"}


def _sms_payload(contact_id: str, message: str) -> dict:
    """The request body, split out with no I/O so its shape is testable.

    send_sms_via_ghl short-circuits under pytest before it posts anything, so
    this is the only place the SMS-vs-Email type can be asserted.
    """
    return {"type": "SMS", "contactId": contact_id, "message": message}


def send_sms_via_ghl(to_email: str, message: str, *, phone: str = "") -> dict:
    """Send an SMS through GHL, or say why it could not.

    GHL addresses by contactId, not by phone number, so the recipient must
    exist as a contact and that contact must carry a phone. We pass the number
    through on the upsert so a contact created here is textable.

    Returns {"id": ...} on success and {"skipped": reason} otherwise. It never
    raises: every caller is inside a notification block guarding a booking
    that has already committed, and a text that cannot be sent is not a reason
    to tell someone their booking failed.
    """
    if not is_configured():
        return {"skipped": "ghl not configured"}
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return {"skipped": "pytest"}
    try:
        contact_id = _upsert_contact(to_email, phone=phone)
    except Exception as e:  # noqa: BLE001
        return {"skipped": f"contact lookup failed: {e!r}"}
    body = _sms_payload(contact_id, message)
    try:
        r = requests.post(f"{_V2}/conversations/messages",
                          headers=_headers(), json=body, timeout=20)
        if r.status_code >= 300:
            return {"skipped": f"ghl returned {r.status_code}: {r.text[:200]}"}
        return {"id": (r.json() or {}).get("messageId"), "via": "ghl-sms"}
    except Exception as e:  # noqa: BLE001
        return {"skipped": f"send failed: {e!r}"}
