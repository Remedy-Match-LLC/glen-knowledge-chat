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
    assert seen["cmd"][0] == sys.executable and "send_email" in seen["cmd"][2]
    assert json.loads(seen["input"]) == {"to": "drglenswartwout@gmail.com",
                                         "subject": "iabdm failed", "body": "520"}


def test_a_failed_send_is_reported_in_full_not_raised(monkeypatch, capsys):
    """2026-10-02 the log kept only stderr's last line, gevent's exit noise, and hid
    the real error. The cause must survive even when noise follows it."""
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: subprocess.CompletedProcess(
        cmd, 1, "", "Traceback...\nOperationalError: no connection\n"
                    "Exception ignored while calling deallocator\n"
                    "RuntimeError: greenlet is being finalized"))
    assert run_all._notify_glen("x", "y") is False
    err = capsys.readouterr().err
    assert "OperationalError: no connection" in err and "exit 1" in err


def test_a_send_is_reported_as_sent(monkeypatch, capsys):
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: subprocess.CompletedProcess(
        cmd, 0, '{"id": "abc"}', ""))
    assert run_all._notify_glen("x", "y") is True
    assert "sent" in capsys.readouterr().err


def test_the_child_never_loads_app_or_gevent_and_fails_without_an_id():
    """Run the real child. Under pytest send_email returns {"skipped": "pytest"}
    without sending, so the child must exit non-zero: a skip is not a delivery."""
    import os
    import pathlib
    repo = pathlib.Path(run_all.__file__).resolve().parents[2]
    probe = ("import atexit, sys\n"
             "atexit.register(lambda: print('LOADED' if {'app', 'gevent'} & set(sys.modules)"
             " else 'CLEAN', file=sys.stderr))\n" + run_all._NOTIFY_CHILD)
    env = {k: v for k, v in os.environ.items() if k != "DOPPLER_TOKEN"}
    env["PYTEST_CURRENT_TEST"] = "child-probe"
    r = subprocess.run([sys.executable, "-c", probe], cwd=repo, env=env,
                       input=json.dumps({"to": "nobody@example.com", "subject": "s", "body": "b"}),
                       capture_output=True, text=True, timeout=120)
    assert '"skipped": "pytest"' in r.stdout
    assert r.returncode == 3
    assert r.stderr.strip().splitlines()[-1] == "CLEAN"


def test_the_child_script_compiles():
    compile(run_all._NOTIFY_CHILD, "<notify>", "exec")
