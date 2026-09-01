#!/usr/bin/env python3
"""Render cron entry point: POST one sequence tick to the web service.

Stdlib only and deliberately thin. The logic lives in the web service
(/api/cron/sequence-tick -> scripts.sequence_runner.run_once) so it runs with the
production database and secrets rather than a second copy of them.
"""
import json
import os
import sys
import urllib.error
import urllib.request

BASE = os.environ.get("WEB_URL", "https://glen-knowledge-chat.onrender.com").rstrip("/")
SECRET = os.environ.get("CRON_SECRET") or os.environ.get("CONSOLE_SECRET", "")


def main():
    if not SECRET:
        print("no CRON_SECRET; refusing to run", flush=True)
        return 1
    req = urllib.request.Request(f"{BASE}/api/cron/sequence-tick", method="POST",
                                 data=b"", headers={"X-Cron-Secret": SECRET})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            body = json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        print(f"tick failed: HTTP {e.code} {e.read().decode()[:200]}", flush=True)
        return 1
    print(json.dumps(body), flush=True)
    # A tick that sent nothing is normal (every sequence ships inactive); a tick
    # that FAILED sends is not, so surface it in the cron's exit status.
    return 1 if body.get("failed") else 0


if __name__ == "__main__":
    sys.exit(main())
