#!/usr/bin/env python3
"""CNS tracking watcher — draft customer tracking emails from USPS Click-N-Ship.

Flow (mirrors the E4L Gmail watcher):
  1. Search Glen's mailbox for unprocessed "USPS - Click-N-Ship(R) Payment
     Confirmation" emails from noreply-ecns@usps.com.
  2. Parse each into shipments (tracking #, recipient, address).
  3. Match each recipient name -> GHL contact email (with a confidence score).
  4. Send high-confidence tracking notices through the client's GHL contact.
     Fuzzy/unresolved matches stay Gmail drafts for human review.
  5. Record tracking, scheduled delivery, notification audit, and the safe order
     link in shipments so re-runs never double-send.

DEFAULT IS DRY-RUN: prints what it would do and mutates nothing (no drafts, no
DB writes). Pass --live to actually create drafts.

Run with GHL creds for real matching:
    doppler run -- python3 cns_tracking_watcher.py            # dry-run
    doppler run -- python3 cns_tracking_watcher.py --live     # create drafts

Auth: needs a Gmail token with gmail.readonly + gmail.compose at
~/.config/google/token.json (mint via scripts/gmail_tracking_auth.py).
"""

from __future__ import annotations

# Gevent must monkey-patch BEFORE ssl/socket are imported. The live run imports
# `app` (for the harvest→onboard helpers), whose own monkey.patch_all() then runs
# after googleapiclient/requests have already pulled in ssl — which recurses to a
# RecursionError on Python 3.12+ (see conftest.py / gevent issue #1016). Patching
# here, first, makes app's later patch_all() a no-op. Guarded + best-effort so the
# watcher still runs if gevent is absent.
try:
    from gevent import monkey
    if not monkey.is_module_patched("ssl"):
        monkey.patch_all()
except ImportError:
    pass

import argparse
import base64
import os
import sqlite3
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

from dashboard.tracking import (
    parse_cns_confirmation,
    build_tracking_email,
    init_tracking_schema,
    record_shipment,
    shipment_exists,
    shipment_by_tracking,
    link_shipment_to_orders,
    normalize_delivery_date,
)

# Confidence tiers we trust enough to email the customer without a human glance.
#   "high"      — exactly one GHL contact whose name matches the ship-to EXACTLY
#   "harvested" — a single email precision-parsed from the order confirmation
# "medium" (single but fuzzy GHL match) and "low"/none stay drafts for review, so
# --auto-send never mails a guessed recipient.
AUTO_SEND_CONFIDENCES = ("high", "harvested", "address-board", "address-fmp")

GMAIL_QUERY = 'from:noreply-ecns@usps.com subject:"Payment Confirmation"'
TOKEN_PATH = Path(os.environ.get(
    "GMAIL_TOKEN_PATH", str(Path.home() / ".config" / "google" / "token.json")))
SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.compose",
]


def _db_path():
    base = os.environ.get("DATA_DIR", str(Path(__file__).resolve().parent))
    return str(Path(base) / "chat_log.db")


def _iso_now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


# ── Gmail plumbing (thin; the testable logic is handle_confirmation) ─────────

def gmail_service(token_path=TOKEN_PATH):
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build
    creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)
    if creds.expired and creds.refresh_token:
        # Never save: the file is shared, and saving narrows its scopes for every job.
        creds.refresh(Request())
    return build("gmail", "v1", credentials=creds)


def _extract_html(payload):
    """Walk a Gmail message payload and return the first text/html body."""
    if payload.get("mimeType") == "text/html":
        data = payload.get("body", {}).get("data")
        if data:
            return base64.urlsafe_b64decode(data).decode("utf-8", "replace")
    for part in payload.get("parts", []) or []:
        html = _extract_html(part)
        if html:
            return html
    return ""


def build_raw(subject, html, text, to=None):
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    if to:
        msg["To"] = to
    msg.attach(MIMEText(text, "plain", "utf-8"))
    msg.attach(MIMEText(html, "html", "utf-8"))
    return base64.urlsafe_b64encode(msg.as_bytes()).decode()


