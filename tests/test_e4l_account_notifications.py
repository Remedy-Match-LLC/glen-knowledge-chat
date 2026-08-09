import base64
import sqlite3
from unittest.mock import MagicMock

from dashboard import e4l_account_notifications as accounts


BODY = """Dear Dr. Glen

Congratulations, a new Energy4Life client has signed up with you!

Jane Example
jane@example.com
8085551212

Please check all your new client's information is correct by clicking here
"""


def _message(msg_id="m1"):
    encoded = base64.urlsafe_b64encode(BODY.encode()).decode().rstrip("=")
    return {
        "id": msg_id,
        "internalDate": "1786200000000",
        "payload": {
            "headers": [
                {"name": "From", "value": "Energy4Life <noreply@e4l.com>"},
                {"name": "Subject", "value": accounts.SUBJECT},
            ],
            "mimeType": "text/plain",
            "body": {"data": encoded},
        },
    }


def _service(message):
    service = MagicMock()
    service.users().messages().list().execute.return_value = {
        "messages": [{"id": message["id"]}]
    }
    service.users().messages().get().execute.return_value = message
    return service


def test_parse_notification_extracts_client_fields():
    assert accounts.parse_notification(BODY) == {
        "email": "jane@example.com",
        "client_name": "Jane Example",
        "phone": "8085551212",
    }


def test_ingest_is_idempotent_and_marks_account_known():
    cx = sqlite3.connect(":memory:")
    service = _service(_message())
    first = accounts.ingest_notifications(cx, service=service)
    second = accounts.ingest_notifications(cx, service=service)
    assert first == {"listed": 1, "inserted": 1, "updated": 0,
                     "skipped": 0, "parse_failed": 0}
    assert second == {"listed": 1, "inserted": 0, "updated": 0,
                      "skipped": 1, "parse_failed": 0}
    assert accounts.has_account(cx, "JANE@EXAMPLE.COM") is True
    assert cx.execute(
        "SELECT client_name,phone,gmail_msg_id FROM e4l_accounts"
    ).fetchone() == ("Jane Example", "8085551212", "m1")
    assert cx.execute(
        "SELECT gmail_msg_id,email FROM e4l_account_notification_messages"
    ).fetchone() == ("m1", "jane@example.com")


def test_repeated_signup_for_same_email_does_not_oscillate():
    cx = sqlite3.connect(":memory:")
    accounts.ingest_notifications(cx, service=_service(_message("m1")))
    accounts.ingest_notifications(cx, service=_service(_message("m2")))
    service = MagicMock()
    service.users().messages().list().execute.return_value = {
        "messages": [{"id": "m1"}, {"id": "m2"}]
    }
    third = accounts.ingest_notifications(cx, service=service)
    assert third["skipped"] == 2
    assert service.users().messages().get.call_count == 0
