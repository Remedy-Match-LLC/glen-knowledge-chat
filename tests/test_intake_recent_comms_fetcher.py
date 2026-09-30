"""The real recent-comms fetcher returns the endpoint's payload.

cfdaf40d (2026-08-27) inserted the photo fetcher inside _default_fetch_recent_comms and cut
its body, so it returned None for every real email. "Mine recent comms -> Stresses" and the
clinical proposals then found nothing for every client (Glen hit it 2026-09-30). The route
tests stub the fetcher, so none of them could see it. This test calls the real one.
"""
import io
import json

import pytest

import biofield_local_app as bla

PAYLOAD = {"emails": [{"subject": "my back", "body": "pain since the move"}], "window_days": 7}


class _Resp(io.BytesIO):
    pass


@pytest.fixture
def captured(monkeypatch):
    seen = {}

    def fake_urlopen(req, timeout=None):
        seen["url"] = req.full_url
        seen["key_header"] = req.headers.get("X-console-key")
        return _Resp(json.dumps(PAYLOAD).encode())

    monkeypatch.setenv("CONSOLE_SECRET", "test-secret")
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://example.test")
    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    return seen


def test_a_real_email_returns_the_endpoints_payload(captured):
    assert bla._default_fetch_recent_comms("client@example.com") == PAYLOAD
    assert captured["url"].startswith("https://example.test/api/people/recent-comms?")
    assert "q=client%40example.com" in captured["url"]
    assert captured["key_header"] == "test-secret"


def test_a_blank_email_makes_no_call(captured):
    assert bla._default_fetch_recent_comms("  ") == {}
    assert "url" not in captured


def test_a_failure_returns_empty_not_none(monkeypatch):
    monkeypatch.delenv("CONSOLE_SECRET", raising=False)
    assert bla._default_fetch_recent_comms("client@example.com") == {}
