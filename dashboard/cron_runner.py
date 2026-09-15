"""Run a scheduled script as a child process with a hard time budget. No Flask, no network.

The in-app console push ran console_push_cron.py through subprocess.run(timeout=300). It hit
that timeout 33 times between 2026-09-12 and 2026-09-15. Two things made that invisible:
- subprocess.run discards the child's output on a timeout, so the slow step never showed.
- After killing the child it waits for the pipes to close, and a grandchild still holding
  stdout can keep that wait going past the budget.

Measured after #1690 went live, 2026-09-15: all 36 "timed out after 300" lines since 09-12 were
logged 29 to 49 seconds before the next scheduler restart, and 169 hourly runs were skipped as
"maximum number of running instances". The in-process timer only fired when a deploy shut the
worker down. Under gunicorn's gevent worker it is not a hard budget.

So the budget is enforced OUTSIDE Python:
- The child runs under coreutils `timeout`, which signals the child's whole process group at
  the budget and sends SIGKILL `KILL_AFTER_SECS` later. /usr/bin/timeout exists on Render.
- Output goes to unnamed temporary files, not pipes, so no grandchild can hold a pipe open
  and the parent only waits for the child's exit.
- The in-process wait stays as a backstop, `BACKSTOP_SECS` past the hard kill.
- Progress lines are printed with flush=True, so a hang shows where it stopped.
"""
import os
import shutil
import signal
import subprocess
import tempfile
import time

TAIL_CHARS = 3000
KILL_AFTER_SECS = 10
BACKSTOP_SECS = 30
DRAIN_SECS = 5
# coreutils timeout exits 124 when the budget ran out, and 128+9 when it had to SIGKILL.
_TIMEOUT_CODES = (124, 137)


def _kill_group(proc):
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


def _tail(text, n=TAIL_CHARS):
    return (text or "")[-n:]


def _read(f):
    try:
        f.seek(0)
        return f.read().decode("utf-8", errors="replace")
    except Exception:
        return ""


def hard_budget_argv(args, timeout, timeout_bin=None):
    """`args` wrapped in coreutils timeout, or unchanged when the binary is missing."""
    tb = timeout_bin if timeout_bin is not None else shutil.which("timeout")
    if not tb:
        return list(args), False
    return [tb, "-k", str(KILL_AFTER_SECS), str(int(timeout))] + list(args), True


def run_script(args, timeout, env=None, label="job"):
    """Run `args` with a `timeout` in seconds. Returns a dict:
    ok, returncode, timed_out, duration_s, stdout, stderr, error.
    `error` is a short category ("timeout after 300s", "exit 2", "exception: OSError"),
    never output text."""
    started = time.monotonic()
    out = {"ok": False, "returncode": None, "timed_out": False, "duration_s": 0.0,
           "stdout": "", "stderr": "", "error": ""}
    argv, hard = hard_budget_argv(args, timeout)
    child_env = dict(os.environ if env is None else env)
    child_env.setdefault("PYTHONUNBUFFERED", "1")  # a killed child still leaves its output
    with tempfile.TemporaryFile() as fo, tempfile.TemporaryFile() as fe:
        try:
            proc = subprocess.Popen(argv, stdout=fo, stderr=fe, env=child_env,
                                    start_new_session=True)
        except Exception as e:
            out["error"] = f"exception: {type(e).__name__}"
            out["duration_s"] = round(time.monotonic() - started, 1)
            print(f"[{label}] could not start: {type(e).__name__}", flush=True)
            return out
        print(f"[{label}] started pid {proc.pid}, budget {timeout}s, "
              f"{'hard' if hard else 'in-process only'}", flush=True)
        # With the hard wrapper, the in-process wait is only a backstop past its SIGKILL.
        # Without it, the in-process wait is the whole budget, as before.
        backstop = timeout + KILL_AFTER_SECS + BACKSTOP_SECS if hard else timeout
        try:
            proc.wait(timeout=backstop)
        except subprocess.TimeoutExpired:
            out["timed_out"] = True
            print(f"[{label}] backstop reached at {backstop}s, killing the group", flush=True)
            _kill_group(proc)
            try:
                proc.wait(timeout=DRAIN_SECS)
            except subprocess.TimeoutExpired:
                pass
        rc = proc.returncode
        if hard and rc in _TIMEOUT_CODES:
            out["timed_out"] = True
        _kill_group(proc)  # anything the child left behind in its group
        out["returncode"] = rc
        out["stdout"], out["stderr"] = _read(fo), _read(fe)
    out["duration_s"] = round(time.monotonic() - started, 1)
    if out["timed_out"]:
        out["error"] = f"timeout after {timeout}s"
    elif rc != 0:
        out["error"] = f"exit {rc}"
    else:
        out["ok"] = True
    state = "ok" if out["ok"] else out["error"]
    print(f"[{label}] {state} in {out['duration_s']}s", flush=True)
    print(_tail(out["stdout"]) or "(no output)", flush=True)
    if out["stderr"] and not out["ok"]:
        print(f"[{label}] stderr: {_tail(out['stderr'], 1000)}", flush=True)
    return out
