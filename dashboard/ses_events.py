"""Amazon SES bounce and complaint events, delivered by SNS, into the suppression list.

SES publishes to an SNS topic, and SNS posts here. Every post is verified against
Amazon's signing certificate and the one topic named in SES_SNS_TOPIC_ARN before
anything is read. With no topic configured, every post is refused, so the route is
inert until the AWS side exists.

What each event does:
  Permanent bounce   blocks the address (bounce_type 'hard').
  Transient bounce   nothing. A full mailbox is not a dead address.
  Complaint          blocks the address (bounce_type 'complaint') and marks the hub
                     person `consent:unsubscribed`, because a spam report means the
                     person asked us to stop.
A row is replaced only by an event that says more: complaint over hard, and either
over an opt-out or a GoHighLevel flag. A replayed event writes nothing.
"""
from __future__ import annotations

import base64
import json
import os
import re
from urllib.parse import urlparse

import requests

from dashboard import email_suppression as es

TOPIC_ENV = "SES_SNS_TOPIC_ARN"
_SNS_HOST = re.compile(r"^sns\.[a-z0-9-]+\.amazonaws\.com$")
_CERT_CACHE: dict[str, bytes] = {}

_SIGNED_KEYS = {
    "Notification": ("Message", "MessageId", "Subject", "Timestamp", "TopicArn", "Type"),
    "SubscriptionConfirmation": ("Message", "MessageId", "SubscribeURL", "Timestamp",
                                 "Token", "TopicArn", "Type"),
    "UnsubscribeConfirmation": ("Message", "MessageId", "SubscribeURL", "Timestamp",
                                "Token", "TopicArn", "Type"),
}


class Rejected(Exception):
    """The post is not a verified message from our topic."""


def expected_topic() -> str:
    return os.environ.get(TOPIC_ENV, "").strip()


def _amazon_url(url: str) -> bool:
    p = urlparse(url or "")
    return p.scheme == "https" and bool(_SNS_HOST.match(p.hostname or ""))


def _fetch_cert(url: str) -> bytes:
    if url not in _CERT_CACHE:
        r = requests.get(url, timeout=10)
        r.raise_for_status()
        _CERT_CACHE[url] = r.content
    return _CERT_CACHE[url]


def string_to_sign(msg: dict) -> bytes:
    keys = _SIGNED_KEYS.get(msg.get("Type"))
    if not keys:
        raise Rejected("unknown message type")
    out = []
    for k in keys:
        if k == "Subject" and "Subject" not in msg:
            continue
        if k not in msg:
            raise Rejected(f"missing {k}")
        out.append(f"{k}\n{msg[k]}\n")
    return "".join(out).encode()


def verify(msg: dict, *, fetch_cert=_fetch_cert) -> None:
    """Raise Rejected unless the message is signed by Amazon and from our topic."""
    from cryptography import x509
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding

    topic = expected_topic()
    if not topic:
        raise Rejected("no topic configured")
    if msg.get("TopicArn") != topic:
        raise Rejected("wrong topic")
    cert_url = msg.get("SigningCertURL", "")
    if not _amazon_url(cert_url) or not urlparse(cert_url).path.endswith(".pem"):
        raise Rejected("certificate not from Amazon")
    version = str(msg.get("SignatureVersion", ""))
    algo = {"1": hashes.SHA1(), "2": hashes.SHA256()}.get(version)
    if algo is None:
        raise Rejected("unknown signature version")
    try:
        sig = base64.b64decode(msg.get("Signature", ""), validate=True)
    except (ValueError, TypeError):
        raise Rejected("signature not base64")
    cert = x509.load_pem_x509_certificate(fetch_cert(cert_url))
    try:
        cert.public_key().verify(sig, string_to_sign(msg), padding.PKCS1v15(), algo)
    except InvalidSignature:
        raise Rejected("bad signature")


def _mark_person_unsubscribed(cx, email: str) -> bool:
    # lower(email) matches how app.py looks people up. No try/except: on Postgres a
    # failed statement aborts the transaction and would silently roll back the
    # suppression rows written before it.
    row = cx.execute("SELECT id, tags FROM people WHERE lower(email)=?", (email,)).fetchone()
    if not row:
        return False
    tags = es._tags_of(row[1])
    if es.has_unsubscribed_tag(tags):
        return False
    tags = sorted(set(tags) | {es.UNSUBSCRIBED_TAG})
    cx.execute("UPDATE people SET tags=? WHERE id=?", (json.dumps(tags), row[0]))
    return True


# How much a row tells us. An event replaces a row only when it says more. A
# complaint outranks a dead address, and both outrank an opt-out or a GoHighLevel
# flag, because transactional mail still reaches an opt-out and must not reach
# someone who marked us as spam.
_RANK = {"complaint": 2, "hard": 1}


def _block(cx, email: str, bounce_type: str, why: str, source: str) -> bool:
    """Write or upgrade the row. True only when something was written."""
    current = es._table_reason(cx, email)
    if current is not None and _RANK.get(current, 0) >= _RANK[bounce_type]:
        return False
    es.add(cx, email, bounce_type, why, source, overwrite=True, commit=False)
    return True


def apply_event(cx, event: dict) -> dict:
    """Record one SES event. Returns counts, for the log and the tests."""
    kind = event.get("eventType") or event.get("notificationType") or ""
    done = {"kind": kind, "blocked": 0, "people_marked": 0}
    if kind == "Bounce":
        b = event.get("bounce") or {}
        if b.get("bounceType") != "Permanent":
            return done
        why = f"ses bounce {b.get('bounceSubType') or ''}".strip()
        for r in b.get("bouncedRecipients") or []:
            email = es.normalize(r.get("emailAddress"))
            if email:
                done["blocked"] += int(_block(cx, email, "hard", why, "ses-bounce"))
    elif kind == "Complaint":
        c = event.get("complaint") or {}
        why = f"ses complaint {c.get('complaintFeedbackType') or ''}".strip()
        for r in c.get("complainedRecipients") or []:
            email = es.normalize(r.get("emailAddress"))
            if email:
                done["blocked"] += int(_block(cx, email, "complaint", why, "ses-complaint"))
                done["people_marked"] += int(_mark_person_unsubscribed(cx, email))
    cx.commit()
    return done


def handle(body: str, cx_factory, *, fetch_cert=_fetch_cert, confirm=None) -> tuple[int, dict]:
    """One SNS post. Returns (http status, json body)."""
    try:
        msg = json.loads(body or "")
        if not isinstance(msg, dict):
            raise ValueError
    except ValueError:
        return 400, {"error": "not json"}
    try:
        verify(msg, fetch_cert=fetch_cert)
    except Rejected as e:
        return 403, {"error": str(e)}
    kind = msg.get("Type")
    if kind == "SubscriptionConfirmation":
        url = msg.get("SubscribeURL", "")
        if not _amazon_url(url):
            return 403, {"error": "subscribe url not from Amazon"}
        (confirm or (lambda u: requests.get(u, timeout=10).raise_for_status()))(url)
        return 200, {"ok": True, "confirmed": True}
    if kind != "Notification":
        return 200, {"ok": True, "ignored": kind}
    try:
        event = json.loads(msg.get("Message") or "")
    except ValueError:
        return 200, {"ok": True, "ignored": "message not json"}
    with cx_factory() as cx:
        es.init_table(cx)
        done = apply_event(cx, event)
    return 200, {"ok": True, **done}
