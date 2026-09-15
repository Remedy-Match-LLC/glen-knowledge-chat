"""The GHL people sync as its own scheduled job, with a hard time budget and a recorded status.

Render's in-app console push timed out 33 times between 2026-09-12 and 2026-09-15, and the
people sync inside it rarely ran. These tests pin the three parts of the fix:
- cron_runner kills a timed-out child's whole process group and still returns its output;
- cron_status records every result, and a success resets the failure count;
- console_push_cron's flags split the people sync from the rest, with the default unchanged.
"""
import os
import sqlite3
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from dashboard import cron_runner, cron_status  # noqa: E402

PY = sys.executable


# ── cron_runner ───────────────────────────────────────────────────────────────

def test_a_successful_child_returns_ok_and_its_output(capsys):
    res = cron_runner.run_script([PY, "-c", "print('hello from child')"], 20, label="t")
    assert res["ok"] and res["returncode"] == 0 and not res["timed_out"]
    assert "hello from child" in res["stdout"]
    assert res["error"] == ""
    assert "hello from child" in capsys.readouterr().out


def test_a_non_zero_exit_is_a_failure_with_a_short_category():
    res = cron_runner.run_script([PY, "-c", "import sys; print('x'); sys.exit(3)"], 20)
    assert not res["ok"] and res["returncode"] == 3
    assert res["error"] == "exit 3"


def test_a_slow_child_times_out_and_keeps_its_partial_output(capsys):
    code = "import sys,time; print('step one done', flush=True); time.sleep(60)"
    started = time.monotonic()
    res = cron_runner.run_script([PY, "-c", code], 2, label="slow")
    assert time.monotonic() - started < 15
    assert res["timed_out"] and not res["ok"]
    assert res["error"] == "timeout after 2s"
    assert "step one done" in res["stdout"]
    assert "step one done" in capsys.readouterr().out


def test_a_grandchild_holding_the_pipe_cannot_stretch_the_budget():
    # The child starts a grandchild that inherits stdout and sleeps, then the child sleeps too.
    # Killing only the child would leave the pipe open until the grandchild exits.
    code = ("import subprocess,sys,time; "
            "subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']); "
            "print('spawned', flush=True); time.sleep(60)")
    started = time.monotonic()
    res = cron_runner.run_script([PY, "-c", code], 2, label="grandchild")
    elapsed = time.monotonic() - started
    assert res["timed_out"]
    assert elapsed < 2 + cron_runner.DRAIN_SECS - 1, elapsed
    assert "spawned" in res["stdout"]


def test_a_child_that_cannot_start_is_a_failure_not_a_raise():
    res = cron_runner.run_script(["/nonexistent/interpreter-xyz"], 5)
    assert not res["ok"] and res["error"].startswith("exception: ")


# ── cron_status ───────────────────────────────────────────────────────────────

@pytest.fixture
def cx(tmp_path):
    c = sqlite3.connect(str(tmp_path / "status.db"))
    cron_status.init_table(c)
    yield c
    c.close()


def _one(cx, job):
    return {d["job"]: d for d in cron_status.all_status(cx)}[job]


def test_failures_count_up_and_a_success_resets_them(cx):
    t0 = datetime(2026, 9, 15, 12, 0, tzinfo=timezone.utc)
    cron_status.record_start(cx, "people_sync", now=t0)
    cron_status.record_result(cx, "people_sync", True, "", 61.2, now=t0)
    for i in range(3):
        cron_status.record_start(cx, "people_sync", now=t0 + timedelta(hours=i + 1))
        cron_status.record_result(cx, "people_sync", False, "timeout after 240s", 240.0,
                                  now=t0 + timedelta(hours=i + 1))
    d = cron_status.all_status(cx, now=t0 + timedelta(hours=3, minutes=5))[0]
    assert d["consecutive_failures"] == 3
    assert d["last_error"] == "timeout after 240s"
    assert d["last_success_utc"] == t0.isoformat()
    assert d["stale"] and d["alert"]

    cron_status.record_result(cx, "people_sync", True, "", 58.0, now=t0 + timedelta(hours=4))
    d = cron_status.all_status(cx, now=t0 + timedelta(hours=4, minutes=1))[0]
    assert d["consecutive_failures"] == 0
    assert d["last_error"] == ""
    assert d["last_success_utc"] == (t0 + timedelta(hours=4)).isoformat()
    assert not d["stale"] and not d["alert"]


