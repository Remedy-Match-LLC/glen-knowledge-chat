"""Amazon SES sender, replacing GoHighLevel's send. Switched off until SES_SEND_ENABLED.

Account and identity: `mail.remedymatch.com`, verified in SES us-west-2 on
2026-09-17, with DKIM and a custom MAIL FROM at `bounce.mail.remedymatch.com`.
Production access was requested the same day. Until AWS grants it, SES accepts
mail only to verified addresses and to its mailbox simulator.

Nothing calls this yet. Each GoHighLevel send moves over one at a time, per the
replacement plan, and only after bounce and complaint events reach the
suppression list through `dashboard/ses_events.py`.

Two kinds of mail, told apart by the caller:
  marketing=True   checks the whole suppression list (bounces, opt-outs, the hub's
                   refusal tag) and carries an unsubscribe footer plus the RFC 8058
                   one-click headers.
  marketing=False  transactional: orders, shipping, sign-in links. It still stops
                   at a dead address or a spam complaint, but reaches a person who
                   only opted out of marketing. It carries no unsubscribe footer.
"""
from __future__ import annotations

import os
from email import policy as _policy
from email.message import EmailMessage
from email.utils import formatdate, make_msgid

from dashboard import email_suppression as es
from dashboard import unsubscribe as un

ENABLED_ENV = "SES_SEND_ENABLED"
DEFAULT_FROM = "Dr. Glen Swartwout <drglen@mail.remedymatch.com>"
MSGID_DOMAIN = "mail.remedymatch.com"

# The default policy folds any header over 78 characters, and a header with no
# spaces to fold at gets RFC 2047 encoded. That turns the List-Unsubscribe URL
# into =?utf-8?q?...?=, which no mail client can POST to. 998 is the RFC 5322 limit.
_POLICY = _policy.SMTP.clone(max_line_length=998)

# Table rows that block transactional mail too. A hard bounce is a dead address,
# and a complaint means the person marked us as spam. An opt-out does not stop an
# order confirmation.
TRANSACTIONAL_BLOCKS = frozenset({"hard", "complaint"})


def enabled() -> bool:
    return os.environ.get(ENABLED_ENV, "").strip().lower() in ("1", "true", "yes", "on")


def region() -> str:
    return os.environ.get("SES_REGION", "us-west-2").strip() or "us-west-2"


def block_reason(cx, email: str, *, marketing: bool):
    """Why this address must not get this kind of mail, or None."""
    email = es.normalize(email)
    if not email:
        return "no-address"
    if marketing:
        return es.suppression_reason(cx, email)
    row = es._table_reason(cx, email)
    return row if row in TRANSACTIONAL_BLOCKS else None


def build_message(to, subject, text, html=None, *, from_addr=None, reply_to=None,
                  unsubscribe_scope=None) -> EmailMessage:
    """The MIME message. A scope adds the footer and the one-click headers."""
    to = es.normalize(to)
    msg = EmailMessage(policy=_POLICY)
    msg["From"] = from_addr or os.environ.get("SES_FROM") or DEFAULT_FROM
    msg["To"] = to
    msg["Subject"] = subject
    msg["Date"] = formatdate(localtime=False)
    msg["Message-ID"] = make_msgid(domain=MSGID_DOMAIN)
    if reply_to:
        msg["Reply-To"] = reply_to
    if unsubscribe_scope:
        url = un.unsubscribe_url(to, unsubscribe_scope)
        msg["List-Unsubscribe"] = f"<{url}>"
        msg["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"
        text = text + un.footer_text(to, unsubscribe_scope)
        if html is not None:
            html = html + un.footer_html(to, unsubscribe_scope)
    msg.set_content(text)
    if html is not None:
        msg.add_alternative(html, subtype="html")
    return msg


def _client():
    import boto3
    return boto3.client("sesv2", region_name=region())


def send(cx, to, subject, text, html=None, *, marketing: bool, from_addr=None,
         reply_to=None, scope: str = un.GLOBAL, client=None) -> dict:
    """Send one message through SES. Returns what happened, never raises on a skip.

    `client` is for tests. Without one, a pytest run never reaches AWS."""
    to = es.normalize(to)
    reason = block_reason(cx, to, marketing=marketing)
    if reason:
        return {"skipped": "suppressed", "reason": reason}
    if not enabled():
        return {"skipped": "disabled"}
    if client is None and os.environ.get("PYTEST_CURRENT_TEST"):
        return {"skipped": "pytest"}
    msg = build_message(to, subject, text, html, from_addr=from_addr, reply_to=reply_to,
                        unsubscribe_scope=scope if marketing else None)
    kwargs = {
        "FromEmailAddress": msg["From"],
        "Destination": {"ToAddresses": [to]},
        "Content": {"Raw": {"Data": msg.as_bytes()}},
    }
    config_set = os.environ.get("SES_CONFIGURATION_SET", "").strip()
    if config_set:
        kwargs["ConfigurationSetName"] = config_set
    resp = (client or _client()).send_email(**kwargs)
    return {"sent": True, "message_id": resp.get("MessageId")}
