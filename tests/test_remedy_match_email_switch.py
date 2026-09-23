"""The chat's "Your remedy match" email is OFF unless REMEDY_MATCH_EMAIL_ENABLED is set.

Glen, 2026-09-22: "switch them off". It emailed one client 12 times in 31 hours with
the extractor's unreviewed text in Glen's name. The in-chat match card is unaffected.
"""
import pytest

MATCH = {"name": "Microbiome", "why": "unreviewed AI sentence", "product_url": "/p/x"}


@pytest.fixture
def app_mod(monkeypatch, tmp_path):
    import app
    monkeypatch.setattr(app, "LOG_DB", str(tmp_path / "chat_log.db"))
    monkeypatch.setattr(app, "_canonical_match_name", lambda n: n)
    sent = []
    monkeypatch.setattr(app, "send_evox_email", lambda *a, **k: sent.append(a))
    app._sent = sent
    return app


def test_off_by_default_sends_nothing(app_mod, monkeypatch):
    monkeypatch.delenv("REMEDY_MATCH_EMAIL_ENABLED", raising=False)
    assert app_mod._email_remedy_match_once("c@example.com", "C", "s1", MATCH) is False
    assert app_mod._sent == []


@pytest.mark.parametrize("value", ["", "0", "false", "no", "off"])
def test_any_non_yes_value_stays_off(app_mod, monkeypatch, value):
    monkeypatch.setenv("REMEDY_MATCH_EMAIL_ENABLED", value)
    app_mod._email_remedy_match_once("c@example.com", "C", "s1", MATCH)
    assert app_mod._sent == []


def test_switched_on_it_still_sends(app_mod, monkeypatch):
    """The switch, not a deletion: turning it on restores today's behaviour."""
    monkeypatch.setenv("REMEDY_MATCH_EMAIL_ENABLED", "1")
    assert app_mod._email_remedy_match_once("c@example.com", "C", "s1", MATCH) is True
    assert len(app_mod._sent) == 1
