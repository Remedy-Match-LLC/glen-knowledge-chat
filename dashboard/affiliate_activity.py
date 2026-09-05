"""How many affiliates have actually DONE something.

WHY. 307 affiliate signups are on record, every one status='approved' because that is
the column default, and many carry dot-stuffed Gmail addresses of the kind bots
generate. Between all 307 there are 11 attribution records. So "307 affiliates" is
almost certainly not 307 affiliates.

A bot can produce a signup row. It cannot upload a photo, write a bio, set prices, or
add a social link, because each of those needs a session in the portal and, for the
profile fields, a human review afterwards. Counting those actions separates the people
from the noise.

READ ONLY. This counts; it changes nothing and excludes nobody. Narrowing who earns is a
separate decision, and a riskier one: a genuine referrer who never finished their
profile would silently stop being paid, which is the opposite mistake from paying bots
and much harder to notice.
"""
from dashboard import db


def _exists(cx, name):
    """Ask the backend which catalogue to read. On Postgres a failed statement aborts
    the transaction, so probing by try/except poisons the connection (learned the hard
    way, 2026-09-05)."""
    if db.backend_of(cx) == "postgres":
        return cx.execute(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_schema = current_schema() AND table_name = ?",
            (name,)).fetchone() is not None
    return cx.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (name,)).fetchone() is not None


def _count(cx, sql):
    try:
        return cx.execute(sql).fetchone()[0]
    except Exception as e:
        return f"error: {str(e).splitlines()[0][:60]}"


def summary(cx):
    """Counts per signal, plus how many affiliates show ANY sign of real setup."""
    out = {}
    out["approved_signups"] = _count(
        cx, "SELECT COUNT(*) FROM affiliate_signups WHERE status='approved'")

    active_ids, active_slugs = set(), set()

    if _exists(cx, "practitioner_profile_drafts"):
        rows = cx.execute(
            "SELECT DISTINCT practitioner_id FROM practitioner_profile_drafts").fetchall()
        active_ids |= {str(r[0]) for r in rows if r and r[0] is not None}
        out["started_a_profile"] = len(rows)
        out["profile_has_photo"] = _count(
            cx, "SELECT COUNT(*) FROM practitioner_profile_drafts "
                "WHERE fields LIKE '%photo_url%'")
    else:
        out["started_a_profile"] = "table absent"

    if _exists(cx, "practitioner_pricing"):
        rows = cx.execute(
            "SELECT DISTINCT practitioner_id FROM practitioner_pricing").fetchall()
        active_ids |= {str(r[0]) for r in rows if r and r[0] is not None}
        out["set_their_pricing"] = len(rows)
    else:
        out["set_their_pricing"] = "table absent"

    if _exists(cx, "affiliate_social_links"):
        rows = cx.execute("SELECT DISTINCT slug FROM affiliate_social_links").fetchall()
        active_slugs |= {str(r[0]) for r in rows if r and r[0]}
        out["added_a_social_link"] = len(rows)
    else:
        out["added_a_social_link"] = "table absent"

    if _exists(cx, "affiliate_earnings"):
        out["has_ever_earned"] = _count(
            cx, "SELECT COUNT(DISTINCT email) FROM affiliate_earnings")
    else:
        out["has_ever_earned"] = "table absent"

    out["distinct_practitioner_ids_active"] = len(active_ids)
    out["distinct_slugs_active"] = len(active_slugs)
    out["any_sign_of_setup"] = len(active_ids) + len(active_slugs)
    return out
