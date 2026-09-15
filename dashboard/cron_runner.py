"""Run a scheduled script as a child process with a hard time budget. No Flask, no network.

The in-app console push ran console_push_cron.py through subprocess.run(timeout=300). It hit
that timeout 33 times between 2026-09-12 and 2026-09-15. Two things made that invisible:
- subprocess.run discards the child's output on a timeout, so the slow step never showed.
- After killing the child it waits for the pipes to close, and a grandchild still holding
  stdout can keep that wait going past the budget.

This runner starts the child in its own session, kills the whole process group on a timeout,
collects whatever output there was, and always prints the tail with flush=True.
"""
import os
import signal
import subprocess
import time

TAIL_CHARS = 3000
DRAIN_SECS = 5


def _kill_group(proc):
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


def _tail(text, n=TAIL_CHARS):
    return (text or "")[-n:]


def run_script(args, timeout, env=None, label="job"):
    """Run `args` with a `timeout` in seconds. Returns a dict:
    ok, returncode, timed_out, duration_s, stdout, stderr, error.
    `error` is a short category ("timeout", "exit 2", "exception: OSError"), never output text."""
    started = time.monotonic()
    out = {"ok": False, "returncode": None, "timed_out": False, "duration_s": 0.0,
           "stdout": "", "stderr": "", "error": ""}
    try:
        proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True, env=env, start_new_session=True)
    except Exception as e:
        out["error"] = f"exception: {type(e).__name__}"
        out["duration_s"] = round(time.monotonic() - started, 1)
        print(f"[{label}] could not start: {type(e).__name__}", flush=True)
        return out
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        out["timed_out"] = True
        _kill_group(proc)
        try:
            stdout, stderr = proc.communicate(timeout=DRAIN_SECS)
        except subprocess.TimeoutExpired:
            for pipe in (proc.stdout, proc.stderr):
                try:
                    pipe.close()
                except Exception:
                    pass
            stdout, stderr = "", ""
    out["returncode"] = proc.returncode
    out["stdout"], out["stderr"] = stdout or "", stderr or ""
    out["duration_s"] = round(time.monotonic() - started, 1)
    if out["timed_out"]:
        out["error"] = f"timeout after {timeout}s"
    elif proc.returncode != 0:
        out["error"] = f"exit {proc.returncode}"
    else:
        out["ok"] = True
    state = "ok" if out["ok"] else out["error"]
    print(f"[{label}] {state} in {out['duration_s']}s", flush=True)
    print(_tail(out["stdout"]) or "(no output)", flush=True)
    if out["stderr"] and not out["ok"]:
        print(f"[{label}] stderr: {_tail(out['stderr'], 1000)}", flush=True)
    return out
