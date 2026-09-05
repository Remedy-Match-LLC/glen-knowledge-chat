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

    # ONE set, holding affiliate SLUGS. An earlier version kept practitioner_ids and
    # slugs separately and added the two lengths, so a person with a profile AND a chat
    # record counted twice and the production total came back inflated. Everything now
    # resolves to the slug, which is the affiliate's identity.
    active_slugs = set()

    # NOTE ON TWO IDENTITY SPACES. practitioner_profile_drafts and practitioner_pricing
    # are keyed by practitioner_id, which resolves through SUPABASE
    # (practitioner_portal.practitioner_email_by_id -> practitioners.email), a different
    # database. They cannot be joined to affiliate_signups here, so they are reported as
    # raw counts and deliberately NOT folded into any_sign_of_setup. Folding them in
    # would mean adding two different kinds of identity together, which is how the first
    # version of this returned an inflated number.

    if _exists(cx, "practitioner_profile_drafts"):
        rows = cx.execute(
            "SELECT DISTINCT practitioner_id FROM practitioner_profile_drafts").fetchall()
        out["started_a_profile"] = len(rows)   # practitioner-keyed, not folded in
        out["profile_has_photo"] = _count(
            cx, "SELECT COUNT(*) FROM practitioner_profile_drafts "
                "WHERE fields LIKE '%photo_url%'")
    else:
        out["started_a_profile"] = "table absent"

    if _exists(cx, "practitioner_pricing"):
        rows = cx.execute(
            "SELECT DISTINCT practitioner_id FROM practitioner_pricing").fetchall()
        out["set_their_pricing"] = len(rows)   # practitioner-keyed, not folded in
    else:
        out["set_their_pricing"] = "table absent"

    if _exists(cx, "affiliate_social_links"):
        rows = cx.execute("SELECT DISTINCT slug FROM affiliate_social_links").fetchall()
        active_slugs |= {str(r[0]) for r in rows if r and r[0]}
        out["added_a_social_link"] = len(rows)
    else:
        out["added_a_social_link"] = "table absent"

    # Their own intake form. Keyed by email, so it joins straight to the signup. A
    # 'draft' counts: Glen's ruling is ANY data entered, and starting the form is
    # already more than a bot does. Counted separately from submitted, because a
    # half-filled form and a finished one mean different things about the person.
    if _exists(cx, "intake_responses"):
        out["started_their_intake"] = _count(
            cx, "SELECT COUNT(*) FROM intake_responses r "
                "JOIN affiliate_signups a ON LOWER(a.email)=LOWER(r.email) "
                "WHERE a.status='approved' AND COALESCE(r.answers_json,'') NOT IN ('','{}')")
        out["submitted_their_intake"] = _count(
            cx, "SELECT COUNT(*) FROM intake_responses r "
                "JOIN affiliate_signups a ON LOWER(a.email)=LOWER(r.email) "
                "WHERE a.status='approved' AND r.status='submitted'")
        rows = cx.execute(
            "SELECT DISTINCT LOWER(a.slug) FROM intake_responses r "
            "JOIN affiliate_signups a ON LOWER(a.email)=LOWER(r.email) "
            "WHERE a.status='approved' AND COALESCE(r.answers_json,'') NOT IN ('','{}')"
        ).fetchall()
        active_slugs |= {str(r[0]) for r in rows if r and r[0]}
    else:
        out["started_their_intake"] = "table absent"
        out["submitted_their_intake"] = "table absent"

    if _exists(cx, "affiliate_earnings"):
        out["has_ever_earned"] = _count(
            cx, "SELECT COUNT(DISTINCT email) FROM affiliate_earnings")
    else:
        out["has_ever_earned"] = "table absent"

    # Has this affiliate ever used the chat? query_log gained an email column later, so
    # it joins on email like everything else here.
    if _exists(cx, "query_log"):
        out["has_used_the_chat"] = _count(
            cx, "SELECT COUNT(DISTINCT LOWER(q.email)) FROM query_log q "
                "JOIN affiliate_signups a ON LOWER(a.email)=LOWER(q.email) "
                "WHERE a.status='approved' AND COALESCE(q.email,'')<>''")
        rows = cx.execute(
            "SELECT DISTINCT LOWER(a.slug) FROM query_log q "
            "JOIN affiliate_signups a ON LOWER(a.email)=LOWER(q.email) "
            "WHERE a.status='approved' AND COALESCE(q.email,'')<>''").fetchall()
        active_slugs |= {str(r[0]) for r in rows if r and r[0]}
    else:
        out["has_used_the_chat"] = "table absent"

    # Do they have an E4L account? The strongest signal of all: it means they went
    # through the scan funnel themselves, not merely filled in a form.
    if _exists(cx, "e4l_accounts"):
        out["has_an_e4l_account"] = _count(
            cx, "SELECT COUNT(DISTINCT LOWER(e.email)) FROM e4l_accounts e "
                "JOIN affiliate_signups a ON LOWER(a.email)=LOWER(e.email) "
                "WHERE a.status='approved'")
        rows = cx.execute(
            "SELECT DISTINCT LOWER(a.slug) FROM e4l_accounts e "
            "JOIN affiliate_signups a ON LOWER(a.email)=LOWER(e.email) "
            "WHERE a.status='approved'").fetchall()
        active_slugs |= {str(r[0]) for r in rows if r and r[0]}
    else:
        out["has_an_e4l_account"] = "table absent"

    out["any_sign_of_setup"] = len(active_slugs)
    out["_note"] = (
        "any_sign_of_setup counts PEOPLE, deduplicated by affiliate slug, across the "
        "email-keyed signals: intake, chat, E4L account, social links. The per-signal "
        "numbers overlap and must not be summed. started_a_profile and "
        "set_their_pricing are keyed by practitioner_id, which resolves through a "
        "different database, so they are reported but NOT included here.")
    return out
