#!/usr/bin/env python3
"""Reply-watcher cron entry. Runs on Render every 15 minutes.

Also carries the USPS Click-N-Ship tracking watcher as a piggyback leg (see
run_cns_tracking below). Same 15-minute cadence, and Render limits how many cron
services exist, so it rides here rather than claiming its own — the same reason
run_personal_email_cron.py carries run_daily_piggybacks.

Stdlib-only (no deps): runs in the cron container and just curls the web service's
/api/cron/reply-watch endpoint. The watcher needs the Gmail token on the persistent
disk (/data/google-token.json) + chat_log.db, which live on the web service — NOT in
the cron container — so the work must happen there. Same cross-container pattern as
run_personal_email_cron.py.

Env (on the cron service):
  WEB_URL       base URL of the web service (default the onrender host)
  CRON_SECRET   shared secret (or CONSOLE_SECRET fallback); sent as X-Cron-Secret
"""
import os
import sys
import json
import urllib.error

from _cron_http import post_with_retry

WEB_URL = os.environ.get("WEB_URL", "https://glen-knowledge-chat.onrender.com").rstrip("/")
CRON_SECRET = os.environ.get("CRON_SECRET") or os.environ.get("CONSOLE_SECRET", "")

if not CRON_SECRET:
    print("ERROR: CRON_SECRET (or CONSOLE_SECRET) not set on cron service", flush=True)
    sys.exit(1)


def run_cns_tracking():
    """USPS Click-N-Ship tracking watcher, folded onto this 15-minute cron.

    Best-effort: a failure here is printed and never changes the reply-watcher's exit
    code, because the two jobs are unrelated and a tracking blip must not mask inbox
    health. days=1 on purpose — the endpoint is idempotent per tracking number, so the
    cadence only ever needs today, and a wide window would mail people about parcels
    that landed weeks ago.
    """
    url = f"{WEB_URL}/api/cron/cns-tracking?days=1"
    headers = {"X-Cron-Secret": CRON_SECRET, "Content-Type": "application/json"}
    try:
        body = json.loads(post_with_retry(url, headers, timeout=300,
                                          label="cns-tracking-cron"))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            print("[cns-tracking-cron] endpoint 404 (not deployed yet) — skip", flush=True)
            return
        print(f"[cns-tracking-cron] HTTP {e.code}: {e.read()[:300]!r}", flush=True)
        return
    except Exception as e:  # noqa: BLE001
        print(f"[cns-tracking-cron] failed: {e!r}", flush=True)
        return

    if not body.get("ok"):
        print(f"[cns-tracking-cron] failed: {body.get('error')}", flush=True)
        return
    print(f"[cns-tracking-cron] mailbox={body.get('mailbox')} "
          f"emails={body.get('emails')} shipments={body.get('shipments')} "
          f"actions={body.get('actions')}", flush=True)


def run_usps_status():
    """Advance order cards from USPS tracking-status emails, on the same cadence.

    Best-effort, exactly like run_cns_tracking: a failure here is printed and never
    changes the reply-watcher's exit code. days=3 rather than 1 because a scan email
    can land a day or two after the event, and unlike the CNS watcher a wide window
    mails nobody — it only moves order cards, and the endpoint is idempotent.
    """
    url = f"{WEB_URL}/api/cron/usps-status?days=3"
    headers = {"X-Cron-Secret": CRON_SECRET, "Content-Type": "application/json"}
    try:
        body = json.loads(post_with_retry(url, headers, timeout=300,
                                          label="usps-status-cron"))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            print("[usps-status-cron] endpoint 404 (not deployed yet) — skip", flush=True)
            return
        print(f"[usps-status-cron] HTTP {e.code}: {e.read()[:300]!r}", flush=True)
        return
    except Exception as e:  # noqa: BLE001
        print(f"[usps-status-cron] failed: {e!r}", flush=True)
        return

    if not body.get("ok"):
        print(f"[usps-status-cron] failed: {body.get('error')}", flush=True)
        return
    print(f"[usps-status-cron] mailbox={body.get('mailbox')} "
          f"emails={body.get('emails')} parcels={body.get('parcels')} "
          f"advanced={body.get('advanced')} held={body.get('pre_transit_held')} "
          f"unknown={body.get('unknown_parcels')} errors={body.get('errors')}",
          flush=True)


def main():
    run_cns_tracking()
    run_usps_status()

    url = f"{WEB_URL}/api/cron/reply-watch"
    headers = {"X-Cron-Secret": CRON_SECRET, "Content-Type": "application/json"}
    # Transient 5xx / connection blips are retried inside post_with_retry; a sustained
    # failure re-raises here and we fail the run as before.
    try:
        body = json.loads(post_with_retry(url, headers, timeout=240,
                                           label="reply-watcher-cron"))
    except urllib.error.HTTPError as e:
        print(f"[reply-watcher-cron] HTTP {e.code}: {e.read()[:300]!r}", flush=True)
        sys.exit(1)
    except Exception as e:  # noqa: BLE001
        print(f"[reply-watcher-cron] failed: {e!r}", flush=True)
        sys.exit(1)

    if not body.get("ok"):
        print(f"[reply-watcher-cron] failed: {body.get('error')}", flush=True)
        sys.exit(2)
    processed = body.get("processed") or 0
    errored = body.get("errored") or 0
    print(f"[reply-watcher-cron] processed={processed} "
          f"skipped_nonuser={body.get('skipped_nonuser')} "
          f"errored={errored}", flush=True)
    # Systematic failure (e.g. token lost gmail.modify): nothing processed but the
    # batch errored. Surface as a failed job instead of a healthy-looking exit 0.
    if errored and not processed:
        sys.exit(3)


if __name__ == "__main__":
    main()
