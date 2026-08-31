"""Unsubscribe links for promotional email.

GHL appends its own unsubscribe footer to `source: "workflow"` mail only. Anything
we send through the conversations API (`source: "app"`) arrives with none, so we
mint our own. Verified 2026-08-30 by reading delivered bodies: every workflow
message carries a services.msgsndr.com unsubscribe token, every API message
carries nothing.

An opt-out is recorded in `email_suppression` with `bounce_type='optout'`, so every
sender already calling `is_suppressed` honors it without further change.

The footer is opt-in per call site. Transactional mail (invoices, magic links,
portal-ready notices) must NOT carry one.

The signing here is duplicated in `03 Marketing/ghl-email-automation/unsub.py`,
which runs on Glen's Mac and cannot import from this repo. Both test suites pin the
same vector so the two cannot drift apart.
"""
from __future__ import annotations

import hashlib
import hmac
import html as _html
import os
from urllib.parse import quote

_SECRET = os.environ.get("CONSOLE_SECRET") or os.environ.get("WEBHOOK_SECRET", "")

GLOBAL = "global"
POSTAL = "Remedy Match LLC, Hilo, Hawaii"


def _norm(email: str) -> str:
    return (email or "").strip().lower()


def _base() -> str:
    return os.environ.get("PUBLIC_BASE_URL", "https://illtowell.com").rstrip("/")


def sign(email: str, scope: str) -> str:
    """HMAC over address and scope. Mirrors _portal_claim_sign in app.py."""
    msg = f"unsub:{scope}:{_norm(email)}".encode()
    return hmac.new(_SECRET.encode(), msg, hashlib.sha256).hexdigest()[:40]


def verify(email: str, scope: str, sig: str) -> bool:
    if not sig:
        return False
    return hmac.compare_digest(sign(email, scope), sig)


def unsubscribe_url(email: str, scope: str = GLOBAL) -> str:
    e = _norm(email)
    return (f"{_base()}/email/unsubscribe?e={quote(e)}"
            f"&scope={quote(scope)}&s={sign(e, scope)}")


def footer_text(email: str, scope: str = GLOBAL) -> str:
    return ("\n\n---\nYou are receiving this because you signed up at Remedy Match.\n"
            f"Unsubscribe: {unsubscribe_url(email, scope)}\n{POSTAL}")


def footer_html(email: str, scope: str = GLOBAL) -> str:
    url = _html.escape(unsubscribe_url(email, scope), quote=True)
    return ('<div style="margin-top:28px;padding-top:14px;border-top:1px solid #ddd;'
            'font-family:Arial,sans-serif;font-size:12px;line-height:1.5;color:#777">'
            "You are receiving this because you signed up at Remedy Match.<br>"
            f'<a href="{url}" style="color:#777">Unsubscribe</a><br>'
            f"{_html.escape(POSTAL)}</div>")
