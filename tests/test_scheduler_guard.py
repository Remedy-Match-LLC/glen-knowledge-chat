"""The web scheduler must not start inside a script that imports app.

2026-09-13: a Render one-off job inherits DATA_DIR, so importing app started the
scheduler, and its console push ran at once in five jobs. Scripts now set
NO_BACKGROUND_SCHEDULER=1 before the import.
"""
import os
from pathlib import Path

import pytest

if not os.environ.get("PINECONE_API_KEY"):
    pytest.skip("needs doppler env for import app", allow_module_level=True)

import app as appmod


def test_web_process_on_render_starts_the_scheduler():
    assert appmod._scheduler_wanted({"DATA_DIR": "/data"}, {})


def test_switch_stops_the_scheduler():
    assert not appmod._scheduler_wanted(
        {"DATA_DIR": "/data", "NO_BACKGROUND_SCHEDULER": "1"}, {})


def test_no_data_dir_or_pytest_stops_the_scheduler():
    assert not appmod._scheduler_wanted({}, {})
    assert not appmod._scheduler_wanted({"DATA_DIR": "/data"}, {"pytest": object()})


def test_loading_the_weekly_sender_leaves_the_scheduler_off(monkeypatch):
    import importlib.util
    monkeypatch.delenv("NO_BACKGROUND_SCHEDULER", raising=False)
    spec = importlib.util.spec_from_file_location(
        "weekly_live_invitation_scheduler_check",
        Path(__file__).parents[1] / "scripts" / "weekly_live_invitation.py")
    spec.loader.exec_module(importlib.util.module_from_spec(spec))
    # As a Render one-off job sees it: DATA_DIR set and no pytest.
    assert not appmod._scheduler_wanted({**os.environ, "DATA_DIR": "/data"}, {})


def test_weekly_sender_sets_the_switch_before_importing_app():
    source = (Path(__file__).parents[1] / "scripts" / "weekly_live_invitation.py").read_text()
    switch = source.index('os.environ["NO_BACKGROUND_SCHEDULER"] = "1"')
    assert switch < source.index("\nimport app as appmod")
