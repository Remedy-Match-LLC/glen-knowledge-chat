"""The 15-minute cron's piggyback legs.

Render caps cron services, so the Click-N-Ship watcher and the USPS status sync
both ride on the reply-watcher's cron rather than claiming their own. Nothing
tested that runner before, which means the legs were the one part of the feature
that could silently never run — the exact shape that hid a 60-day outage.

Two properties matter and neither was pinned:
  1. Both legs are actually invoked, before the reply-watch call.
  2. A leg failure is printed and never changes the reply-watcher's exit code.
     The jobs are unrelated; a tracking blip must not read as inbox failure, and
     an inbox failure must not be masked by a healthy leg.
"""

import importlib
import json
import sys
import urllib.error
from pathlib import Path

import pytest


@pytest.fixture
def runner(monkeypatch):
    repo = Path(__file__).resolve().parent.parent
    scripts = repo / "scripts"
    for p in (str(repo), str(scripts)):
        if p not in sys.path:
            sys.path.insert(0, p)
    monkeypatch.setenv("CRON_SECRET", "s3cret")
    monkeypatch.setenv("WEB_URL", "https://example.invalid")
    mod = importlib.import_module("run_reply_watcher_cron")
    return importlib.reload(mod)


def _ok(url):
    """A plausible success body for whichever endpoint was called."""
    if "cns-tracking" in url:
        return json.dumps({"ok": True, "mailbox": "x@y.z", "emails": 1,
                           "shipments": 2, "actions": {}})
    if "usps-status" in url:
        return json.dumps({"ok": True, "mailbox": "x@y.z", "emails": 4,
                           "parcels": 2, "acted": 1, "cards_reported": 1,
                           "pre_transit_held": 1,
                           "unknown_parcels": 0, "errors": 0})
    return json.dumps({"ok": True, "processed": 3, "errored": 0,
                       "skipped_nonuser": 0})


def test_both_legs_run_before_the_reply_watch(runner, monkeypatch):
    calls = []

    def fake_post(url, headers, timeout=None, label=None):
        calls.append(url)
        return _ok(url)
    monkeypatch.setattr(runner, "post_with_retry", fake_post)

    runner.main()

    paths = [u.split("example.invalid")[-1] for u in calls]
    assert any("cns-tracking" in p for p in paths)
    assert any("usps-status" in p for p in paths)
    assert paths[-1].startswith("/api/cron/reply-watch")


def test_the_usps_leg_asks_for_a_three_day_window(runner, monkeypatch):
    """days=3 is the live contract: a scan email can arrive after the event."""
    calls = []
    monkeypatch.setattr(runner, "post_with_retry",
                        lambda url, headers, **k: calls.append(url) or _ok(url))

    runner.main()

    usps = next(u for u in calls if "usps-status" in u)
    assert "days=3" in usps


def test_the_usps_leg_sends_the_secret_in_a_header_not_the_url(runner, monkeypatch):
    """A credential in a URL lands in server and proxy logs."""
    seen = {}

    def fake_post(url, headers, timeout=None, label=None):
        if "usps-status" in url:
            seen["url"], seen["headers"] = url, headers
        return _ok(url)
    monkeypatch.setattr(runner, "post_with_retry", fake_post)

    runner.main()

    assert seen["headers"]["X-Cron-Secret"] == "s3cret"
    assert "s3cret" not in seen["url"]
    assert "key=" not in seen["url"]


@pytest.mark.parametrize("boom", [
    urllib.error.HTTPError("u", 500, "err", None, None),
    RuntimeError("connection reset"),
])
def test_a_failing_usps_leg_does_not_fail_the_reply_watcher(runner, monkeypatch, boom):
    def fake_post(url, headers, timeout=None, label=None):
        if "usps-status" in url:
            raise boom
        return _ok(url)
    monkeypatch.setattr(runner, "post_with_retry", fake_post)

    runner.main()   # must not raise and must not SystemExit


def test_a_404_on_the_usps_leg_is_reported_as_not_yet_deployed(
        runner, monkeypatch, capsys):
    """Render auto-deploy is off, so the cron can run a commit before the web
    service has the route. That must read as 'not deployed', not as a failure."""
    def fake_post(url, headers, timeout=None, label=None):
        if "usps-status" in url:
            raise urllib.error.HTTPError(url, 404, "Not Found", None, None)
        return _ok(url)
    monkeypatch.setattr(runner, "post_with_retry", fake_post)

    runner.main()

    out = capsys.readouterr().out
    assert "[usps-status-cron]" in out and "404" in out


def test_a_failing_reply_watch_still_fails_the_run(runner, monkeypatch):
    """The legs must not mask the job's own health."""
    def fake_post(url, headers, timeout=None, label=None):
        if "reply-watch" in url:
            return json.dumps({"ok": False, "error": "token lost"})
        return _ok(url)
    monkeypatch.setattr(runner, "post_with_retry", fake_post)

    with pytest.raises(SystemExit) as exc:
        runner.main()
    assert exc.value.code == 2


def test_the_usps_leg_summary_names_what_moved(runner, monkeypatch, capsys):
    """A leg that prints nothing useful is a leg nobody can tell is working."""
    monkeypatch.setattr(runner, "post_with_retry",
                        lambda url, headers, **k: _ok(url))

    runner.main()

    out = capsys.readouterr().out
    assert "acted=1" in out
    assert "cards=1" in out
    assert "held=1" in out
