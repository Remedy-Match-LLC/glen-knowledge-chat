"""Reconstruct affiliate attribution that was never recorded.

WHY THIS EXISTS. Affiliate payout asks one question at settlement: is there a
referral_events row for this buyer naming an approved affiliate? Until 2026-09-05 only
three things wrote one (the ScoreApp webhook, masterclass signups, concierge signups).
An ordinary ?ref= visit and a redeemed referral code both wrote nothing, so most people
who were genuinely referred earned their referrer no credit.

That is now fixed forward. This module is about the past.

WHAT CAN BE RECOVERED, and what cannot:

  inquiries            client_email + ref_slug   captured when someone identified
                                                 themselves. The best source, because it
                                                 is the same moment the live code now
                                                 writes an attribution.
  referral_redemptions referee + owner email     a redeemed referral code. Two of its
                                                 three writers are switched off, so this
                                                 is mostly gift coupons, but every row is
                                                 a real recorded relationship.
  affiliate_conversions email + affiliate_slug   a conversion already credited. Weakest
                                                 evidence: a conversion implies an
                                                 attribution existed at the time.

  NOT RECOVERABLE: anyone who arrived by affiliate link, browsed and bought without ever
  submitting an inquiry or using a code. Their referral lived only in a browser cookie
  and left no server-side trace. Probably the largest group. It is gone.

TWO RULES, both deliberate:

  1. FIRST TOUCH ACROSS THE WHOLE RUN, not per row. Replaying history out of order would
     hand each referral to whoever appears last in the data rather than first in time.
     Candidates are therefore sorted by their own timestamp and the earliest wins.
  2. ATTRIBUTION ONLY. Writes no reward and stamps nothing as rewarded, matching
     dashboard/referral_backfill.py. Paying out on reconstructed history is far harder to
     undo than to skip.

Dry run is the default and the only mode the CLI offers without an explicit flag.
"""
from dashboard import db

_MEDIUM = "backfill"


def _approved_slugs(cx):
    return {r[0] for r in cx.execute(
        "SELECT slug FROM affiliate_signups WHERE status='approved'").fetchall()}


def _slug_for_email(cx, email):
    row = cx.execute(
        "SELECT slug FROM affiliate_signups WHERE LOWER(email)=? AND status='approved'",
        ((email or "").strip().lower(),)).fetchone()
    return row[0] if row else None


def _table_exists(cx, name):
    try:
        return cx.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (name,)).fetchone() is not None
    except Exception:
        # Postgres: information_schema instead of sqlite_master
        try:
            return cx.execute(
                "SELECT 1 FROM information_schema.tables WHERE table_name=?",
                (name,)).fetchone() is not None
        except Exception:
            return False


def candidates(cx):
    """Every recoverable (email, slug, when, source) an approved affiliate could claim.

    Returns them unfiltered and unsorted; `plan` applies first touch and exclusions, so
    that this stays inspectable on its own.
    """
    approved = _approved_slugs(cx)
    out = []

    if _table_exists(cx, "inquiries"):
        for email, slug, when in cx.execute(
            "SELECT LOWER(client_email), ref_slug, created_at FROM inquiries "
            "WHERE COALESCE(ref_slug,'')<>'' AND COALESCE(client_email,'')<>''"
        ).fetchall():
            if slug in approved:
                out.append((email, slug, when, "inquiry"))

    if _table_exists(cx, "referral_redemptions"):
        for referee, owner, when in cx.execute(
            "SELECT LOWER(referee_email), LOWER(owner_email), created_at "
            "FROM referral_redemptions WHERE COALESCE(referee_email,'')<>''"
        ).fetchall():
            slug = _slug_for_email(cx, owner)
            if slug:
                out.append((referee, slug, when, "referral-code"))

    if _table_exists(cx, "affiliate_conversions"):
        for email, slug, when in cx.execute(
            "SELECT LOWER(email), affiliate_slug, received_at FROM affiliate_conversions "
            "WHERE COALESCE(affiliate_slug,'')<>'' AND COALESCE(email,'')<>''"
        ).fetchall():
            if slug in approved:
                out.append((email, slug, when, "conversion"))
    return out


def plan(cx):
    """What a backfill would write, and what it would skip and why.

    Never writes. `run` applies exactly this plan, so a dry run is an honest preview
    rather than a separate code path that might drift from the real one.
    """
    already = {}
    if _table_exists(cx, "referral_events"):
        for email, src in cx.execute(
            "SELECT LOWER(email), utm_source FROM referral_events "
            "WHERE COALESCE(utm_source,'')<>'' AND COALESCE(email,'')<>''"
        ).fetchall():
            already.setdefault(email, src)

    rows = candidates(cx)
    # First touch across the whole run: earliest evidence wins for each person.
    rows.sort(key=lambda r: (r[0], str(r[2] or "")))

    write, skip_existing, skip_dupe = [], [], []
    claimed = {}
    for email, slug, when, source in rows:
        if email in already:
            skip_existing.append((email, slug, already[email], source))
            continue
        if email in claimed:
            skip_dupe.append((email, slug, claimed[email], source))
            continue
        claimed[email] = slug
        write.append((email, slug, when, source))

    by_source = {}
    for _e, _s, _w, src in write:
        by_source[src] = by_source.get(src, 0) + 1
    return {
        "would_write": write,
        "skipped_already_attributed": skip_existing,
        "skipped_later_evidence": skip_dupe,
        "counts": {
            "candidates": len(rows),
            "would_write": len(write),
            "already_attributed": len(skip_existing),
            "lost_to_first_touch": len(skip_dupe),
            "by_source": by_source,
            "distinct_affiliates_credited": len({s for _e, s, _w, _x in write}),
        },
    }


def run(db_path, *, dry_run=True):
    """Apply the plan. dry_run=True (the default) writes nothing."""
    with db.connect(db_path) as cx:
        p = plan(cx)
        if dry_run:
            p["counts"]["written"] = 0
            return p
        import datetime
        written = 0
        for email, slug, when, source in p["would_write"]:
            ts = str(when or datetime.datetime.now(datetime.timezone.utc).isoformat())
            cx.execute(
                "INSERT INTO referral_events (received_at, lead_id, email, first_name, "
                "last_name, utm_source, utm_medium, utm_campaign, utm_content, utm_term, "
                "quiz_score, raw_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (ts, None, email, "", "", slug, _MEDIUM, source, "", "", "", ""))
            written += 1
        cx.commit()
        p["counts"]["written"] = written
        return p