def make_draft_fn(service):
    def draft_fn(to, subject, html, text):
        raw = build_raw(subject, html, text, to=to)
        res = service.users().drafts().create(
            userId="me", body={"message": {"raw": raw}}).execute()
        return res.get("id")
    return draft_fn


def make_ghl_send_fn():
    """Send through the client's GHL contact and return its conversation message id."""
    from dashboard import ghl_email
    def send_fn(to, subject, html, text):
        res = ghl_email.send_via_ghl(
            to, subject, html=html, text=text, from_name="Dr. Glen Swartwout")
        return res.get("id")
    return send_fn


def make_gmail_search_fn(service):
    """Return search(query) -> [{'sender','body'}] over the connected mailbox."""
    def _plain(payload):
        if payload.get("mimeType", "").startswith("text/"):
            data = payload.get("body", {}).get("data")
            if data:
                return base64.urlsafe_b64decode(data).decode("utf-8", "replace")
        for p in payload.get("parts", []) or []:
            t = _plain(p)
            if t:
                return t
        return ""
    def search(query):
        q = query  # caller passes the ship-to name; harvest widens as needed
        listing = service.users().messages().list(
            userId="me", q=q, maxResults=10).execute()
        out = []
        for m in listing.get("messages", []):
            full = service.users().messages().get(
                userId="me", id=m["id"], format="full").execute()
            headers = {h["name"].lower(): h["value"]
                       for h in full.get("payload", {}).get("headers", [])}
            out.append({"sender": headers.get("from", ""),
                        "body": _plain(full.get("payload", {}))})
        return out
    return search


def make_harvest_fn(gmail_search):
    from dashboard.order_harvest import harvest_buyer
    return lambda name: harvest_buyer(gmail_search, name)


def make_persist_contact():
    """Upsert the harvested buyer into GHL; onboard ONLY genuine new storefront
    buyers (source 'neworder' AND newly created). Returns {contact_id, onboarded}."""
    from app import ghl_upsert_contact, ghl_add_to_pipeline, ghl_enroll_workflow
    def persist(identity, ship_to_name):
        tag = ("source:gk-purchase" if identity.get("source") == "neworder"
               else "source:phone-email-order")
        cid, created, err = ghl_upsert_contact(
            identity["email"], first_name=identity.get("first") or "",
            last_name=identity.get("last") or "", phone=identity.get("phone") or "",
            source_tag=tag, extra_tags=["tracking-harvest"])
        onboarded = False
        if not err and cid and identity.get("source") == "neworder" and created:
            _opp, e_pipe = ghl_add_to_pipeline(cid, ship_to_name, identity["email"])
            _wf, e_wf = ghl_enroll_workflow(cid)
            onboarded = not e_wf          # workflow enroll is what actually onboards
            if e_pipe or e_wf:
                print(f"[tracking-harvest] onboarding partial for {identity['email']}: "
                      f"pipeline_err={e_pipe} workflow_err={e_wf}", flush=True)
        return {"contact_id": cid, "onboarded": onboarded}
    return persist


# ── Core decision logic (pure of Gmail/GHL; unit-tested) ─────────────────────

