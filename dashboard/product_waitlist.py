"""Free waiting lists for a product that is not on sale yet.

First list, 2026-09-28: Retina Renew, on the Neuro-Magnesium presale page (production's brief,
production/05 Formulations/retina-renew-neuro-magnesium/2026-09-28/waitlist-brief.md). Glen:
"yes in GHL for now, but make sure we are mirroring that in house". So a sign-up is recorded
here first, with the consent wording it was given, tagged on the person's own record, and only
then mirrored to GoHighLevel through ghl_write_queue. The in-house copy is the source of truth.

The consent covers the launch email only: a sign-up never adds consent:opted-in, which drives
recurring mail. Anyone who reserves the founding batch is left out at send time; their tag
stays, as history.
"""
import json
from datetime import datetime, timezone

from dashboard import ghl_queue as _gq
from dashboard import people as _pe

# One list per product slug, with the tag it carries in house and in GoHighLevel.
LISTS = {"neuro-magnesium": "retina-renew-waitlist"}
# Glen's approved wording, 2026-09-29, stored with every sign-up.
CONSENT_TEXT = "We will email you when Retina Renew launches, and nothing else unless you ask."


def _now():
    return datetime.now(timezone.utc).isoformat()


def _norm(email):
    return (email or "").strip().lower()


def init_table(cx):
    cx.execute("CREATE TABLE IF NOT EXISTS product_waitlist ("
               "product_slug TEXT NOT NULL, email TEXT NOT NULL, first_name TEXT DEFAULT '', "
               "consent_text TEXT NOT NULL, created_at TEXT NOT NULL, emailed_at TEXT DEFAULT '', "
               "PRIMARY KEY (product_slug, email))")
    cx.commit()


def sign_up(cx, slug, email, *, first_name=""):
    """Record one sign-up: 'new', or 'existing' when this email is already on the list.
    A new sign-up tags the person in house (creating a minimal person when there is none)
    and queues ONE GoHighLevel tag_add. Caller holds any lock; this commits."""
    tag = LISTS.get(slug)
    if not tag:
        raise ValueError("no waiting list for this product")
    e = _norm(email)
    if not e:
        raise ValueError("email required")
    init_table(cx)
    if cx.execute("SELECT 1 FROM product_waitlist WHERE product_slug=? AND email=?",
                  (slug, e)).fetchone():
        return "existing"
    cx.execute("INSERT INTO product_waitlist (product_slug, email, first_name, consent_text, "
               "created_at) VALUES (?,?,?,?,?)",
               (slug, e, (first_name or "").strip()[:80], CONSENT_TEXT, _now()))
    row = cx.execute("SELECT id, tags FROM people WHERE lower(email)=?", (e,)).fetchone()
    if row is None:
        cx.execute("INSERT INTO people (email, name, source, tags, created_at, updated_at) "
                   "VALUES (?,?,?,?,?,?)",
                   (e, (first_name or "").strip()[:80], "waitlist:" + slug, json.dumps([tag]),
                    _now(), _now()))
    else:
        tags = _pe.set_person_tags(json.loads(row[1] or "[]"), add=[tag])
        cx.execute("UPDATE people SET tags=? WHERE id=?", (json.dumps(tags), row[0]))
    _gq.init_ghl_queue_table(cx)
    _gq.enqueue(cx, op="tag_add", email=e, payload={"tags": [tag]}, actor="waitlist:" + slug)
    cx.commit()
    return "new"


def _reserved(cx, slug):
    try:
        return {_norm(r[0]) for r in cx.execute(
            "SELECT email FROM subscriptions WHERE founding=1 AND founding_slug=? "
            "AND status!='cancelled'", (slug,)).fetchall()}
    except Exception:  # noqa: BLE001 - no reservations table on a fresh database
        try:
            cx.rollback()
        except Exception:  # noqa: BLE001
            pass
        return set()


def waiters_to_email(cx, slug):
    """Who gets the launch email: not yet emailed, and not holding a founding reservation."""
    init_table(cx)
    reserved = _reserved(cx, slug)
    rows = cx.execute("SELECT email, first_name, created_at FROM product_waitlist "
                      "WHERE product_slug=? AND COALESCE(emailed_at,'')='' ORDER BY created_at",
                      (slug,)).fetchall()
    return [{"email": r[0], "first_name": r[1], "created_at": r[2]}
            for r in rows if _norm(r[0]) not in reserved]


def mark_emailed(cx, slug, email):
    cx.execute("UPDATE product_waitlist SET emailed_at=? WHERE product_slug=? AND email=?",
               (_now(), slug, _norm(email)))
    cx.commit()


def counts(cx, slug):
    """In-house sign-ups beside the in-house tag and the GoHighLevel queue, so drift shows."""
    init_table(cx)
    tag = LISTS.get(slug, "")
    signed = cx.execute("SELECT COUNT(*) FROM product_waitlist WHERE product_slug=?", (slug,)).fetchone()[0]
    emailed = cx.execute("SELECT COUNT(*) FROM product_waitlist WHERE product_slug=? "
                         "AND COALESCE(emailed_at,'')<>''", (slug,)).fetchone()[0]
    tagged = sum(1 for (t,) in cx.execute("SELECT tags FROM people WHERE tags LIKE ?",
                                           (f'%"{tag}"%',)).fetchall()
                 if tag in json.loads(t or "[]"))
    queued = sum(1 for (p,) in cx.execute("SELECT payload_json FROM ghl_write_queue "
                                           "WHERE op='tag_add' AND actor=?",
                                           ("waitlist:" + slug,)).fetchall()
                 if tag in (json.loads(p or "{}").get("tags") or []))
    return {"signed_up": signed, "tagged_in_house": tagged, "ghl_queued": queued, "emailed": emailed}
