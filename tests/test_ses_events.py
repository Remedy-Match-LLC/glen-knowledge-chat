"""SES bounce and complaint events, posted by SNS, into the suppression list.

The signature tests sign with a throwaway key and certificate, so every check runs
the real verifier. Only the certificate download is replaced.
"""
import base64
import contextlib
import datetime
import json
import sqlite3

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.x509.oid import NameOID

from dashboard import email_suppression as es
from dashboard import ses_events as se

TOPIC = "arn:aws:sns:us-west-2:448674444064:ses-events"
CERT_URL = "https://sns.us-west-2.amazonaws.com/SimpleNotificationService-abc.pem"


@pytest.fixture(scope="module")
def keypair():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "sns.amazonaws.com")])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
            .public_key(key.public_key()).serial_number(1)
            .not_valid_before(now).not_valid_after(now + datetime.timedelta(days=1))
            .sign(key, hashes.SHA256()))
    return key, cert.public_bytes(serialization.Encoding.PEM)


@pytest.fixture(autouse=True)
def topic(monkeypatch):
    monkeypatch.setenv(se.TOPIC_ENV, TOPIC)


def _signed(key, msg, version="2"):
    msg = dict(msg, SignatureVersion=version, SigningCertURL=msg.get("SigningCertURL", CERT_URL))
    algo = hashes.SHA256() if version == "2" else hashes.SHA1()
    sig = key.sign(se.string_to_sign(msg), padding.PKCS1v15(), algo)
    msg["Signature"] = base64.b64encode(sig).decode()
    return msg


def _note(event, **over):
    base = {"Type": "Notification", "MessageId": "m-1", "TopicArn": TOPIC,
            "Timestamp": "2026-09-18T20:00:00.000Z", "Message": json.dumps(event)}
    base.update(over)
    return base


def _db():
    cx = sqlite3.connect(":memory:")
    es.init_table(cx)
    cx.execute("CREATE TABLE people (id INTEGER PRIMARY KEY, email TEXT, tags TEXT)")
    cx.commit()
    return cx


def _factory(cx):
    @contextlib.contextmanager
    def f():
        yield cx
    return f


def _post(cx, pem, msg):
    return se.handle(json.dumps(msg), _factory(cx), fetch_cert=lambda url: pem)


BOUNCE = {"eventType": "Bounce", "bounce": {
    "bounceType": "Permanent", "bounceSubType": "General",
    "bouncedRecipients": [{"emailAddress": "Dead@Example.com"}]}}
COMPLAINT = {"eventType": "Complaint", "complaint": {
    "complaintFeedbackType": "abuse",
    "complainedRecipients": [{"emailAddress": "angry@example.com"}]}}


def test_permanent_bounce_blocks_the_address(keypair):
    key, pem = keypair
    cx = _db()
    status, out = _post(cx, pem, _signed(key, _note(BOUNCE)))
    assert status == 200 and out["blocked"] == 1
    assert es.suppression_reason(cx, "dead@example.com") == "hard"


def test_sha1_signature_version_is_accepted(keypair):
    key, pem = keypair
    cx = _db()
    status, _ = _post(cx, pem, _signed(key, _note(BOUNCE), version="1"))
    assert status == 200
    assert es.is_suppressed(cx, "dead@example.com")


def test_identity_notification_shape_is_read_too(keypair):
    key, pem = keypair
    cx = _db()
    event = dict(BOUNCE)
    event["notificationType"] = event.pop("eventType")
    _post(cx, pem, _signed(key, _note(event)))
    assert es.is_suppressed(cx, "dead@example.com")


def test_transient_bounce_blocks_nothing(keypair):
    key, pem = keypair
    cx = _db()
    event = json.loads(json.dumps(BOUNCE))
    event["bounce"]["bounceType"] = "Transient"
    status, out = _post(cx, pem, _signed(key, _note(event)))
    assert status == 200 and out["blocked"] == 0
    assert not es.is_suppressed(cx, "dead@example.com")


def test_complaint_blocks_the_address_and_marks_the_person(keypair):
    key, pem = keypair
    cx = _db()
    cx.execute("INSERT INTO people (id, email, tags) VALUES (7, 'angry@example.com', ?)",
               (json.dumps(["type:client"]),))
    status, out = _post(cx, pem, _signed(key, _note(COMPLAINT)))
    assert status == 200 and out["people_marked"] == 1
    assert es.suppression_reason(cx, "angry@example.com") == "complaint"
    tags = json.loads(cx.execute("SELECT tags FROM people WHERE id=7").fetchone()[0])
    assert es.UNSUBSCRIBED_TAG in tags and "type:client" in tags


def test_a_bounce_upgrades_an_existing_opt_out(keypair):
    """Inverted 2026-09-18 on platform's review: a dead address says more than an
    opt-out, and transactional mail must stop at it."""
    key, pem = keypair
    cx = _db()
    es.add_optout(cx, "dead@example.com", "unsubscribe-link:global")
    _, out = _post(cx, pem, _signed(key, _note(BOUNCE)))
    assert out["blocked"] == 1
    assert es.suppression_reason(cx, "dead@example.com") == "hard"