def test_three_failures_alert_even_when_the_last_success_is_recent(cx):
    now = datetime(2026, 9, 15, 12, 0, tzinfo=timezone.utc)
    cron_status.record_result(cx, "console_push", True, "", 70.0, now=now - timedelta(minutes=50))
    for _ in range(3):
        cron_status.record_result(cx, "console_push", False, "exit 1", 3.0, now=now)
    d = cron_status.all_status(cx, now=now)[0]
    assert not d["stale"] and d["alert"]


def test_a_job_that_never_succeeded_is_stale(cx):
    cron_status.record_start(cx, "people_sync")
    d = _one(cx, "people_sync")
    assert d["last_success_utc"] is None and d["stale"] and d["alert"]


def test_a_long_error_is_cut_to_120_characters(cx):
    cron_status.record_result(cx, "j", False, "e" * 500, 1.0)
    assert len(_one(cx, "j")["last_error"]) == 120


# ── console_push_cron flags ───────────────────────────────────────────────────

@pytest.fixture
def cron(monkeypatch):
    import console_push_cron as c
    calls = []

    def rec(name, value=None):
        def f(*a, **k):
            calls.append(name)
            return value
        return f

    for name, value in [("triage_gmail", []), ("triage_pb", []), ("triage_starred", []),
                        ("triage_remedy_gmail", []), ("fetch_ghl_tasks", []),
                        ("_post_todos", None), ("sync_people_from_ghl", None),
                        ("push_calendar_events", None), ("process_delete_queue", None),
                        ("push_projects_md", None), ("push_task_board", None),
                        ("_gmail_service", None)]:
        monkeypatch.setattr(c, name, rec(name, value))
    monkeypatch.setattr(c, "GLEN_TOKEN", Path("/nonexistent/glen-token.json"))
    monkeypatch.setattr(c, "RAE_TOKEN", Path("/nonexistent/rae-token.json"))
    return c, calls


def test_people_only_runs_only_the_people_sync(cron):
    c, calls = cron
    c.main(["--people-only"])
    assert calls == ["sync_people_from_ghl"]


def test_skip_people_runs_everything_except_the_people_sync(cron):
    c, calls = cron
    c.main(["--skip-people"])
    assert "sync_people_from_ghl" not in calls
    assert {"fetch_ghl_tasks", "_post_todos", "push_calendar_events", "push_task_board"} <= set(calls)


def test_no_flag_is_the_full_run_the_mac_backstop_depends_on(cron):
    c, calls = cron
    c.main([])
    assert "sync_people_from_ghl" in calls
    assert calls.index("_post_todos") < calls.index("sync_people_from_ghl") < calls.index("push_task_board")


def test_people_only_exits_non_zero_when_the_sync_had_errors(cron, monkeypatch):
    c, _ = cron
    monkeypatch.setattr(c, "sync_people_from_ghl", lambda *a, **k: 2)
    with pytest.raises(SystemExit) as e:
        c.main(["--people-only"])
    assert e.value.code == 1


def test_the_full_run_keeps_exit_zero_when_the_sync_had_errors(cron, monkeypatch):
    c, calls = cron
    monkeypatch.setattr(c, "sync_people_from_ghl", lambda *a, **k: calls.append("sync") or 2)
    c.main([])  # the Mac backstop's mode: no SystemExit
    assert "sync" in calls


class _Resp:
    def __init__(self, status, body):
        self.status_code, self._body = status, body

    def json(self):
        if isinstance(self._body, Exception):
            raise self._body
        return self._body


@pytest.mark.parametrize("upsert", [_Resp(502, ValueError("html")), _Resp(401, {"error": "Unauthorized"})])
def test_a_failed_people_upsert_counts_as_an_error(upsert, monkeypatch):
    import console_push_cron as c
    posted = []
    monkeypatch.setattr(c, "GHL_API_KEY", "fake-ghl")
    monkeypatch.setattr(c, "fetch_email_dnd_v2", lambda: None)
    monkeypatch.setattr(c.requests, "get", lambda *a, **k: _Resp(200, {"contacts": [
        {"id": "c1", "email": "a@example.invalid", "tags": []}]}))
    monkeypatch.setattr(c.requests, "post", lambda *a, **k: posted.append(1) or upsert)
    assert c.sync_people_from_ghl() == 1
    assert posted == [1]  # the upsert was actually reached


def test_a_good_people_upsert_counts_no_error(monkeypatch):
    import console_push_cron as c
    monkeypatch.setattr(c, "GHL_API_KEY", "fake-ghl")
    monkeypatch.setattr(c, "fetch_email_dnd_v2", lambda: None)
    monkeypatch.setattr(c.requests, "get", lambda *a, **k: _Resp(200, {"contacts": [
        {"id": "c1", "email": "a@example.invalid", "tags": []}]}))
    monkeypatch.setattr(c.requests, "post", lambda *a, **k: _Resp(200, {"inserted": 1, "updated": 0}))
    assert c.sync_people_from_ghl() == 0


