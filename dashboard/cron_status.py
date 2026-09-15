"""Last-run status for the in-app scheduled jobs. Pure SQL; no Flask, no network.

A job that times out used to print "[CRON] Exception" and record nothing, so 33 timeouts in
three days never reached the job-health digest. Each run now records its start and its result
here, and GET /api/console/cron-status reads it back.

Postgres in production: ON CONFLICT upserts only, no lastrowid, no cursor.description.
"""
from datetime import datetime, timedelta, timezone

STALE_AFTER = timedelta(hours=3)
FAILURE_ALERT = 3
_COLS = ("job", "last_start_utc", "last_success_utc", "last_error",
         "consecutive_failures", "last_duration_s")


def _now():
    return datetime.now(timezone.utc)


def init_table(cx):
    cx.execute("""
        CREATE TABLE IF NOT EXISTS cron_job_status (
            job                  TEXT PRIMARY KEY,
            last_start_utc       TEXT,
            last_success_utc     TEXT,
            last_error           TEXT,
            consecutive_failures INTEGER NOT NULL DEFAULT 0,
            last_duration_s      REAL
        )
    """)
    cx.commit()


def record_start(cx, job, now=None):
    ts = (now or _now()).isoformat()
    cx.execute(
        "INSERT INTO cron_job_status (job, last_start_utc, consecutive_failures) VALUES (?,?,0) "
        "ON CONFLICT (job) DO UPDATE SET last_start_utc=excluded.last_start_utc",
        (job, ts))
    cx.commit()


def record_result(cx, job, ok, error="", duration_s=None, now=None):
    """Success sets last_success_utc and resets the failure count. A failure keeps the last
    success and adds one to the count. `error` is a short category, cut to 120 characters."""
    ts = (now or _now()).isoformat()
    err = (error or "")[:120]
    if ok:
        cx.execute(
            "INSERT INTO cron_job_status (job, last_success_utc, last_error, consecutive_failures, "
            "last_duration_s) VALUES (?,?,'',0,?) "
            "ON CONFLICT (job) DO UPDATE SET last_success_utc=excluded.last_success_utc, "
            "last_error='', consecutive_failures=0, last_duration_s=excluded.last_duration_s",
            (job, ts, duration_s))
    else:
        cx.execute(
            "INSERT INTO cron_job_status (job, last_error, consecutive_failures, last_duration_s) "
            "VALUES (?,?,1,?) "
            "ON CONFLICT (job) DO UPDATE SET last_error=excluded.last_error, "
            "consecutive_failures=cron_job_status.consecutive_failures + 1, "
            "last_duration_s=excluded.last_duration_s",
            (job, err, duration_s))
    cx.commit()


def _parse(ts):
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(str(ts))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def all_status(cx, now=None):
    """[{job, last_start_utc, last_success_utc, last_error, consecutive_failures,
    last_duration_s, stale, alert}] sorted by job. `stale` means no success in 3 hours.
    `alert` means stale or 3 failures in a row."""
    now = now or _now()
    rows = cx.execute(
        "SELECT job, last_start_utc, last_success_utc, last_error, consecutive_failures, "
        "last_duration_s FROM cron_job_status ORDER BY job").fetchall()
    out = []
    for r in rows:
        d = dict(zip(_COLS, list(r)))
        d["consecutive_failures"] = int(d["consecutive_failures"] or 0)
        ok_at = _parse(d["last_success_utc"])
        d["stale"] = ok_at is None or (now - ok_at) > STALE_AFTER
        d["alert"] = d["stale"] or d["consecutive_failures"] >= FAILURE_ALERT
        out.append(d)
    return out