def handle_confirmation(html, msg_id, cx, find_contact, draft_fn,
                        harvest_fn=None, persist_contact=None, send_fn=None,
                        auto_send=False, dry_run=True, link_orders=False,
                        address_resolve=None):
    """Process one confirmation email's HTML. Returns a list of per-shipment
    result dicts. In dry-run, no drafts/sends/GHL writes/DB writes happen.

    When auto_send is on (and a send_fn is supplied), a recipient resolved at an
    AUTO_SEND_CONFIDENCES tier is emailed outright (status 'sent') instead of
    drafted. Fuzzy ('medium') and unresolved ('low'/none) recipients always draft,
    so an uncertain match is never auto-mailed."""
    parsed = parse_cns_confirmation(html)
    results = []
    fresh = []
    for s in parsed["shipments"]:
        if shipment_exists(cx, s["tracking"]):
            result = {"tracking": s["tracking"],
                      "recipient": s["recipient_name"],
                      "action": "skipped (already processed)"}
            # Backfill order links for confirmations processed before this feature
            # shipped. This never creates another draft or customer email.
            if link_orders and not dry_run:
                row = shipment_by_tracking(cx, s["tracking"])
                try:
                    shipment_id = row["id"] if hasattr(row, "keys") else row[0]
                    resolved = (row["resolved_email"] if hasattr(row, "keys")
                                else row[5])
                    linked = link_shipment_to_orders(
                        cx, shipment_id, s, resolved_email=(resolved or ""))
                    result["order_link"] = linked
                except Exception as exc:
                    result["order_link"] = {"status": "error", "order_ids": [],
                                            "reason": str(exc)}
            results.append(result)
            continue
        fresh.append(s)

    # Glen, 2026-10-14: "Please consolidate tracking number emails when going to the same
    # address the same day." He chose "same checkout": the boxes of one confirmation that
    # go to one address and one person become ONE email listing every number, sent as
    # soon as now. Fukushima's 6 boxes on 2026-10-14 had made 6 drafts. The recipient is
    # resolved once per name at an address, and the groups are formed after resolution so
    # two names that resolve to one email still get one email.
    resolved_for = {}
    groups = {}
    for s in fresh:
        rkey = (_addr_key(s), _norm(s["recipient_name"]))
        if rkey not in resolved_for:
            resolved_for[rkey] = _resolve(s, find_contact, address_resolve, harvest_fn)
        r = resolved_for[rkey]
        # The confidence is in the key: a box resolved with less certainty never rides
        # along on another box's auto-send (review round 2).
        gkey = (rkey[0], r["conf"],
                ("to", r["to"].strip().lower()) if r["to"] else ("name", rkey[1]))
        groups.setdefault(gkey, {"r": r, "shipments": []})["shipments"].append(s)

    for g in groups.values():
        results.extend(_notify_group(g["r"], g["shipments"], parsed, msg_id, cx, draft_fn,
                                     persist_contact, send_fn, auto_send, dry_run,
                                     link_orders))
    return results


def _norm(value):
    return " ".join(str(value or "").lower().split())


def _addr_key(s):
    """One address however the label spells it. Falls back to the printed address block
    when the label has no street or ZIP, so two unparsed labels never merge by accident."""
    from dashboard import tracking_recipient as _trc
    z, sk = _trc.zip5(s.get("zip")), _trc.street_key(s.get("street"))
    if z and sk:
        return ("addr", z, sk)
    return ("block", _norm(s.get("address_block")), _norm(s.get("recipient_name")))


def _resolve(s, find_contact, address_resolve, harvest_fn):
    """Who this box goes to: {to, conf, ghl_contact_id, review_reason, harvested,
    path, addr_failed}.

    `path` says how far the drop-ship hold may trust the answer: 'dropship' (a
    drop-ship order at the address), 'own' (a board order whose own name agrees with
    the label), or 'other' (anything else, including every name-only fallback)."""
    match = find_contact(s["recipient_name"])
    conf = match["confidence"] if match else "none"
    to = match["email"] if (match and conf in ("high", "medium")) else None
    ghl_contact_id = (match or {}).get("contact_id")

    # The SHIP-TO ADDRESS comes first (Glen, 2026-09-22): a board order at the
    # address, else a FileMaker client there whose name agrees. See
    # dashboard/tracking_recipient.py. A name-only GHL match then counts only when
    # it names the same email; FileMaker against GHL with different emails is a
    # conflict of equal standing and goes to review. The board outranks GHL.
    review_reason = ""
    addr = None
    addr_failed = False
    to_client = False
    path = "other"
    if address_resolve is not None:
        try:
            addr = address_resolve(s)
        except Exception as exc:
            addr = None
            addr_failed = True
            review_reason = f"address lookup failed: {type(exc).__name__}"
    ghl_email = (to or "").strip().lower() or None
    if addr and addr.get("email"):
        a_email = addr["email"]
        if addr.get("source") == "fmp" and ghl_email and ghl_email != a_email:
            to, conf = None, "conflict"
            review_reason = f"FileMaker has {a_email}, GHL has {ghl_email}"
        else:
            to, conf = a_email, f"address-{addr.get('source')}"
            if addr.get("dropship"):
                path = "dropship"
                to_client = bool(addr.get("to_client"))
            elif addr.get("source") == "board" and addr.get("name_agrees"):
                path = "own"
            if ghl_email != a_email:
                ghl_contact_id = None      # that GHL contact is someone else
    elif addr and addr.get("conflict"):
        to, conf = None, "conflict"
        review_reason = addr.get("reason") or "several people at the address"

    # No confident match of any kind: try a precision-safe harvest from order
    # emails. Never after a conflict: harvest is name-only and would pick a side.
    harvested = None
    if to is None and conf != "conflict" and harvest_fn is not None:
        try:
            harvested = harvest_fn(s["recipient_name"])
        except Exception as exc:
            # One failing search must not stop every other box in the checkout; this one
            # goes to review with the reason (review round 2).
            harvested = None
            review_reason = review_reason or f"order-email search failed: {type(exc).__name__}"
        if harvested and harvested.get("email"):
            to = harvested["email"]
            conf = "harvested"
    return {"to": to, "conf": conf, "ghl_contact_id": ghl_contact_id,
            "review_reason": review_reason, "harvested": harvested,
            "path": path, "addr_failed": addr_failed, "to_client": to_client}


