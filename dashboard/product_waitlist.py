"""Free waiting lists for a product that is not on sale yet.

First list, 2026-09-28: Retina Renew, on the Neuro-Magnesium presale page (production's brief,
production/05 Formulations/retina-renew-neuro-magnesium/2026-09-28/waitlist-brief.md). Glen:
"yes in GHL for now, but make sure we are mirroring that in house". So a sign-up is recorded
here first, with the consent wording it was given. Once its owner clicks the confirmation link
(Glen, 2026-09-29), it is tagged on the person's own record and only then mirrored to
GoHighLevel through ghl_write_queue. The in-house copy is the source of truth.

The consent covers the launch email only: a sign-up never adds consent:opted-in, which drives
recurring mail. Anyone who reserves the founding batch is left out at send time; their tag
stays, as history.
"""
import hashlib
import json
import secrets
from datetime import datetime, timedelta, timezone

from dashboard import ghl_queue as _gq
from dashboard import people as _pe

# One list per product slug, with the tag it carries in house and in GoHighLevel.
LISTS = {"neuro-magnesium": "retina-renew-waitlist"}
# Glen's approved wording, 2026-09-29, stored with every sign-up.
CONSENT_TEXT = "We will email you when Retina Renew launches, and nothing else unless you ask."
# Glen, 2026-09-29 ("confirm email"): a sign-up counts only once its owner clicks the link.
CONFIRM_DAYS = 30
RESEND_MINUTES = 10


def _now():
    return datetime.now(timezone.utc).isoformat()


def _norm(email):
    return (email or "").strip().lower()


def init_table(cx):
    cx.execute("CREATE TABLE IF NOT EXISTS product_waitlist ("
               "product_slug TEXT NOT NULL, email TEXT NOT NULL, first_name TEXT DEFAULT '', "
               "consent_text TEXT NOT NULL, created_at TEXT NOT NULL, emailed_at TEXT DEFAULT '', "
               "confirm_hash TEXT DEFAULT '', confirm_sent_at TEXT DEFAULT '', "
               "confirmed_at TEXT DEFAULT '', "
               "PRIMARY KEY (product_slug, email))")
    cx.commit()


def _person_email(cx, email):
    """The surviving person's address: a merged-away address tags the survivor (round 1)."""
    try:
        from dashboard import person_aliases as _pa
        return _pa.canonical_email(cx, email) or email
    except Exception:  # noqa: BLE001 - no alias table yet
        try:
            cx.rollback()
        except Exception:  # noqa: BLE001
            pass
        return email


def _listed_row(cx, slug, email):
    return cx.execute("SELECT confirmed_at, confirm_sent_at FROM product_waitlist "
                      "WHERE product_slug=? AND email=?", (slug, email)).fetchone()


def _hash(token):
    return hashlib.sha256((token or "").encode()).hexdigest()


def _ago(ts):
    try:
        return datetime.now(timezone.utc) - datetime.fromisoformat(ts)
    except (TypeError, ValueError):
        return timedelta.max


def _tag_person(cx, email, tag, first_name, slug):
    """Add the tag to the person's own record, creating a minimal person if needed. The write
    applies only if the tags are unchanged since read, so a tag another worker removed
    meanwhile (an opt-out) is never written back (review round 2)."""
    for _ in range(5):
        row = cx.execute("SELECT id, tags FROM people WHERE lower(email)=?", (email,)).fetchone()
        if row is None:
            cx.execute("INSERT INTO people (email, name, source, tags, created_at, updated_at) "
                       "VALUES (?,?,?,?,?,?) ON CONFLICT (email) DO NOTHING",
                       (email, (first_name or "").strip()[:80], "waitlist:" + slug,
                        json.dumps([tag]), _now(), _now()))
            continue
        raw = row[1] or "[]"
        tags = _pe.set_person_tags(json.loads(raw), add=[tag])
        if tag in json.loads(raw):
            return
        cur = cx.execute("UPDATE people SET tags=? WHERE id=? AND COALESCE(tags,'[]')=?",
                         (json.dumps(tags), row[0], raw))
        if getattr(cur, "rowcount", 1):
            return


def sign_up(cx, slug, email, *, first_name=""):
    """Record a sign-up and return (status, token). The token goes in the confirmation email;
    only its hash is stored. Nothing is tagged or mirrored until confirm().

    'new'       a new sign-up; send the email.
    'resend'    signed up before, not confirmed, last email over RESEND_MINUTES ago; send again.
    'throttled' signed up before, not confirmed, emailed recently; send nothing.
    'existing'  already confirmed (or a simultaneous duplicate); send nothing. Commits."""
    if slug not in LISTS:
        raise ValueError("no waiting list for this product")
    e = _norm(email)
    if not e:
        raise ValueError("email required")
    init_table(cx)
    e = _norm(_person_email(cx, e))
    row = _listed_row(cx, slug, e)
    token = secrets.token_urlsafe(24)
    if row is not None:
        if (row[0] or "").strip():
            return ("existing", None)
        if _ago(row[1]) < timedelta(minutes=RESEND_MINUTES):
            return ("throttled", None)
        cx.execute("UPDATE product_waitlist SET confirm_hash=?, confirm_sent_at=? "
                   "WHERE product_slug=? AND email=? AND COALESCE(confirmed_at,'')=''",
                   (_hash(token), _now(), slug, e))
        cx.commit()
        return ("resend", token)
    cur = cx.execute("INSERT INTO product_waitlist (product_slug, email, first_name, consent_text, "
                     "created_at, confirm_hash, confirm_sent_at) VALUES (?,?,?,?,?,?,?) "
                     "ON CONFLICT (product_slug, email) DO NOTHING",
                     (slug, e, (first_name or "").strip()[:80], CONSENT_TEXT, _now(),
                      _hash(token), _now()))
    cx.commit()
    if not getattr(cur, "rowcount", 1):
        return ("existing", None)
    return ("new", token)


