"""Tell a client their free product review is ready.

Glen's ruling, 2026-09-09: confirming a review should email the client. Until now a
finished review sat in the portal unannounced, so anyone who did not happen to revisit
never learned it was there.

The mail carries no review text. It says the review is ready and links the person to
their own portal, which is where the text lives and where access is already gated.

Failure-isolated by design. `notify_confirmed` never raises, so a mail problem can
never roll back a confirm that already succeeded in the database.
"""
import os

from dashboard import db

SUBJECT = "Your supplement review is ready"


def _portal_base():
    """Same rule as app.portal_base: the client portal host, read at call time."""
    base = (os.environ.get("PORTAL_BASE_URL")
            or os.environ.get("PUBLIC_BASE_URL") or "").rstrip("/")
    return base


def _product_label(name, brand):
    name = (name or "").strip()
    brand = (brand or "").strip()
    return f"{name} by {brand}" if brand else name


def body_for(product_name, product_brand, link):
    """The wording, split out so a test can read it without sending anything."""
    label = _product_label(product_name, product_brand)
    text = (
        "Aloha,\n\n"
        f"Your review of {label} is ready.\n\n"
        "It is on your Upcoming Live Events and account page, under Free Product "
        "Review:\n\n"
        f"{link}\n\n"
        "Please keep that link to yourself.\n\n"
        "With aloha,\n"
        "Dr. Glen Swartwout"
    )
    html = (
        '<div style="font-family:Arial,sans-serif;font-size:16px;line-height:1.55">'
        "<p>Aloha,</p>"
        f"<p>Your review of {label} is ready.</p>"
        "<p>It is on your account page, under Free Product Review:</p>"
        f'<p><a href="{link}">{link}</a></p>'
        "<p>Please keep that link to yourself.</p>"
        "<p>With aloha,<br>Dr. Glen Swartwout</p>"
        "</div>"
    )
    return text, html


def notify_confirmed(cx, review_id):
    """Email the client that review `review_id` is ready. Returns a small dict
    describing what happened, and never raises.

    Skips when: the row is missing, it is not confirmed, the address is suppressed,
    no portal base URL is configured, or GoHighLevel is not configured.
    """
    try:
        from dashboard import supplement_reviews as _sr
        row = _sr.get(cx, review_id)
        if not row:
            return {"sent": False, "reason": "no-row"}
        if row.get("status") != "confirmed":
            return {"sent": False, "reason": "not-confirmed"}
        email = (row.get("email") or "").strip().lower()
        if not email:
            return {"sent": False, "reason": "no-email"}

        try:
            from dashboard import email_suppression as _es
            if _es.is_suppressed(cx, email):
                return {"sent": False, "reason": "suppressed"}
        except Exception as e:
            # Fail closed. An unreadable suppression list is not permission to send.
            print(f"[review-ready] suppression check failed for {email}: {e!r}",
                  flush=True)
            return {"sent": False, "reason": "suppression-unreadable"}

        base = _portal_base()
        if not base:
            return {"sent": False, "reason": "no-portal-base"}

        from dashboard import client_portal as _cp
        from dashboard import notify_state as _ns
        # Self-sufficient: the confirm action's connection has only had the review
        # tables created, so do not assume the portal tables are already there.
        _cp.init_client_portal_table(cx)
        _ns.init_table(cx)
        token = _cp.ensure_token(cx, email)
        link = f"{base}/portal/{token}"

        from dashboard import ghl_email as _ghl
        if not _ghl.is_configured():
            return {"sent": False, "reason": "ghl-not-configured"}
        text, html = body_for(row.get("product_name"), row.get("product_brand"), link)
        res = _ghl.send_via_ghl(email, SUBJECT, html=html, text=text) or {}
        # send_via_ghl no-ops under pytest and says so. Report that rather than
        # claiming a send, so a test run cannot read as proof that mail goes out.
        if res.get("skipped"):
            return {"sent": False, "reason": "skipped:" + str(res["skipped"]),
                    "email": email}
        return {"sent": True, "email": email}
    except Exception as e:
        print(f"[review-ready] send failed for review {review_id}: {e!r}", flush=True)
        return {"sent": False, "reason": "error"}
