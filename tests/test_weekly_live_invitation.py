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
