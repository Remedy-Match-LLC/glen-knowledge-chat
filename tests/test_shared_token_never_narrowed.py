"""A script that asks for fewer scopes must never save the shared Google token file.

~/.config/google/token.json is shared by every local mail job. Loading it with a narrow
SCOPES list, refreshing, and writing it back rewrites the file's scope list, so every
other job then refreshes narrow. console-push.py did this with gmail.readonly and Rae's
briefing failed to send for four days (25-28 Sep 2026). console_push_cron.py then did
it with 7 of the 9 granted scopes on 29 Sep, dropping gmail.modify.
"""
import json

import pytest
from google.oauth2 import credentials as gcreds

import cns_tracking_watcher
import console_push_cron


class _FakeCreds:
    expired = True
    refresh_token = "r"

    def __init__(self, scopes):
        self.scopes = scopes

    def refresh(self, request):
        self.expired = False

    def to_json(self):
        return json.dumps({"scopes": self.scopes})


@pytest.fixture
def token_file(tmp_path, monkeypatch):
    path = tmp_path / "token.json"
    original = json.dumps({"scopes": ["a", "b", "c", "d", "e", "f", "g", "h", "i"]})
    path.write_text(original)
    monkeypatch.setattr(gcreds.Credentials, "from_authorized_user_file",
                        classmethod(lambda cls, f, scopes=None: _FakeCreds(scopes)))
    monkeypatch.setattr("googleapiclient.discovery.build", lambda *a, **k: object())
    return path, original


def test_console_push_cron_does_not_rewrite_the_shared_token(token_file):
    path, original = token_file
    assert console_push_cron._gmail_service(path, console_push_cron.SCOPES_GLEN) is not None
    assert path.read_text() == original


def test_cns_watcher_does_not_rewrite_the_shared_token(token_file):
    path, original = token_file
    cns_tracking_watcher.gmail_service(token_path=path)
    assert path.read_text() == original
