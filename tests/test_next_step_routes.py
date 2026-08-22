import importlib
import sys
from pathlib import Path

import pytest


def _load_app():
    repo_root = Path(__file__).resolve().parent.parent
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
    try:
        return importlib.import_module("app")
    except Exception as e:
        pytest.skip(f"app module not importable in this env: {e}")


def _client(app_module, monkeypatch, tmp_path):
    db = str(tmp_path / "chat_log.db")
    monkeypatch.setattr(app_module, "LOG_DB", db)
    import sqlite3, begin_funnel
    with sqlite3.connect(db) as cx:
        begin_funnel.init_journey_tables(cx)
    app_module.app.config["TESTING"] = True
    return app_module.app.test_client()


def test_begin_state_includes_next_step_block(monkeypatch, tmp_path):
    app_module = _load_app()
    c = _client(app_module, monkeypatch, tmp_path)
    c.set_cookie("amg_session", "ns-route-test-1")
    r = c.get("/begin/state")
    assert r.status_code == 200
    body = r.get_json()
    assert "next_step" in body
    assert "prompt" in body["next_step"] and "chips" in body["next_step"]
    # Inverted 2026-08-22: a cold visitor gets the seed OUTCOME chips, which answer
    # the greeting, plus one 'explore instead' escape hatch. The route contract this
    # test really guards -- next_step carrying prompt + chips -- is asserted above.
    chips = body["next_step"]["chips"]
    primary = [c for c in chips if c["role"] == "primary"]
    assert primary and all(c["action"] == "text" for c in primary)
    secondary = [c for c in chips if c["role"] == "secondary"]
    assert len(secondary) == 1 and secondary[0].get("value") == "adventure"


def test_travel_style_route_sets_and_returns_block(monkeypatch, tmp_path):
    app_module = _load_app()
    c = _client(app_module, monkeypatch, tmp_path)
    c.set_cookie("amg_session", "ns-route-test-2")
    r = c.post("/begin/travel-style", json={"style": "adventure"})
    assert r.status_code == 200
    body = r.get_json()
    assert body["ok"] is True
    labels = [ch["label"] for ch in body["next_step"]["chips"] if ch["role"] == "primary"]
    assert "Explore my biofield" in labels


def test_travel_style_route_rejects_bad_value(monkeypatch, tmp_path):
    app_module = _load_app()
    c = _client(app_module, monkeypatch, tmp_path)
    c.set_cookie("amg_session", "ns-route-test-3")
    r = c.post("/begin/travel-style", json={"style": "wander"})
    assert r.status_code == 400


def test_travel_style_route_still_builds_signals(monkeypatch, tmp_path):
    """Inverted 2026-08-20. This used to force has_e4l and assert the Scan chip
    resolved to portal.e4l.com, because #863 fixed the route failing to thread
    `signals` into next_step_chips. The chip now hands off to /begin/scan in both
    states, and the bridge makes the portal-vs-signup call itself
    (tests/test_e4l_bridge.py::test_a_returning_scanner_goes_to_the_portal_not_the_signup).

    The #863 regression guard is kept, not dropped: signals still drive the Give
    card's done-detection, so the route must still BUILD them. Asserting _has_e4l
    was actually consulted keeps that honest without depending on an href that no
    longer varies."""
    app_module = _load_app()
    c = _client(app_module, monkeypatch, tmp_path)
    seen = []

    def _spy(cx, email, state):
        seen.append(email)
        return True

    monkeypatch.setattr(app_module, "_has_e4l", _spy)
    c.set_cookie("amg_session", "ns-route-e4l")
    r = c.post("/begin/travel-style", json={"style": "adventure"})
    assert r.status_code == 200
    assert seen, "route stopped building journey signals"
    scan = next(ch for ch in r.get_json()["next_step"]["chips"]
                if ch["label"] == "Explore my biofield")
    assert scan["href"].startswith("/begin/scan")
