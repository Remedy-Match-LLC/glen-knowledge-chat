"""Amazon SES sender, replacing GoHighLevel's send. Switched off until SES_SEND_ENABLED.

Account and identity: `mail.remedymatch.com`, verified in SES us-west-2 on
2026-09-17, with DKIM and a custom MAIL FROM at `bounce.mail.remedymatch.com`.
Production access was requested the same day. Until AWS grants it, SES accepts
mail only to verified addresses and to its mailbox simulator.

The free-ebook library email is the first caller (2026-10-29). Each GoHighLevel send moves over one at a time, per the
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
    """On only with the configuration set too: without it, bounces and complaints never
    reach the suppression list, and dead addresses would keep being mailed."""
    return (os.environ.get(ENABLED_ENV, "").strip().lower() in ("1", "true", "yes", "on")
            and bool(os.environ.get("SES_CONFIGURATION_SET", "").strip()))


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
    # One attempt, short timeouts: SendEmail has no idempotency key, so a retry after a
    # lost reply can deliver a second copy.
    import boto3
    from botocore.config import Config
    return boto3.client("sesv2", region_name=region(),
                        config=Config(retries={"total_max_attempts": 1, "mode": "standard"},
                                      connect_timeout=5,
                                      read_timeout=15))


def send(cx, to, subject, text, html=None, *, marketing: bool, from_addr=None,
         reply_to=None, scope: str = un.GLOBAL, client=None,
         prechecked: bool = False) -> dict:
    """Send one message through SES. Returns what happened, never raises on a skip.

    prechecked=True, with cx None, says the caller already ran block_reason and closed
    its connection, so no database connection is held across the network call.
    `client` is for tests. Without one, a pytest run never reaches AWS."""
    to = es.normalize(to)
    if prechecked and cx is not None:
        raise ValueError("prechecked sends take no connection")
    if not prechecked and cx is None:
        raise ValueError("a send needs a connection for the suppression check")
    reason = None if prechecked else block_reason(cx, to, marketing=marketing)
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


# Reply-To for app mail sent through SES: Glen's inbox, where replies went on Gmail.
REPLY_TO = "drglenswartwout@gmail.com"


def deliver(connect, to, subject, text, html=None, *, marketing: bool, scope: str = un.GLOBAL,
            from_name=None, reply_to=REPLY_TO):
    """Try one message through SES, for a caller that has another transport to fall back
    to. Returns (outcome, message_id). outcome is:
      "ses"         sent
      "suppressed"  this address must not get this mail; send it nowhere
      "unknown"     SES may have taken it (a timeout after the request went out); do NOT
                    fall back, or the person can get two copies
      None          SES surely sent nothing (switched off, refused, unreachable): fall back
    connect() opens a database connection. It is held only for the suppression check,
    never across the network call. Never logs the body or the address."""
    if not enabled():
        return None, None
    try:
        with connect() as cx:
            reason = block_reason(cx, to, marketing=marketing)
    except Exception as e:
        print(f"[ses] suppression check failed: {type(e).__name__}", flush=True)
        return None, None
    if reason:
        return "suppressed", None
    try:
        from botocore.exceptions import (ClientError, ConnectTimeoutError, EndpointConnectionError,
                                         NoCredentialsError, ParamValidationError,
                                         PartialCredentialsError)
        # Each of these is raised before AWS accepts anything (round 1). Never ValueError:
        # it can follow acceptance (round 2).
        sure_not_sent = (ClientError, ConnectTimeoutError, EndpointConnectionError,
                         NoCredentialsError, ParamValidationError, PartialCredentialsError)
    except Exception:
        sure_not_sent = (ImportError,)   # no SDK: nothing can have gone out
    from_addr = None
    if from_name:
        from email.utils import formataddr, parseaddr
        from_addr = formataddr((from_name, parseaddr(os.environ.get("SES_FROM")
                                                     or DEFAULT_FROM)[1]))
    r = None
    try:
        r = send(None, to, subject, text, html, marketing=marketing, scope=scope,
                 from_addr=from_addr, reply_to=reply_to, prechecked=True)
    except Exception as e:
        print(f"[ses] send failed: {type(e).__name__}", flush=True)
        if r is None:
            # A 5xx may follow acceptance, so only a 4xx refusal counts as sure (round 1).
            resp = getattr(e, "response", None)
            status = (resp.get("ResponseMetadata") or {}).get("HTTPStatusCode") \
                if isinstance(resp, dict) else None
            sure = isinstance(e, sure_not_sent) and not (status and status >= 500)
            return (None if sure else "unknown"), None
    if r.get("sent"):
        try:
            print(f"[ses] sent {r.get('message_id')}", flush=True)
        except Exception:
            pass
        return "ses", r.get("message_id")
    return None, None
