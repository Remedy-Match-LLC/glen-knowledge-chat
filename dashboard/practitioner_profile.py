"""Practitioner-authored storefront profile: the bridge from a public slug to
the self-authored fields shown on /p/<slug>.

Provenance rule (spec 2026-07-20-practitioner-storefront-editor.md): the
`practitioners` table already holds bio/photo/specialties/city/state, but that
data is SCRAPED. The storefront publishes a field only when the practitioner has
self-authored/confirmed it, tracked by practitioners.profile_self_authored_at.
Scraped rows (timestamp null) return {} and the storefront shows name +
disclosure only.

This module owns the sqlite (affiliate_signups) -> Postgres (practitioners) hop
and the provenance gate, so public_surface.py stays a thin caller. Any failure
in the read path degrades to {} — a public page must never 500 on a profile read.
"""

import re

MAX_BIO = 600
MAX_SERVICES = 12
MAX_SERVICE_LEN = 60
MAX_LOC_LEN = 80

_TAG_RE = re.compile(r"<\s*/?\s*[a-zA-Z][^>]*>")

PROFILE_PUBLIC_FIELDS = frozenset({
    "bio", "photo_url", "logo_url", "services", "location", "accepting_clients",
})


def _norm(s):
    """Strip HTML tags and collapse whitespace."""
    return " ".join(_TAG_RE.sub("", s or "").split()).strip()


def sanitize_bio(text):
    """Strip HTML, collapse whitespace. Raise ValueError if >600 chars after
    cleaning. Does NOT strip URLs/emails/phones — a practitioner may include
    their own contact detail in their own bio, and over-stripping prose is a
    known failure mode."""
    clean = _norm(text)
    if len(clean) > MAX_BIO:
        raise ValueError(f"bio exceeds {MAX_BIO} characters")
    return clean


def clean_services(items):
    """Strip HTML per item, drop empties, cap 12 items x 60 chars."""
    out = []
    for it in (items or []):
        v = _norm(str(it))[:MAX_SERVICE_LEN].strip()
        if v:
            out.append(v)
        if len(out) >= MAX_SERVICES:
            break
    return out


def format_location(city, state):
    city = _norm(city)[:MAX_LOC_LEN]
    state = _norm(state)[:MAX_LOC_LEN]
    if city and state:
        return f"{city}, {state}"
    return city or ""


def profile_for_slug(cx, slug):
    """Self-authored storefront profile for `slug`, or {} if unknown / scraped /
    error. `cx` is the sqlite connection (row_factory=sqlite3.Row); Postgres is
    reached internally. Fails closed: any exception returns {}."""
    try:
        row = cx.execute(
            "SELECT email FROM affiliate_signups WHERE slug=? AND status='approved'",
            (slug,)).fetchone()
        if not row:
            return {}
        email = (row["email"] or "").strip().lower()
        if not email:
            return {}
        from db_supabase import supabase_cursor
        with supabase_cursor() as cur:
            cur.execute(
                "SELECT bio, photo_url, logo_url, specialties, city, state, "
                "accepting_new_patients, profile_self_authored_at "
                "FROM practitioners WHERE lower(email)=lower(%s) "
                "ORDER BY profile_self_authored_at DESC NULLS LAST LIMIT 1", (email,))
            p = cur.fetchone()
        if not p or not p.get("profile_self_authored_at"):
            return {}
        view = {
            "bio": p.get("bio") or "",
            "photo_url": p.get("photo_url") or "",
            "logo_url": p.get("logo_url") or "",
            "services": list(p.get("specialties") or []),
            "location": format_location(p.get("city"), p.get("state")),
            "accepting_clients": bool(p.get("accepting_new_patients")),
        }
        return {k: v for k, v in view.items() if k in PROFILE_PUBLIC_FIELDS}
    except Exception:
        return {}


def _write_live_profile(pid, fields):
    """The ONLY place profile_self_authored_at is ever stamped. Everything
    public flows through this one statement, which is what makes the review
    gate auditable."""
    from db_supabase import supabase_cursor
    with supabase_cursor() as cur:
        cur.execute(
            "UPDATE practitioners SET bio=%s, photo_url=%s, specialties=%s,"
            " city=%s, state=%s, accepting_new_patients=%s,"
            " profile_self_authored_at=now(), updated_at=now() WHERE id=%s",
            (fields.get("bio", ""), fields.get("photo_url", ""),
             fields.get("services", []), fields.get("city", ""),
             fields.get("state", ""), bool(fields.get("accepting_clients", True)),
             str(pid)))


def save_draft(cx, pid, profile):
    """Write the practitioner's proposed profile to their DRAFT.

    Renamed from save_profile in section 2a: this no longer publishes
    anything. Sanitization is unchanged and still runs here, so a too-long
    bio is refused at the point the practitioner typed it rather than at
    review time.
    """
    from dashboard import practitioner_drafts as _pd
    fields = {
        "bio": sanitize_bio(profile.get("bio", "")),
        "services": clean_services(profile.get("services")),
        "city": _norm(profile.get("city"))[:MAX_LOC_LEN],
        "state": _norm(profile.get("state"))[:MAX_LOC_LEN],
        "photo_url": (profile.get("photo_url") or "").strip(),
        "accepting_clients": bool(profile.get("accepting_clients", True)),
    }
    _pd.init_tables(cx)
    _pd.upsert_draft(cx, pid, fields)
    return fields


def publish_draft(cx, pid):
    """Copy an APPROVED draft into the public practitioners row.

    Returns False and writes nothing unless the draft is approved. This is
    the gate: no other code path may stamp profile_self_authored_at.
    """
    from dashboard import practitioner_drafts as _pd
    _pd.init_tables(cx)
    d = _pd.get_draft(cx, pid)
    if not d or d.get("status") != "approved":
        return False
    _write_live_profile(pid, d["fields"])
    return True