def test_a_complaint_upgrades_an_opt_out_so_transactional_stops(keypair):
    from dashboard import ses_mail as sm
    key, pem = keypair
    cx = _db()
    es.add_optout(cx, "angry@example.com", "unsubscribe-link:global")
    assert sm.block_reason(cx, "angry@example.com", marketing=False) is None
    _post(cx, pem, _signed(key, _note(COMPLAINT)))
    assert es.suppression_reason(cx, "angry@example.com") == "complaint"
    assert sm.block_reason(cx, "angry@example.com", marketing=False) == "complaint"


def test_a_bounce_never_downgrades_a_complaint(keypair):
    key, pem = keypair
    cx = _db()
    es.add(cx, "dead@example.com", "complaint", "r", "ses-complaint")
    _, out = _post(cx, pem, _signed(key, _note(BOUNCE)))
    assert out["blocked"] == 0
    assert es.suppression_reason(cx, "dead@example.com") == "complaint"


def test_a_replayed_event_counts_nothing(keypair):
    key, pem = keypair
    cx = _db()
    msg = _signed(key, _note(BOUNCE))
    assert _post(cx, pem, msg)[1]["blocked"] == 1
    assert _post(cx, pem, msg)[1]["blocked"] == 0


def test_a_mixed_case_stored_address_still_gets_the_tag(keypair):
    key, pem = keypair
    cx = _db()
    cx.execute("INSERT INTO people (id, email, tags) VALUES (8, 'Angry@Example.COM', '[]')")
    _, out = _post(cx, pem, _signed(key, _note(COMPLAINT)))
    assert out["people_marked"] == 1
    tags = json.loads(cx.execute("SELECT tags FROM people WHERE id=8").fetchone()[0])
    assert es.UNSUBSCRIBED_TAG in tags


def test_a_tampered_message_is_refused(keypair):
    key, pem = keypair
    cx = _db()
    msg = _signed(key, _note(BOUNCE))
    msg["Message"] = msg["Message"].replace("Dead@", "Someone@")
    status, _ = _post(cx, pem, msg)
    assert status == 403
    assert not es.is_suppressed(cx, "someone@example.com")


def test_another_key_is_refused(keypair):
    _, pem = keypair
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    cx = _db()
    status, _ = _post(cx, pem, _signed(other, _note(BOUNCE)))
    assert status == 403
    assert not es.is_suppressed(cx, "dead@example.com")


def test_another_topic_is_refused(keypair):
    key, pem = keypair
    cx = _db()
    msg = _signed(key, _note(BOUNCE, TopicArn="arn:aws:sns:us-west-2:1:someone-else"))
    status, out = _post(cx, pem, msg)
    assert status == 403 and out["error"] == "wrong topic"


def test_a_certificate_off_amazon_is_refused(keypair):
    key, pem = keypair
    cx = _db()
    for url in ("https://evil.example.com/cert.pem",
                "http://sns.us-west-2.amazonaws.com/cert.pem",
                "https://sns.us-west-2.amazonaws.com.evil.com/cert.pem"):
        msg = _signed(key, _note(BOUNCE, SigningCertURL=url))
        status, out = _post(cx, pem, msg)
        assert status == 403, url
    assert not es.is_suppressed(cx, "dead@example.com")


def test_with_no_topic_configured_everything_is_refused(keypair, monkeypatch):
    key, pem = keypair
    monkeypatch.delenv(se.TOPIC_ENV)
    cx = _db()
    status, out = _post(cx, pem, _signed(key, _note(BOUNCE)))
    assert status == 403 and out["error"] == "no topic configured"


def test_subscription_is_confirmed_only_at_an_amazon_url(keypair):
    key, pem = keypair
    cx = _db()
    hits = []
    base = {"Type": "SubscriptionConfirmation", "MessageId": "s-1", "TopicArn": TOPIC,
            "Timestamp": "2026-09-18T20:00:00.000Z", "Message": "confirm", "Token": "t"}
    good = _signed(key, dict(base, SubscribeURL="https://sns.us-west-2.amazonaws.com/?Action=Confirm"))
    status, _ = se.handle(json.dumps(good), _factory(cx), fetch_cert=lambda u: pem,
                          confirm=hits.append)
    assert status == 200 and len(hits) == 1
    bad = _signed(key, dict(base, SubscribeURL="https://evil.example.com/confirm"))
    status, _ = se.handle(json.dumps(bad), _factory(cx), fetch_cert=lambda u: pem,
                          confirm=hits.append)
    assert status == 403 and len(hits) == 1


def test_not_json_is_a_400():
    status, _ = se.handle("not json", _factory(_db()))
    assert status == 400
