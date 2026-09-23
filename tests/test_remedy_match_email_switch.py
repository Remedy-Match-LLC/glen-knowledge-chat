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


def _catalog(app_mod, monkeypatch, product=None):
    monkeypatch.setattr(app_mod, "_resolve_buy_slug", lambda n: "microbiome")
    monkeypatch.setattr(app_mod, "_get_product",
                        lambda s: product if product is not None else
                        {"slug": "microbiome", "name": "Microbiome"})


def test_switched_on_it_queues_and_never_sends_at_once(app_mod, monkeypatch):
    """Rebuilt the same day: switched on, the chat QUEUES the match. Nothing is mailed
    from inside the chat; the drain sends it after the chat goes quiet."""
    monkeypatch.setenv("REMEDY_MATCH_EMAIL_ENABLED", "1")
    _catalog(app_mod, monkeypatch)
    assert app_mod._email_remedy_match_once("c@example.com", "C", "s1", MATCH) is True
    assert app_mod._sent == []
    from dashboard import db
    from dashboard import remedy_match_email as rme
    with db.connect(app_mod.LOG_DB) as cx:
        row = rme.recent(cx)[0]
    assert row["page_url"].endswith("/begin/product/microbiome")
    assert row["status"] == "pending"


def test_a_service_or_off_catalog_match_is_never_queued(app_mod, monkeypatch):
    monkeypatch.setenv("REMEDY_MATCH_EMAIL_ENABLED", "1")
    _catalog(app_mod, monkeypatch, product={"slug": "biofield-analysis", "name": "B",
                                            "service": True})
    assert app_mod._email_remedy_match_once("c@example.com", "C", "s1", MATCH) is False
    monkeypatch.setattr(app_mod, "_resolve_buy_slug", lambda n: None)
    assert app_mod._email_remedy_match_once("c@example.com", "C", "s2", MATCH) is False


def test_the_drain_does_nothing_while_switched_off(app_mod, monkeypatch):
    monkeypatch.delenv("REMEDY_MATCH_EMAIL_ENABLED", raising=False)
    import dashboard.remedy_match_email as rme
    called = []
    monkeypatch.setattr(rme, "drain", lambda *a, **k: called.append(1) or {})
    app_mod._drain_remedy_match_emails()
    assert called == []