def test_the_two_flags_cannot_be_combined(cron):
    c, _ = cron
    with pytest.raises(SystemExit):
        c.main(["--people-only", "--skip-people"])


# ── app wiring and the status endpoint ────────────────────────────────────────

@pytest.fixture
def app_mod(monkeypatch, tmp_path):
    os.environ.setdefault("OPENAI_API_KEY", "sk-dummy")
    os.environ.setdefault("PINECONE_API_KEY", "pc-dummy")
    import app
    path = str(tmp_path / "chat_log.db")
    monkeypatch.setattr(app, "LOG_DB", path)
    monkeypatch.setattr(app, "CONSOLE_SECRET", "testkey")  # an absent secret opens the gate
    return app


def test_the_status_endpoint_needs_the_console_key(app_mod):
    c = app_mod.app.test_client()
    assert c.get("/api/console/cron-status").status_code == 401
    assert c.get("/api/console/cron-status", headers={"X-Console-Key": "wrong"}).status_code == 401


def test_the_people_sync_job_records_a_timeout_and_the_endpoint_reports_it(app_mod, monkeypatch):
    seen = {}

    def fake_run(args, timeout, env=None, label="job"):
        seen.update(args=args, timeout=timeout, label=label)
        return {"ok": False, "returncode": -9, "timed_out": True, "duration_s": 240.0,
                "stdout": "partial", "stderr": "", "error": "timeout after 240s"}

    monkeypatch.setattr(cron_runner, "run_script", fake_run)
    app_mod._run_people_sync()
    assert seen["args"][-1] == "--people-only"
    assert seen["timeout"] == app_mod._PEOPLE_SYNC_TIMEOUT_S == 240
    assert seen["label"] == "people_sync"

    r = app_mod.app.test_client().get("/api/console/cron-status", headers={"X-Console-Key": "testkey"})
    assert r.status_code == 200
    jobs = {j["job"]: j for j in r.get_json()["jobs"]}
    ps = jobs["people_sync"]
    assert ps["consecutive_failures"] == 1 and ps["last_error"] == "timeout after 240s"
    assert ps["stale"] and ps["alert"]
    assert "partial" not in r.get_data(as_text=True)  # output text never leaves the server


def test_the_console_push_job_skips_people_and_still_saves_tokens(app_mod, monkeypatch, tmp_path):
    seen = {}

    def fake_run(args, timeout, env=None, label="job"):
        seen.update(args=args, label=label)
        return {"ok": True, "returncode": 0, "timed_out": False, "duration_s": 70.0,
                "stdout": "", "stderr": "", "error": ""}

    monkeypatch.setattr(cron_runner, "run_script", fake_run)
    monkeypatch.setattr(app_mod, "_CRON_TOKEN_DIR", str(tmp_path))  # never the real /tmp
    with sqlite3.connect(app_mod.LOG_DB) as c:
        c.execute("CREATE TABLE IF NOT EXISTS oauth_tokens (name TEXT PRIMARY KEY, token_json TEXT, updated_at TEXT)")
        c.execute("INSERT INTO oauth_tokens VALUES ('calendar', '{\"fake\": 1}', 'x')")
    app_mod._run_cron()
    assert seen["args"][-1] == "--skip-people" and seen["label"] == "console_push"
    with sqlite3.connect(app_mod.LOG_DB) as c:
        row = c.execute("SELECT updated_at FROM oauth_tokens WHERE name='calendar'").fetchone()
        st = c.execute("SELECT consecutive_failures, last_success_utc FROM cron_job_status WHERE job='console_push'").fetchone()
    assert row[0] != "x"  # the save-back ran after the job
    assert st[0] == 0 and st[1]


def test_the_scheduler_registers_people_sync_as_its_own_job(app_mod, monkeypatch):
    added = []

    class FakeScheduler:
        def add_job(self, fn, trigger, **kw):
            added.append((kw.get("id"), fn, kw))

        def start(self):
            pass

    import apscheduler.schedulers.background as bg
    monkeypatch.setattr(bg, "BackgroundScheduler", FakeScheduler)
    app_mod._start_scheduler()
    jobs = {i: (fn, kw) for i, fn, kw in added}
    assert jobs["people_sync"][0] is app_mod._run_people_sync
    assert jobs["console_push"][0] is app_mod._run_cron
    assert jobs["people_sync"][1]["next_run_time"] > jobs["console_push"][1]["next_run_time"]