def _dropship_hold(r, shipments, cx):
    """(held, reason) for one email about to go out.

    Glen, 2026-10-21: "Give them the option, with fallback being no emails." A
    drop-ship client gets mail from us only when the practitioner's switch is on.
    When the watcher cannot prove a parcel is not a held client's, it drafts: a wrong
    send is the failure that matters, and an extra draft is cheap. The holds are read
    here, right before the send, so a switch turned off mid-run holds the next email."""
    from dashboard import tracking_recipient as _trc
    to = (r.get("to") or "").strip().lower()
    if not to:
        return False, ""
    holds = _trc.dropship_holds(cx)
    if holds is None:
        return True, "Held: the drop-ship list could not be read."
    if r.get("addr_failed"):
        return True, f"Held: {r.get('review_reason') or 'address lookup failed'}."
    path = r.get("path") or "other"
    if path == "dropship" and not r.get("to_client"):
        # A drop-ship going to its practitioner, unless that practitioner is herself
        # another practitioner's held client at this very address.
        hit = [h for s in shipments for h in _trc.holds_at_address(holds, s)
               if h["client"] and h["client"] == to]
    elif path == "own":
        # The board order is this person's own purchase, not a drop-ship, and the label
        # names its buyer by the strict match. Glen's rule covers drop-ship orders, not
        # a person's own purchases, so a former drop-ship client's own order still
        # sends, even to a held email.
        hit = []
    else:
        # Every name-only fallback, a board order that does not name the label, and a
        # drop-ship going to its client: a second practitioner's hold still applies.
        hit = [h for s in shipments for h in _trc.holds_touching(holds, s, to)]
    if not hit:
        return False, ""
    orders = ", ".join(sorted({f"#{h['order_id']}" for h in hit}))
    return True, f"Held: drop-ship client, switch off (order {orders})."


