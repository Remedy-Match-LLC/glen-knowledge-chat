#!/usr/bin/env python3
"""Send whatever is due on an active sequence. One tick.

Slice 3 of docs/superpowers/specs/2026-08-30-sequence-engine-design.md. Registered
as a Render cron; every sequence ships inactive, so this sends nothing until a
deliberate `set_active`.

Order of operations is the safety design:

  1. close stale claims    — a crash mid-send must not wedge a step forever. The
                             step is closed as skipped, never reopened: SES may have
                             taken it before the crash.
  2. skip long-overdue     — a backdated enrollment must not release a burst
  3. claim                 — UNIQUE(slug, step_no, email) arbitrates, not a read
  4. check suppression     — at SEND time; someone can opt out on day 2 of 25
  5. send                  — through Amazon SES, never GoHighLevel (Glen, 14 Nov 2026:
                             GHL is being retired). SES adds the unsubscribe footer and
                             the one-click headers, scoped to the sequence.
  6. record sent/failed. A send SES surely did not make because it is switched off
     frees its claim, so a later tick tries again inside the catch-up window. A
     refusal while SES is on is recorded as failed and not retried. A send SES may have made is
     recorded as sent and never retried: a duplicate cannot be recalled.

Claim-before-send means a crash between 3 and 6 leaves a stuck row that step 1
closes. That can cost one email. The alternative failure mode is a duplicate, and a
duplicate cannot be recalled.

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


def _html(body_md):
    """Markdown-ish body to HTML. The unsubscribe footer is added by ses_mail, after
    this escaping, so its anchor survives as markup."""
    import html as _h
    esc = _h.escape(body_md).replace("\n\n", "</p><p>").replace("\n", "<br>")
    return ('<div style="font-family:Arial,sans-serif;font-size:16px;'
            'line-height:1.55"><p>' + esc + "</p></div>")


class _Lent:
    """Lends the tick's open connection to ses_mail.deliver for its suppression check,
    without closing it (closing a pooled connection returns it to the pool)."""
    def __init__(self, cx):
        self.cx = cx

    def __enter__(self):
        return self.cx

    def __exit__(self, *exc):
        return False


def send_one(cx, *, to_email, subject, body_md, slug):
    """Send one sequence email through SES, as marketing mail with an unsubscribe
    scoped to the sequence. Returns ses_mail.deliver's (outcome, message_id):
    "ses", "suppressed", "unknown", or None when SES surely sent nothing. There is
    no GoHighLevel fallback. Under pytest it sends nothing and returns (None, None)."""
    if _UNDER_TEST:
        return None, None
    from dashboard import ses_mail
    return ses_mail.deliver(lambda: _Lent(cx), to_email, subject, body_md,
                            _html(body_md), marketing=True, scope=slug,
                            from_name="Dr. Glen Swartwout")


def _release(cx, slug, step_no, email):
    """Free this tick's claim on a send SES surely did not make. A failed query inside
    deliver can leave a Postgres transaction aborted, so it is rolled back first (the
    claim itself was committed by claim())."""
    try:
        cx.rollback()
    except Exception:
        pass
    cx.execute("DELETE FROM sequence_sends WHERE slug=? AND step_no=? AND email=? "
               "AND status='claimed'", (slug, int(step_no), (email or "").strip().lower()))
    cx.commit()


def run_once(cx, *, now=None, dry_run=False, max_catchup_days=MAX_CATCHUP_DAYS):
    from dashboard import email_suppression as _es, sequences as _seq

    counts = {"abandoned": 0, "skipped_stale": 0, "would_send": 0,
              "sent": 0, "failed": 0, "suppressed": 0, "unknown": 0, "not_sent": 0}

    if not dry_run:
        counts["abandoned"] = _seq.abandon_stale_claims(
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
        if any(c in d["email"] for c in "\r\n\t ") or "@" not in d["email"]:
            # A malformed address fails while the message is built, which SES reports
            # as "unknown". Refused here, so it is never recorded as sent (round 3).
            _seq.mark_failed(cx, d["slug"], d["step_no"], d["email"], "malformed address")
            counts["failed"] += 1
            continue
        try:
            outcome, mid = send_one(cx, to_email=d["email"], subject=d["subject"],
                                    body_md=d["body_md"], slug=d["slug"])
        except Exception as e:  # noqa: BLE001 — one bad address must not stop the tick
            _seq.mark_failed(cx, d["slug"], d["step_no"], d["email"], type(e).__name__)
            counts["failed"] += 1
            print(f"[seq] send failed step {d['step_no']}: {type(e).__name__}", flush=True)
            continue
        if outcome == "ses":
            _seq.mark_sent(cx, d["slug"], d["step_no"], d["email"], mid or "")
            counts["sent"] += 1
        elif outcome == "unknown":
            # SES may have taken it. Recorded as sent so it is never sent twice.
            _seq.mark_sent(cx, d["slug"], d["step_no"], d["email"], "ses-unknown")
            counts["unknown"] += 1
        elif outcome == "suppressed":
            _seq.mark_skipped(cx, d["slug"], d["step_no"], d["email"],
                              "suppressed (SES check)")
            counts["suppressed"] += 1
        else:
            # SES surely sent nothing. Switched off: the claim is freed so the step
            # goes out once SES is back on (inside the catch-up window). Switched on:
            # SES refused or was unreachable, so it is recorded as failed with that
            # reason and not retried every tick (review round 3).
            from dashboard import ses_mail
            if not ses_mail.enabled():
                _release(cx, d["slug"], d["step_no"], d["email"])
                counts["not_sent"] += 1
            else:
                _seq.mark_failed(cx, d["slug"], d["step_no"], d["email"],
                                 "SES refused or unreachable; nothing sent")
            counts["failed"] += 1
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
