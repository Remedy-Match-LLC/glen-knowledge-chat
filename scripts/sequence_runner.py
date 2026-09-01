#!/usr/bin/env python3
"""Send whatever is due on an active sequence. One tick.

Slice 3 of docs/superpowers/specs/2026-08-30-sequence-engine-design.md. Registered
as a Render cron; every sequence ships inactive, so this sends nothing until a
deliberate `set_active`.

Order of operations is the safety design:

  1. release stale claims  — a crash mid-send must not wedge a step forever
  2. skip long-overdue     — a backdated enrollment must not release a burst
  3. claim                 — UNIQUE(slug, step_no, email) arbitrates, not a read
  4. check suppression     — at SEND time; someone can opt out on day 2 of 25
  5. send                  — through the same transport as every other bulk mail
  6. record sent/failed

Claim-before-send means a crash between 3 and 5 leaves a stuck row that step 1
reopens. The alternative failure mode is a duplicate, and a duplicate cannot be
recalled.

Usage:
    python3 scripts/sequence_runner.py --dry-run
    python3 scripts/sequence_runner.py
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

# Own guard, at this module's own entry point. send_via_ghl has one too, but a
# bare full-suite run has sent real email before and one layer was not enough.
_UNDER_TEST = bool(os.environ.get("PYTEST_CURRENT_TEST"))

MAX_CATCHUP_DAYS = 2
STALE_CLAIM_MINUTES = 60


def _html(body_md, email, slug):
    """Markdown-ish body to HTML, plus the unsubscribe footer.

    The footer is appended AFTER escaping so its anchor survives as markup, the
    same ordering weekly_live_invitation needs.
    """
    import html as _h

    from dashboard import unsubscribe as _un
    esc = _h.escape(body_md).replace("\n\n", "</p><p>").replace("\n", "<br>")
    doc = ('<div style="font-family:Arial,sans-serif;font-size:16px;'
           'line-height:1.55"><p>' + esc + "</p></div>")
    return doc + _un.footer_html(email, slug)


def send_one(*, to_email, subject, html):
    """Send one sequence email. Returns a message id, or None when guarded."""
    if _UNDER_TEST:
        return None
    from dashboard import ghl_email
    res = ghl_email.send_via_ghl(to_email, subject, html=html,
                                 from_name="Dr. Glen Swartwout")
    return (res or {}).get("id")


def run_once(cx, *, now=None, dry_run=False, max_catchup_days=MAX_CATCHUP_DAYS):
    from dashboard import email_suppression as _es, sequences as _seq

    counts = {"released": 0, "skipped_stale": 0, "would_send": 0,
              "sent": 0, "failed": 0, "suppressed": 0}

    if not dry_run:
        counts["released"] = _seq.release_stale_claims(
            cx, now=now, older_than_minutes=STALE_CLAIM_MINUTES)

    for s in _seq.stale_steps(cx, now=now, max_catchup_days=max_catchup_days):
        counts["skipped_stale"] += 1
        if not dry_run:
            _seq.mark_skipped(
                cx, s["slug"], s["step_no"], s["email"],
                f"overdue: due {s['due_at']}, beyond the {max_catchup_days}d "
                "catch-up window")

    for d in _seq.due(cx, now=now, max_catchup_days=max_catchup_days):
        if dry_run:
            counts["would_send"] += 1
            print(f"[dry] {d['email']} <- step {d['step_no']} \"{d['subject'][:50]}\"")
            continue
        if not _seq.claim(cx, d["slug"], d["step_no"], d["email"]):
            continue                      # another process owns this send
        # Checked here, not at enrollment: a 25-day drip outlives its consent.
        if _es.is_suppressed(cx, d["email"]):
            counts["suppressed"] += 1
            _seq.mark_skipped(cx, d["slug"], d["step_no"], d["email"],
                              "suppressed or unsubscribed")
            continue
        try:
            mid = send_one(to_email=d["email"], subject=d["subject"],
                           html=_html(d["body_md"], d["email"], d["slug"]))
            _seq.mark_sent(cx, d["slug"], d["step_no"], d["email"], mid or "")
            counts["sent"] += 1
        except Exception as e:  # noqa: BLE001 — one bad address must not stop the tick
            _seq.mark_failed(cx, d["slug"], d["step_no"], d["email"], repr(e))
            counts["failed"] += 1
            print(f"[seq] send failed {d['email']} step {d['step_no']}: {e!r}",
                  flush=True)
    return counts


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--max-catchup-days", type=int, default=MAX_CATCHUP_DAYS)
    a = ap.parse_args(argv)

    import app as appmod
    from dashboard import db, email_suppression as _es, sequences as _seq
    with db.connect(appmod.LOG_DB) as cx:
        _seq.init_tables(cx)
        _es.init_table(cx)
        counts = run_once(cx, dry_run=a.dry_run,
                          max_catchup_days=a.max_catchup_days)
    print(json.dumps({"dry_run": a.dry_run, **counts}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
