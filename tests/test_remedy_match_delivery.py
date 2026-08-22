import os
import pytest

if not os.environ.get("PINECONE_API_KEY"):
    pytest.skip("needs doppler env for import app", allow_module_level=True)
import app as appmod


def test_confirmed_trauma_alias_resolves_to_real_catalog_page():
    assert appmod._canonical_match_name("Trauma & Shock Release formulation") == (
        "Trauma Relief in Terrain Restore")
    assert appmod._resolve_buy_slug("Trauma & Shock Release formulation") == (
        "trauma-relief-in-terrain-restore")


def test_match_email_is_idempotent(monkeypatch, tmp_path):
    monkeypatch.setattr(appmod, "LOG_DB", str(tmp_path / "chat_log.db"))
    sent = []
    monkeypatch.setattr(appmod, "send_evox_email",
                        lambda *args, **kwargs: sent.append(args))
    match = {"name": "Trauma & Shock Release formulation",
             "why": "It matches the pattern you described.",
             "product_url": "/begin/product/trauma-relief-in-terrain-restore"}
    assert appmod._email_remedy_match_once(
        "maria@example.com", "Maria", "session-1", match)
    assert not appmod._email_remedy_match_once(
        "maria@example.com", "Maria", "session-1", match)
    assert len(sent) == 1
    assert "Trauma Relief in Terrain Restore" in sent[0][2]
