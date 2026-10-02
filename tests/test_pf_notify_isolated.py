"""A practitioner-finder failure email must never import app into the scraper.

2026-10-02: iabdm returned 520, the notification imported app, gevent patched ssl
late, and the next 11 adapters failed with RecursionError."""
import json
import subprocess
import sys

from scrapers.practitioner_finder import run_all


def test_notify_runs_app_in_a_child_process(monkeypatch):
    seen = {}

    def fake_run(cmd, **kw):
        seen["cmd"], seen["input"] = cmd, kw.get("input")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    # Hide app for this test only. Popping it for good made later tests import a
    # second copy, and 110 of them lost their stand-ins in CI (2026-10-02).
    monkeypatch.delitem(sys.modules, "app", raising=False)
    run_all._notify_glen("iabdm failed", "520")
    assert "app" not in sys.modules
    assert seen["cmd"][0] == sys.executable and "from app import" in seen["cmd"][2]
    assert json.loads(seen["input"]) == {"to": "drglenswartwout@gmail.com",
                                         "subject": "iabdm failed", "body": "520"}


def test_a_failed_send_is_reported_not_raised(monkeypatch, capsys):
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: subprocess.CompletedProcess(
        cmd, 1, "", "Traceback...\nOperationalError: no connection"))
    run_all._notify_glen("x", "y")
    assert "OperationalError: no connection" in capsys.readouterr().err


def test_the_child_script_compiles():
    compile(run_all._NOTIFY_CHILD, "<notify>", "exec")