def _notify_group(r, shipments, parsed, msg_id, cx, draft_fn, persist_contact, send_fn,
                  auto_send, dry_run, link_orders):
    """One email (sent or drafted) for every box in the group; one shipments row each."""
    to, conf = r["to"], r["conf"]
    ghl_contact_id, harvested = r["ghl_contact_id"], r["harvested"]
    review_reason = r["review_reason"]
    first = shipments[0]
    numbers = [s["tracking"] for s in shipments]

    # A held parcel is drafted, never sent, with no recipient at all: one click in
    # Gmail must not mail the client, nor tell a practitioner about a stranger. The
    # draft and the shipments row carry the reason. No GHL contact is made either.
    guarded, hold_reason = _dropship_hold(r, shipments, cx)
    if guarded:
        to = None
        conf, ghl_contact_id, harvested = "dropship-held", None, None
        review_reason = hold_reason

    # Auto-send only high-confidence recipients; everything else drafts.
    send_eligible = (auto_send and to is not None and not guarded
                     and conf in AUTO_SEND_CONFIDENCES)
    status = ("sent" if send_eligible else
              "drafted" if to else "needs_review")
    email = build_tracking_email(numbers if len(numbers) > 1 else numbers[0],
                                 first["recipient_name"], resolved_email=to,
                                 hold_note=hold_reason if guarded else None)

    draft_id = None
    onboarded = False
    notification_error = None
    if dry_run:
        verb = "would send" if send_eligible else "would draft"
        action = f"{verb} (harvested)" if conf == "harvested" else verb
    else:
        if harvested and conf == "harvested" and persist_contact is not None:
            pc = persist_contact(harvested, first["recipient_name"]) or {}
            ghl_contact_id = pc.get("contact_id") or ghl_contact_id
            onboarded = bool(pc.get("onboarded"))
        if send_eligible and send_fn is not None:
            try:
                draft_id = send_fn(to=to, subject=email["subject"],
                                   html=email["html"], text=email["text"])
                action = "sent via GHL"
            except Exception as exc:
                # Never substitute a different outbound channel. Keep a Gmail
                # draft for review and audit the GHL failure for operations.
                notification_error = f"{type(exc).__name__}: {exc}"
                draft_id = draft_fn(to=to, subject=email["subject"],
                                    html=email["html"], text=email["text"])
                status = "send_failed"
                action = "drafted (GHL send failed)"
        else:
            status = "drafted" if to else "needs_review"  # never sent
            draft_id = draft_fn(to=to, subject=email["subject"],
                                html=email["html"], text=email["text"])
            action = "drafted"
    if len(numbers) > 1:
        action += f" (one email for {len(numbers)} boxes)"

    results = []
    for s in shipments:
        order_link = None
        record_error = None
        if not dry_run:
            # One box's failed write must not leave the boxes after it unrecorded: the
            # next run would mail those again (review round 1). Each box is written alone.
            try:
                shipment_id = record_shipment(
                    cx, tracking_number=s["tracking"], order_uuid=parsed["order_uuid"],
                    recipient_name=s["recipient_name"], address_block=s["address_block"],
                    resolved_email=to, match_confidence=conf,
                    ghl_contact_id=ghl_contact_id,
                    draft_id=draft_id, status=status, source_msg_id=msg_id,
                    scheduled_delivery_date=normalize_delivery_date(s.get("delivery_date")),
                    notification_channel=("ghl" if status == "sent" else None),
                    notification_sent_at=(_iso_now() if status == "sent" else None),
                    notification_error=notification_error,
                    hold_reason=(hold_reason if guarded else None))
            except Exception as exc:
                shipment_id = None
                record_error = f"{type(exc).__name__}: {exc}"
                try:
                    cx.rollback()      # on Postgres a failed INSERT blocks the next one
                except Exception:
                    pass

            if link_orders and shipment_id is not None:
                try:
                    order_link = link_shipment_to_orders(
                        cx, shipment_id, s, resolved_email=to or "")
                except Exception as exc:
                    # Tracking delivery is more important than the order-board join;
                    # preserve the shipment/email and surface the linking error.
                    order_link = {"status": "error", "order_ids": [],
                                  "reason": str(exc)}

        results.append({"tracking": s["tracking"], "recipient": s["recipient_name"],
                        "to": to or "(blank — needs review)", "confidence": conf,
                        "status": status, "action": action, "draft_id": draft_id,
                        "onboarded": onboarded, "boxes_in_email": len(numbers),
                        "scheduled_delivery_date": normalize_delivery_date(
                            s.get("delivery_date")),
                        "notification_channel": "ghl" if status == "sent" else None,
                        "notification_error": notification_error,
                        "review_reason": review_reason,
                        "record_error": record_error,
                        "order_link": order_link if not dry_run else None})
    return results


# ── Runner (shared by the CLI and the Render cron endpoint) ──────────────────

