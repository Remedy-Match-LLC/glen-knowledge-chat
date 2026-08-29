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

RENDERER CONTRACT — read before displaying anything this module returns:

1. `how_i_work` is stored WITH its line structure intact (see
   `sanitize_how_i_work`). It is the one field here that is multi-line by
   design, so any renderer must emit it inside `white-space: pre-line` — or
   split it on blank lines and emit one `<p>` per paragraph. Dropping it into
   ordinary flowed HTML silently collapses the practitioner's paragraphs and
   bullets back into one wall of text, which is exactly the damage the
   sanitizer was rewritten to stop.
2. Every sanitizer here STRIPS markup, it does not ESCAPE it. A stripped
   value is not pre-escaped output: the renderer is still responsible for
   HTML-escaping (or using `textContent`) at the point of display.
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

# Characters that are never legal raw inside a URL (RFC 3986) and that break
# out of an HTML attribute or a tag when the URL is server-rendered.
_URL_FORBIDDEN_CHARS = '"\'<>` '

PROFILE_PUBLIC_FIELDS = frozenset({
    "bio", "photo_url", "logo_url", "services", "location", "accepting_clients",
    "tagline", "how_i_work",
})


_BLANK_RUN_RE = re.compile(r"\n{3,}")


def _norm(s):
    """Strip HTML tags and collapse whitespace onto ONE line.

    Note what this does to `\\n`: `str.split()` with no argument splits on
    every whitespace character, newlines included, so joining with " " flattens
    line structure. That is correct for a one-line field (tagline) and harmless
    for a 600-char bio, but it DESTROYS the paragraphs of a long prose field.
    Multi-line fields use `_norm_multiline` instead.
    """
    return " ".join(_TAG_RE.sub("", s or "").split()).strip()


def _norm_multiline(s):
    """Strip HTML tags and collapse whitespace WITHIN each line, keeping the
    line and paragraph structure the practitioner typed.

    Rules, in order: normalise CRLF/CR to LF; collapse runs of spaces/tabs
    inside a line to a single space and trim each line's own leading and
    trailing space; collapse three-or-more consecutive newlines to exactly two
    (one blank line = one paragraph break, and no more); trim the whole result.

    A bullet list and a blank-line paragraph break both survive this, which is
    the entire point — see the RENDERER CONTRACT in the module docstring.
    """
    text = _TAG_RE.sub("", s or "")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [" ".join(line.split()) for line in text.split("\n")]
    return _BLANK_RUN_RE.sub("\n\n", "\n".join(lines)).strip()


def _as_text(value, field):
    """Coerce None to "" and refuse a non-string with ValueError.

    Without this a JSON body carrying `{"tagline": 123}` raises TypeError out
    of `_norm` (or AttributeError out of `sanitize_image_url`), neither of
    which the settings route's `except ValueError` catches — so a bad TYPE
    became a 500 while a bad VALUE was a 400. Every bad input is a 400.
    """
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError(f"{field} must be text")
    return value


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
    the settings route's existing 400 handler catches it.

    One line by definition, so `_norm`'s flattening is the wanted behaviour
    here — do NOT switch this to `_norm_multiline`."""
    clean = _norm(_as_text(text, "tagline"))
    if len(clean) > MAX_TAGLINE:
        raise ValueError(f"tagline exceeds {MAX_TAGLINE} characters")
    return clean


def sanitize_how_i_work(text):
    """The longer 'how I work' prose. Bigger cap than the bio, and — unlike
    every other field here — LINE STRUCTURE IS PRESERVED.

    This is the one multi-line field. It uses `_norm_multiline`, not `_norm`:
    at 600 characters a bio is one paragraph so flattening never showed, but a
    2000-character "explain your practice" field is written with paragraphs and
    bullet lists, and flattening happens at STORE time, so no later renderer
    could ever recover the structure. Blank-line paragraph breaks and one-item-
    per-line lists survive; runs of three or more newlines collapse to two.

    Do not "simplify" this back to `_norm` to match sanitize_bio.

    Like sanitize_bio this does NOT strip URLs, emails or phone numbers — a
    practitioner may legitimately include their own, and over-stripping prose
    is a known failure mode.

    Whatever renders this must use `white-space: pre-line` or split on blank
    lines into paragraphs; see the RENDERER CONTRACT in the module docstring.
    """
    clean = _norm_multiline(_as_text(text, "how_i_work"))
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

    Quote, angle-bracket, backtick and raw-space characters are rejected too.
    None of them is legal raw in a URL (RFC 3986 — they must be percent-encoded
    as %22 %27 %3C %3E %60 %20), and every one of them is an attribute- or
    tag-breaking character in the HTML this value is destined for. There is no
    sink for it today, but section 5 server-renders this string.

    Empty input returns "" — clearing an image is legitimate.

    NOTE it STRIPS/REJECTS, it does not ESCAPE. A value that passes here is
    still untrusted text: escape it at the point of render.

    This validates what a PRACTITIONER submits. It deliberately does not touch
    values already in the column from scraping: those predate this rule and
    rewriting them is not this plan's business.
    """
    u = _as_text(url, "image URL").strip()
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
    # Never legal raw in a URL (RFC 3986 wants them percent-encoded) and each
    # one breaks out of an HTML attribute or a tag when this string is
    # server-rendered. Checked AFTER the tab/newline and backslash rules so
    # those keep their own specific messages.
    if any(c in u for c in _URL_FORBIDDEN_CHARS):
        raise ValueError(
            "image URL must not contain quote, angle-bracket, backtick or "
            "space characters")
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
                "accepting_new_patients, tagline, how_i_work, "
                "profile_self_authored_at "
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
            "tagline": p.get("tagline") or "",
            "how_i_work": p.get("how_i_work") or "",
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

    This holds today only because `save_draft` writes all NINE on every save
    (bio, photo_url, logo_url, services, city, state, accepting_clients,
    tagline, how_i_work). If you change that count, change it here too.
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
