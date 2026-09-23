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
    """One email per chat. Rebuilt 2026-09-22: the chat QUEUES the match, repeat calls in
    the same chat only update it, and the drain sends it once, with the catalog name."""
    from datetime import datetime, timedelta, timezone
    from dashboard import db
    from dashboard import remedy_match_email as rme
    monkeypatch.setenv("REMEDY_MATCH_EMAIL_ENABLED", "1")
    monkeypatch.setattr(appmod, "LOG_DB", str(tmp_path / "chat_log.db"))
    sent = []
    monkeypatch.setattr(appmod, "send_evox_email",
                        lambda *args, **kwargs: sent.append(args))
    match = {"name": "Trauma & Shock Release formulation",
             "why": "It matches the pattern you described.",
             "product_url": "/begin/product/trauma-relief-in-terrain-restore"}
    assert appmod._email_remedy_match_once(
        "maria@example.com", "Maria", "session-1", match)
    appmod._email_remedy_match_once("maria@example.com", "Maria", "session-1", match)
    assert sent == []                              # nothing mailed from inside the chat
    later = datetime.now(timezone.utc) + timedelta(hours=1)
    with db.connect(appmod.LOG_DB) as cx:
        rme.drain(cx, lambda e, n, s, h, t: sent.append((e, n, s)), now=later)
        rme.drain(cx, lambda e, n, s, h, t: sent.append((e, n, s)), now=later)
    assert len(sent) == 1
    assert sent[0][2] == "The remedy you found in our chat: Trauma Relief in Terrain Restore"