def run_watch(*, live=False, auto_send=False, days=14, max_messages=25,
              db_file=None, service=None, log=None):
    """Scan Click-N-Ship confirmations and notify recipients. Returns a summary dict.

    The CLI passes nothing for `service` and builds one from the token file. The
    web endpoint passes a service built from the durable oauth_tokens store, so the
    cron never needs a token file of its own. `log` receives one line per shipment;
    the CLI passes `print`, the endpoint passes None to stay quiet.
    """
    dry_run = not live
    if log is None:
        def log(_msg):
            return None

    try:
        from dashboard.ghl import find_contact_by_name
    except Exception:
        find_contact_by_name = lambda name: None  # noqa: E731

    svc = service or gmail_service()
    draft_fn = make_draft_fn(svc) if not dry_run else (lambda **k: None)
    send_fn = make_ghl_send_fn() if (not dry_run and auto_send) else None
    harvest_fn = make_harvest_fn(make_gmail_search_fn(svc))
    persist_contact = None if dry_run else make_persist_contact()

    # Which mailbox this actually read. Without it a wrong-account token looks
    # identical to a genuinely empty inbox: both report zero confirmations.
    try:
        mailbox = svc.users().getProfile(userId="me").execute().get("emailAddress")
    except Exception:  # noqa: BLE001 - identification only, never fail the run
        mailbox = None

    q = f"{GMAIL_QUERY} newer_than:{days}d"
    listing = svc.users().messages().list(
        userId="me", q=q, maxResults=max_messages).execute()
    msg_ids = [m["id"] for m in listing.get("messages", [])]

    from dashboard import db
    db_file = db_file or _db_path()
    mode = "DRY-RUN" if dry_run else ("LIVE+AUTO-SEND" if auto_send else "LIVE")
    log(f"{mode} | {len(msg_ids)} confirmation "
        f"email(s) in last {days}d | db={db_file}\n")

    totals = {}
    shipments = 0
    with db.connect(db_file) as cx:
        init_tracking_schema(cx)
        for mid in msg_ids:
            msg = svc.users().messages().get(
                userId="me", id=mid, format="full").execute()
            html = _extract_html(msg.get("payload", {}))
            from dashboard import tracking_recipient as _trc
            for r in handle_confirmation(html, mid, cx, find_contact_by_name,
                                         draft_fn, harvest_fn=harvest_fn,
                                         persist_contact=persist_contact,
                                         send_fn=send_fn, auto_send=auto_send,
                                         dry_run=dry_run, link_orders=True,
                                         address_resolve=lambda s, _cx=cx:
                                             _trc.resolve_by_address(_cx, s)):
                shipments += 1
                totals[r["action"]] = totals.get(r["action"], 0) + 1
                log(f"  [{r.get('confidence','-'):>6}] {r['recipient']:<22} "
                    f"{r['tracking']}  -> {r.get('to','')}  ({r['action']})")
                link = r.get("order_link")
                if link:
                    log(f"           order-link: {link['status']} "
                        f"{link.get('order_ids') or ''} — {link.get('reason','')}")

    return {"mode": mode, "dry_run": dry_run, "auto_send": bool(auto_send),
            "days": days, "mailbox": mailbox, "emails": len(msg_ids),
            "shipments": shipments, "actions": totals, "db": db_file}


# ── Entry point ──────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--live", action="store_true",
                    help="actually create Gmail drafts + record (default: dry-run)")
    ap.add_argument("--auto-send", action="store_true",
                    help="email high-confidence + harvested recipients through GHL; "
                         "fuzzy/unresolved and GHL failures stay drafts")
    ap.add_argument("--days", type=int, default=14,
                    help="how many days back to scan (default 14)")
    ap.add_argument("--max", type=int, default=25, help="max emails to scan")
    ap.add_argument("--db", default=None, help="override chat_log.db path")
    args = ap.parse_args()

    summary = run_watch(live=args.live, auto_send=args.auto_send, days=args.days,
                        max_messages=args.max, db_file=args.db, log=print)

    totals = summary["actions"]
    print("\nSummary:", ", ".join(f"{k}: {v}" for k, v in totals.items() if v))
    if summary["dry_run"]:
        print("Dry-run only — no drafts created, nothing recorded. "
              "Re-run with --live when the matches look right.")


if __name__ == "__main__":
    main()
