import importlib.util
import os
from datetime import date
from pathlib import Path


os.environ.setdefault("OPENAI_API_KEY", "test-key")
os.environ.setdefault("PINECONE_API_KEY", "test-key")

SPEC = importlib.util.spec_from_file_location(
    "weekly_live_invitation",
    Path(__file__).parents[1] / "scripts" / "weekly_live_invitation.py")
weekly = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(weekly)


def test_copy_routes_only_through_private_portal():
    text, body = weekly._copy(
        "Glen", "https://myhealingoasis.com/portal/private-token", True,
        date(2026, 8, 26))
    assert "Group Coaching is included" in text
    assert "https://myhealingoasis.com/portal/private-token" in text
    assert "zoom.us/" not in (text + body).lower()
    assert "Practice Better" not in text
    assert "Skool" not in text


def test_free_copy_states_group_coaching_is_upgrade_benefit():
    text, _ = weekly._copy(
        "Friend", "https://myhealingoasis.com/portal/private-token", False,
        date(2026, 8, 26))
    assert "MasterClass is open to you" in text
    assert "upgrade benefit" in text
    assert "current access does not include" in text


def test_send_uses_write_token(monkeypatch):
    seen = {}

    def fake_api(method, path, version, body=None, *, write=False):
        seen.update(method=method, path=path, version=version, body=body, write=write)
        return 201, {"messageId": "msg-1"}

    monkeypatch.setattr(weekly, "_api", fake_api)
    status, message_id, _ = weekly._send(
        "contact-1", "Subject", "plain", "<p>html</p>",
        email_to="member@example.com", scheduled_timestamp=1_800_000_000)

    assert status == 201
    assert message_id == "msg-1"
    assert seen["write"] is True
    assert seen["body"]["emailFrom"] == weekly.FROM_ADDRESS
    assert seen["body"]["emailTo"] == "member@example.com"
    assert seen["body"]["scheduledTimestamp"] == 1_800_000_000


def test_create_contact_reuses_contact_when_email_is_an_additional_address(monkeypatch):
    monkeypatch.setenv("GHL_LOCATION_ID", "location-1")
    monkeypatch.setattr(
        weekly, "_api",
        lambda *args, **kwargs: (
            400,
            {"message": "This location does not allow duplicated contacts.",
             "meta": {"contactId": "existing-1", "matchingField": "additionalEmail"}},
        ),
    )

    contact = weekly._create_contact("member@example.com")

    assert contact == {"id": "existing-1", "email": "member@example.com"}


def test_send_preserves_string_error_response_without_crashing(monkeypatch):
    monkeypatch.setattr(
        weekly, "_api",
        lambda *args, **kwargs: (422, {"message": "email address is invalid"}),
    )

    status, message_id, response = weekly._send(
        "contact-1", "Subject", "plain", "<p>html</p>",
        email_to="bad@example.com")

    assert status == 422
    assert message_id == ""
    assert response["message"] == "email address is invalid"
