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

It also owns the WRITE side of that hop (section 2a): `save_draft` records the
practitioner's proposed values in the sqlite draft store and publishes nothing,
and `publish_draft` copies an APPROVED draft into the Postgres row through
`_write_live_profile` — the one and only statement that stamps
`profile_self_authored_at`, and therefore the one and only thing in the codebase
that can make a practitioner page public. Read `_write_live_profile`'s docstring
before touching either side.

Dialect split, deliberate: everything draft-side is sqlite and uses `?`;
`_write_live_profile` is Postgres and uses `%s`. Never both in one function.
"""

import re

MAX_BIO = 600
MAX_TAGLINE = 120
MAX_HOW_I_WORK = 2000
MAX_SERVICES = 12
MAX_SERVICE_LEN = 60
MAX_LOC_LEN = 80
MAX_URL = 500

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


def sanitize_tagline(text):
    """One line under the practitioner's name. Strip HTML, collapse whitespace,
    refuse anything over MAX_TAGLINE. Raises ValueError, like sanitize_bio, so
    the settings route's existing 400 handler catches it."""
    clean = _norm(text)
    if len(clean) > MAX_TAGLINE:
        raise ValueError(f"tagline exceeds {MAX_TAGLINE} characters")
    return clean


def sanitize_how_i_work(text):
    """The longer 'how I work' prose. Same rules as sanitize_bio, bigger cap:
    the 600-char bio cannot carry a page that has to explain a practice.

    Like sanitize_bio this does NOT strip URLs, emails or phone numbers — a
    practitioner may legitimately include their own, and over-stripping prose
    is a known failure mode.
    """
    clean = _norm(text)
    if len(clean) > MAX_HOW_I_WORK:
        raise ValueError(f"how_i_work exceeds {MAX_HOW_I_WORK} characters")
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


def sanitize_image_url(url):
    r"""An image URL safe to put on a public page.

    Allows exactly two shapes: an absolute https:// URL, or a site-relative
    path beginning with a single '/'. Everything else raises ValueError —
    `javascript:` and `data:` are script-execution vectors on a page we serve,
    plaintext `http://` is mixed content, and `//host/path` is
    protocol-relative and inherits whichever scheme the page happens to use.
    Backslashes are rejected anywhere in the URL: browsers normalise `\` to `/`
    for special schemes like https, so a backslash lets a site-relative-looking
    path resolve to an arbitrary external host.

    The validator also normalises ASCII tab, CR, and LF the way a browser does
    (per WHATWG URL Standard), stripping them BEFORE parsing scheme/authority/
    path. This prevents "/\t/evil.com/x.png" from silently becoming "//evil.com/x.png"
    in the browser. If normalisation would change the string, reject it — legitimate
    image URLs never need raw tab or newline, only percent-encoded %09/%0D/%0A.

    Empty input returns "" — clearing an image is legitimate.

    This validates what a PRACTITIONER submits. It deliberately does not touch
    values already in the column from scraping: those predate this rule and
    rewriting them is not this plan's business.
    """
    u = (url or "").strip()
    if not u:
        return ""
    if len(u) > MAX_URL:
        raise ValueError(f"image URL exceeds {MAX_URL} characters")
    # A browser strips ASCII tab/CR/LF from a URL BEFORE parsing scheme,
    # authority or path (WHATWG URL Standard). So validating the raw string
    # would judge a different value than the one the browser resolves:
    # "/\t/evil.com/x.png" contains no "//" here, but the browser sees
    # "//evil.com/x.png" and fetches off-site. Reject rather than silently
    # normalise, so what we store is exactly what we validated.
    normalised = "".join(c for c in u if c not in "\t\r\n")
    if u != normalised:
        raise ValueError("image URL must not contain tab or newline characters")
    if "\\" in u:
        raise ValueError("image URL must not contain a backslash")
    if u.startswith("//"):
        raise ValueError("image URL must not be protocol-relative")
    if u.startswith("/"):
        return u
    if u.lower().startswith("https://"):
        return u
    raise ValueError("image URL must be https:// or a site-relative path")


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
    gate auditable.

    INVARIANT — PUBLISH REPLACES THE ROW WHOLESALE. Every column in the SET
    list is written unconditionally, from `fields.get(k, <default>)`. There is
    no partial update and no merge with what is already live, so a draft that
    is MISSING a key does not leave the live value alone: it overwrites it
    with the default (empty string / empty list / True). A draft MUST
    therefore carry EVERY field published here.

    This holds today only because `save_draft` writes all six on every save.
    If you add a column to this statement, add it to `save_draft` in the same
    commit — `test_save_draft_writes_every_field_publish_reads` in
    tests/test_practitioner_profile.py derives the read set from this
    function's own source and will go red if you don't.

    The wholesale write is a deliberate trade, not an oversight: one fixed
    statement is greppable and auditable, a dynamic SET list is not. See the
    REVIEW_POLICY note in dashboard/practitioner_drafts.py.
    """
    from db_supabase import supabase_cursor
    with supabase_cursor() as cur:
        cur.execute(
            "UPDATE practitioners SET bio=%s, photo_url=%s, logo_url=%s,"
            " specialties=%s, city=%s, state=%s, accepting_new_patients=%s,"
            " tagline=%s, how_i_work=%s,"
            " profile_self_authored_at=now(), updated_at=now() WHERE id=%s",
            (fields.get("bio", ""), fields.get("photo_url", ""),
             fields.get("logo_url", ""), fields.get("services", []),
             fields.get("city", ""), fields.get("state", ""),
             bool(fields.get("accepting_clients", True)),
             fields.get("tagline", ""), fields.get("how_i_work", ""),
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
        "photo_url": sanitize_image_url(profile.get("photo_url")),
        "logo_url": sanitize_image_url(profile.get("logo_url")),
        "accepting_clients": bool(profile.get("accepting_clients", True)),
        "tagline": sanitize_tagline(profile.get("tagline", "")),
        "how_i_work": sanitize_how_i_work(profile.get("how_i_work", "")),
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
