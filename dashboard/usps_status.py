"""Read USPS tracking-status emails and advance the orders they belong to.

Click-N-Ship tells us a label was BOUGHT. It never tells us the parcel moved, so
an order stays 'new'/'packed' after the customer already has their tracking
number. Until 2026-09-10 nothing closed that gap: /api/cron/easypost-sync would
have, but EasyPost is unconfigured (production key held), so it reports
'easypost_unconfigured' and does nothing.

USPS also emails every scan from auto-reply@tracking.usps.com, and Rae now ticks
the tracking-notification box on every label. That makes the mailbox a complete
status feed with no carrier API and no billing plan.

WHY THE BODY AND NOT THE SUBJECT. USPS reuses one subject ("Expected Delivery
by ...") for label-created, in-transit and out-for-delivery alike, and Gmail
truncates long subjects mid-number. The body's first sentence is the only thing
that distinguishes a bought label from a moving parcel, and its labelled
'Tracking Number:' is the only reliable number. All 22 wordings observed in
Glen's mailbox on 2026-09-10 are covered by the phrase tables below.

THE GUARD THAT MATTERS. A label-created email must never advance an order.
Buying a label is not carrier acceptance. Marking an order shipped before the
parcel moves is exactly the mistake this pillar exists to prevent, so
PRE_TRANSIT is counted and held, never acted on.
"""

import base64
import re

from dashboard.tracking import shipment_by_tracking

# Ordered weakest to strongest. The caller only ever acts on the strongest state
# seen for a parcel in the window, so a burst of scan emails is one decision.
PRE_TRANSIT = "pre_transit"
IN_TRANSIT = "in_transit"
OUT_FOR_DELIVERY = "out_for_delivery"
DELIVERED = "delivered"

_RANK = {PRE_TRANSIT: 0, IN_TRANSIT: 1, OUT_FOR_DELIVERY: 2, DELIVERED: 3}

# Only these two reach _advance_orders_by_tracking_status as real evidence;
# PRE_TRANSIT is held and OUT_FOR_DELIVERY/IN_TRANSIT both mean 'shipped' there.
ACTIONABLE = (IN_TRANSIT, OUT_FOR_DELIVERY, DELIVERED)

SENDER = "auto-reply@tracking.usps.com"

# A delivery claim must be affirmative. 'could not be delivered' and 'attempted
# to deliver' are failures, and reading either as delivered would close an order
# and open a coaching window for a parcel still in the network.
_DELIVERY_BLOCKERS = (
    "could not be delivered", "not delivered", "unable to deliver",
    "undeliverable", "attempted to deliver", "delivery attempt",
    "returned to sender", "being returned",
)
_DELIVERED_PHRASES = ("was delivered", "has been delivered", "were delivered")
_OUT_FOR_DELIVERY_PHRASES = ("is out for delivery", "out for delivery on")
_IN_TRANSIT_PHRASES = (
    "arrived at our", "arrived at the post office", "departed our",
    "moving within the usps network", "in transit to the next facility",
    "is currently in transit", "arrived at a", "accepted at",
    # Acceptance. "picked up by" catches a shipping partner; USPS phrases its own
    # counter pickup as "USPS picked up your item", which matched nothing and left
    # a real acceptance reading as unrecognised. Found 2026-09-10 by the sweep's own
    # unparsed_emails counter, on a parcel accepted in Hilo the same afternoon.
    "picked up your item", "picked up item",
    "picked up by", "usps in possession of item",
)
# Sent the moment a label is bought, before the parcel exists to the network.
_PRE_TRANSIT_PHRASES = (
    "usps expects to deliver your package by",
    "usps expects to deliver your package on",
    "shipping label created", "pre-shipment info sent",
)

_TRACKING_LABELLED = re.compile(r"tracking\s*number\s*:?\s*([0-9]{20,26})", re.I)
_TRACKING_BARE = re.compile(r"\b(9[24]0[0-9]{19})\b")


def _text_of(payload):
    """Flatten a Gmail payload to plain text, preferring text/plain parts."""
    plain, html = [], []

    def walk(part):
        data = (part.get("body") or {}).get("data")
        mime = part.get("mimeType") or ""
        if data:
            try:
                raw = base64.urlsafe_b64decode(data + "==").decode("utf8", "replace")
            except Exception:  # noqa: BLE001 - a bad part must not kill the sweep
                raw = ""
            (plain if mime == "text/plain" else html).append(raw)
        for child in part.get("parts") or []:
            walk(child)

    walk(payload or {})
    text = "\n".join(plain) if plain else "\n".join(html)
    text = re.sub(r"<[^>]+>", " ", text)
    text = text.replace("&nbsp;", " ").replace("&#160;", " ")
    return re.sub(r"\s+", " ", text).strip()