def confirm(cx, token):
    """The owner clicked the link: mark the sign-up confirmed, tag the person in house and
    queue ONE GoHighLevel tag_add. Returns the product slug, or None for an unknown, replaced
    or expired link. A second click on a used link changes nothing and still returns the slug."""
    if not token:
        return None
    init_table(cx)
    row = cx.execute("SELECT product_slug, email, first_name, confirm_sent_at, confirmed_at "
                     "FROM product_waitlist WHERE confirm_hash=?", (_hash(token),)).fetchone()
    if row is None:
        return None
    slug, e, first_name, sent_at, confirmed_at = row
    if (confirmed_at or "").strip():
        return slug
    if _ago(sent_at) > timedelta(days=CONFIRM_DAYS):
        return None
    cur = cx.execute("UPDATE product_waitlist SET confirmed_at=? WHERE product_slug=? AND email=? "
                     "AND COALESCE(confirmed_at,'')=''", (_now(), slug, e))
    if not getattr(cur, "rowcount", 1):
        cx.commit()
        return slug                        # another click confirmed it a moment ago
    tag = LISTS[slug]
    _tag_person(cx, e, tag, first_name, slug)
    _gq.init_ghl_queue_table(cx)
    _gq.enqueue(cx, op="tag_add", email=e, payload={"tags": [tag]}, actor="waitlist:" + slug)
    cx.commit()
    return slug


def _reserved(cx, slug):
    """Emails holding a live founding reservation. A missing table means none exist; any
    OTHER failure raises, because failing open would email every reserver (round 2)."""
    try:
        cx.execute("SELECT 1 FROM subscriptions LIMIT 0").fetchall()
    except Exception:  # noqa: BLE001 - no reservations table on a fresh database
        try:
            cx.rollback()
        except Exception:  # noqa: BLE001
            pass
        return set()
    return {_norm(_person_email(cx, _norm(r[0]))) for r in cx.execute(
        "SELECT email FROM subscriptions WHERE founding=1 AND founding_slug=? "
        "AND status!='cancelled'", (slug,)).fetchall()}


def _unmailable(cx):
    """Addresses the launch email must skip: suppressed (bounced) or unsubscribed."""
    out = set()
    try:
        out |= {_norm(r[0]) for r in cx.execute("SELECT email FROM email_suppression").fetchall()}
    except Exception:  # noqa: BLE001 - no suppression table
        try:
            cx.rollback()
        except Exception:  # noqa: BLE001
            pass
    for e, t in cx.execute("SELECT email, tags FROM people WHERE tags LIKE ?",
                           ('%"consent:unsubscribed"%',)).fetchall():
        if "consent:unsubscribed" in json.loads(t or "[]"):
            out.add(_norm(e))
    return out


def waiters_to_email(cx, slug):
    """Who gets the launch email: confirmed, not yet emailed, not holding a founding
    reservation, not suppressed and not unsubscribed."""
    init_table(cx)
    skip = _reserved(cx, slug) | _unmailable(cx)
    rows = cx.execute("SELECT email, first_name, created_at FROM product_waitlist "
                      "WHERE product_slug=? AND COALESCE(emailed_at,'')='' "
                      "AND COALESCE(confirmed_at,'')<>'' ORDER BY created_at",
                      (slug,)).fetchall()
    return [{"email": r[0], "first_name": r[1], "created_at": r[2]}
            for r in rows if _norm(r[0]) not in skip]


def mark_emailed(cx, slug, email):
    cx.execute("UPDATE product_waitlist SET emailed_at=? WHERE product_slug=? AND email=?",
               (_now(), slug, _norm(email)))
    cx.commit()


def counts(cx, slug):
    """In-house sign-ups beside the in-house tag and the GoHighLevel queue, so drift shows."""
    init_table(cx)
    tag = LISTS.get(slug, "")
    signed = cx.execute("SELECT COUNT(*) FROM product_waitlist WHERE product_slug=?", (slug,)).fetchone()[0]
    confirmed = cx.execute("SELECT COUNT(*) FROM product_waitlist WHERE product_slug=? "
                           "AND COALESCE(confirmed_at,'')<>''", (slug,)).fetchone()[0]
    emailed = cx.execute("SELECT COUNT(*) FROM product_waitlist WHERE product_slug=? "
                         "AND COALESCE(emailed_at,'')<>''", (slug,)).fetchone()[0]
    tagged = sum(1 for (t,) in cx.execute("SELECT tags FROM people WHERE tags LIKE ?",
                                           (f'%"{tag}"%',)).fetchall()
                 if tag in json.loads(t or "[]"))
    queued = sum(1 for (p,) in cx.execute("SELECT payload_json FROM ghl_write_queue "
                                           "WHERE op='tag_add' AND actor=?",
                                           ("waitlist:" + slug,)).fetchall()
                 if tag in (json.loads(p or "{}").get("tags") or []))
    return {"signed_up": signed, "confirmed": confirmed, "tagged_in_house": tagged,
            "ghl_queued": queued, "emailed": emailed}