def parse_status_email(subject, body_text):
    """Classify one USPS status email. Returns {'tracking', 'status'}.

    `status` is None when the wording is one USPS has not sent before. That is
    deliberate: an unrecognised sentence must not be guessed into a state.
    """
    body = re.sub(r"\s+", " ", str(body_text or ""))
    low = body.lower()

    tracking = None
    hit = _TRACKING_LABELLED.search(body)
    if hit:
        tracking = hit.group(1)
    else:
        bare = _TRACKING_BARE.search(re.sub(r"[^0-9]", " ", body))
        if bare:
            tracking = bare.group(1)
        else:  # last resort only; Gmail truncates long subjects mid-number
            subj_hit = _TRACKING_BARE.search(re.sub(r"[^0-9]", " ", str(subject or "")))
            tracking = subj_hit.group(1) if subj_hit else None

    # Strongest first. An out-for-delivery body also says "USPS expects to
    # deliver your package today", so checking pre-transit early would demote a
    # parcel already on the truck.
    status = None
    if any(p in low for p in _DELIVERED_PHRASES) and not any(
            b in low for b in _DELIVERY_BLOCKERS):
        status = DELIVERED
    elif any(p in low for p in _OUT_FOR_DELIVERY_PHRASES):
        status = OUT_FOR_DELIVERY
    elif any(p in low for p in _IN_TRANSIT_PHRASES):
        status = IN_TRANSIT
    elif any(p in low for p in _PRE_TRANSIT_PHRASES):
        status = PRE_TRANSIT

    return {"tracking": tracking, "status": status}


def strongest(statuses):
    """The furthest state in a list, ignoring anything unrecognised."""
    known = [s for s in statuses if s in _RANK]
    if not known:
        return None
    return max(known, key=lambda s: _RANK[s])


def run_status_sweep(cx, service, *, days=3, max_messages=200, dry_run=False,
                     advance=None, log=None):
    """Read USPS status emails and advance each parcel's orders once.

    `advance` is injected (app passes _advance_orders_by_tracking_status) so this
    module never imports app. Returns a summary dict; the caller jsonifies it.
    """
    if log is None:
        def log(_msg):
            return None

    # Which mailbox this actually read. Without it a wrong-account token looks
    # identical to a genuinely empty inbox: both report zero emails. That is what
    # hid the 60-day Click-N-Ship outage.
    try:
        mailbox = service.users().getProfile(
            userId="me").execute().get("emailAddress")
    except Exception:  # noqa: BLE001 - identification only, never fail the run
        mailbox = None

    query = f"from:{SENDER} newer_than:{int(days)}d"
    listing = service.users().messages().list(
        userId="me", q=query, maxResults=max_messages).execute()
    msg_ids = [m["id"] for m in listing.get("messages", [])]

    mode = "DRY-RUN" if dry_run else "LIVE"
    log(f"{mode} | {len(msg_ids)} USPS status email(s) in last {days}d")

    # Collapse the window to one decision per parcel before touching any order.
    per_parcel = {}
    unparsed = 0
    for mid in msg_ids:
        msg = service.users().messages().get(
            userId="me", id=mid, format="full").execute()
        headers = (msg.get("payload") or {}).get("headers") or []
        subject = next((h["value"] for h in headers
                        if h.get("name", "").lower() == "subject"), "")
        parsed = parse_status_email(subject, _text_of(msg.get("payload")))
        if not parsed["tracking"] or not parsed["status"]:
            unparsed += 1
            continue
        per_parcel.setdefault(parsed["tracking"], []).append(parsed["status"])

    # 'acted' counts parcels we handed to the advance function. 'cards_reported' is
    # what that function said it touched, and the two differ: a delivered signal
    # returns 1 even when the shipment resolves to no member order at all, which is
    # the common case for a parcel whose order was never linked. Reporting only
    # 'acted' would read as an order moving when nothing on the board changed.
    summary = {
        "mode": mode, "mailbox": mailbox, "days": int(days),
        "emails": len(msg_ids), "parcels": len(per_parcel),
        "acted": 0, "cards_reported": 0, "would_act": 0, "pre_transit_held": 0,
        "unknown_parcels": 0, "unparsed_emails": unparsed, "errors": 0,
        # A count with no reason is not diagnosable. The first live cron run
        # reported errors=1 and there was no way to learn why, because the
        # endpoint passed no logger and the exception text died in the sweep.
        "first_error": None,
    }

    for tracking, seen in per_parcel.items():
        state = strongest(seen)
        if state not in ACTIONABLE:
            # A bought label, nothing more. Held on purpose.
            summary["pre_transit_held"] += 1
            log(f"  {tracking}: {state} — held, a label is not acceptance")
            continue
        if shipment_by_tracking(cx, tracking) is None:
            # Rae's mailbox carries her own USPS mail too. An unknown parcel must
            # never reach the order tables.
            summary["unknown_parcels"] += 1
            log(f"  {tracking}: {state} but no shipment row — skipped")
            continue
        if dry_run:
            summary["would_act"] += 1
            log(f"  {tracking}: would act on {state}")
            continue
        try:
            reported = advance(cx, tracking, state)
        except Exception as exc:  # noqa: BLE001 - one bad parcel must not stop the rest
            summary["errors"] += 1
            if summary["first_error"] is None:
                summary["first_error"] = f"{tracking}: {exc!r}"[:300]
            log(f"  {tracking}: advance failed: {exc!r}")
            continue
        summary["acted"] += 1
        try:
            summary["cards_reported"] += int(reported or 0)
        except (TypeError, ValueError):
            pass
        log(f"  {tracking}: {state} — acted, function reported {reported}")

    return summary
