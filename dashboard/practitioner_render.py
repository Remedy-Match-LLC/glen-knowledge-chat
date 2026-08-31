"""Server-rendered practitioner page.

Pure functions: payload dict in, HTML string out. No database access, no
Flask, no environment reads -- every input arrives as an argument. That is
what lets the whole surface be tested without a request context, and it is
why the canonical URL is passed in rather than derived here.

Why server-rendered at all: link-preview bots for iMessage, WhatsApp,
Facebook and Slack do not execute JavaScript. The JS storefront rendered a
blank preview card when a client texted their practitioner's link, which is
the referral motion this whole feature exists to serve.

This module's own output is plain and self-contained -- the inline <style>
below is everything render_page_html emits. But that is not the same as
what a browser ends up showing: app.py's global after_request hook,
_inject_journey_shell (app.py ~52755), rewrites any text/html 200 outside
/console/, /admin/, /api/ and /static/ before it reaches the client, and
/<slug> matches that. The page is not the standalone document it looks like
in isolation.

What actually gets injected is NOT a stylesheet fighting this module's
element rules -- static/shell.css scopes everything under #journey-shell /
.js-* / .rm-theme-seg*, by its own header comment's design ("never clobber
page CSS"), and its only page-wide declarations are `:root{--jshell-h:52px}`
and `body.js-shell-on{padding-top:var(--jshell-h)}`. So the real
cross-cutting effect, when JOURNEY_SHELL_ENABLED=1 (the production
setting), is a 52px fixed ribbon plus that top-padding rule -- not this
module's `body`/`h1`/`p`/`section` rules being overridden.

Separately, and unconditionally -- independent of JOURNEY_SHELL_ENABLED --
shell_nav.inject_theme_html injects sun-engine.js and theme-mode.js on this
path, and theme-mode.js sets `data-theme` on <html>. That is a second, live
theming mechanism worth naming next to the `prefers-color-scheme` dark-mode
block below: the block here follows the OS/browser's preference, while
theme-mode.js's `data-theme` attribute is a separate, explicit toggle this
page does not drive or read.
"""
import html
import json

# Plain by design, and self-contained as far as THIS module's output goes --
# see the module docstring above for why "self-contained" stops being true
# once the response leaves this function.
_STYLE = (
    "<style>"
    ":root{--ink:#1F5A4D;--line:#e6e6e6;--muted:#666}"
    "body{font:16px/1.6 system-ui,-apple-system,Segoe UI,sans-serif;"
    "color:#222;margin:0}"
    ".wrap{max-width:720px;margin:0 auto;padding:32px 16px 64px}"
    "h1{color:var(--ink);font-size:30px;margin:0 0 4px}"
    ".tagline{font-size:19px;color:var(--muted);margin:0 0 20px}"
    ".practice{font-size:17px;margin:0 0 4px}"
    ".loc{color:var(--muted);margin:0 0 20px}"
    ".photo{width:160px;height:160px;border-radius:50%;object-fit:cover;"
    "display:block;margin:0 0 20px}"
    "section{border-top:1px solid var(--line);padding-top:20px;margin-top:24px}"
    "h2{font-size:18px;color:var(--ink);margin:0 0 8px}"
    "p{margin:0 0 12px}"
    ".disclosure{color:var(--muted);font-size:14px;margin-top:32px}"
    ".logo{max-width:200px;max-height:80px;display:block;margin:0 0 20px}"
    ".accepting{font-weight:600}"
    ".price{color:var(--muted)}"
    ".book-btn{display:inline-block;background:var(--ink);color:#fff;"
    "padding:10px 20px;border-radius:6px;text-decoration:none;font-weight:600}"
    # Dark mode: the deleted JS shell (static/practitioner-storefront.html)
    # carried this. Without it the practitioner page stayed white while the
    # rest of the portal followed the system theme -- restored here, mapped
    # onto this page's structure (.card -> section, the muted-text classes
    # widened to match every element that already uses var(--muted) above).
    "@media (prefers-color-scheme: dark){"
    "body{background:#121212;color:#eee}"
    "section{border-color:#333}"
    ".tagline,.practice,.loc,.disclosure,.price{color:#aaa}"
    "}"
    "</style>"
)


def _esc(s):
    """Escape for both text nodes and attribute values.

    quote=True is not optional here: a raw double quote in a tagline would
    otherwise terminate a meta content="..." attribute and let the rest of
    the value be parsed as markup.
    """
    return html.escape(str(s or ""), quote=True)


MAX_DESCRIPTION = 200


def _display_name(view):
    """The practitioner's name, falling back to their slug, then a generic
    label. One resolution order shared by the title, the document body, and
    the JSON-LD -- so they cannot drift from each other."""
    return view.get("practitioner_name") or view.get("slug") or "Practitioner"


def build_title(view):
    """Name, or "Name — Practice" when a practice name exists.

    An em dash separator, not a pipe: this is a person's page, not a
    directory listing.
    """
    name = _display_name(view)
    practice = str(view.get("practice_name") or "").strip()
    return f"{name} — {practice}" if practice else str(name)


def _truncate(text, limit=MAX_DESCRIPTION):
    """Cut on a word boundary and mark the cut.

    A preview card cut mid-word reads as broken; the ellipsis is what tells a
    reader the sentence continues on the page.
    """
    text = " ".join(str(text or "").split())
    if len(text) <= limit:
        return text
    cut = text[:limit - 1].rstrip()
    if " " in cut:
        cut = cut[:cut.rfind(" ")].rstrip()
    return cut + "…"


def build_description(view):
    """Tagline, else bio, else a neutral line naming the practitioner.

    Never empty: an absent description makes the preview card fall back to
    showing the bare URL, which is the blank-card problem this plan exists to
    fix, only quieter.
    """
    name = view.get("practitioner_name") or view.get("slug") or "This practitioner"
    for key in ("tagline", "bio"):
        val = str(view.get(key) or "").strip()
        if val:
            return _truncate(val)
    return f"{name} on Remedy Match."


def _paragraphs(text):
    """Blank-line-separated paragraphs, preserved.

    save_draft deliberately keeps the blank lines in bio and how_i_work.
    Flattening them here would undo that at the last possible moment.
    """
    out = []
    for para in str(text or "").split("\n\n"):
        para = para.strip()
        if para:
            out.append(f"<p>{_esc(para)}</p>")
    return "".join(out)


def _section(heading, text):
    """A labelled block, or nothing at all when the field is empty."""
    body = _paragraphs(text)
    return f"<section><h2>{_esc(heading)}</h2>{body}</section>" if body else ""


def _photo(view):
    url = str(view.get("photo_url") or "").strip()
    if not url:
        return ""
    alt = _esc(view.get("practitioner_name") or "Practitioner")
    return f'<img class="photo" src="{_esc(url)}" alt="{alt}">'


def _line(css_class, text):
    text = str(text or "").strip()
    return f'<p class="{css_class}">{_esc(text)}</p>' if text else ""


def _logo(view):
    url = str(view.get("logo_url") or "").strip()
    if not url:
        return ""
    practice = view.get("practice_name") or view.get("practitioner_name") or ""
    return f'<img class="logo" src="{_esc(url)}" alt="{_esc(practice)}">'


def _services(view):
    items = [str(s).strip() for s in (view.get("services") or []) if str(s).strip()]
    if not items:
        return ""
    lis = "".join(f"<li>{_esc(s)}</li>" for s in items)
    return f"<section><h2>Services</h2><ul>{lis}</ul></section>"


def _accepting(view):
    """Render the answer either way -- except when there is no answer.

    `accepting_clients` is True or False only when a practitioner (or their
    self-authored profile) actually said so. build_practitioner_storefront
    defaults it to None for every practitioner who has never authored a
    profile, which today is most of the roster. None is not a third value
    someone chose; it is the absence of a claim, and rendering either string
    for it would publish an availability statement on that person's behalf
    that they never made -- exactly the defect this function used to cause.

    So: None renders nothing. True and False still render their line each --
    False is information a visitor needs before they compose an email, and
    for a profile that genuinely says so, showing nothing there would read as
    "unknown", which is the one thing it is not. That reasoning applies only
    once the value is known; it does not license treating "unknown" (None)
    as if it meant "yes".
    """
    value = view.get("accepting_clients")
    if value is None:
        return ""
    return ('<p class="accepting">Accepting new clients</p>'
            if value
            else '<p class="accepting">Not currently accepting new clients</p>')


def _book_block(view, bookable):
    """The Book link -- only when the practitioner has actually turned
    booking on. `bookable` is resolved by app.py from
    practitioner_booking.is_bookable and arrives here as a plain argument;
    this module still reads no database. Most practitioners have never
    configured booking, and an empty booking page reached from a hopeful
    button is a worse first impression than no button at all -- so this
    renders nothing rather than a disabled or explanatory state."""
    if not bookable:
        return ""
    slug = _esc(view.get("slug") or "")
    return (f'<section class="book"><h2>Book a session</h2>'
            f'<p><a class="book-btn" href="/book/{slug}">'
            f"Book a time</a></p></section>")


def _featured(view):
    """Retail prices only -- the payload whitelist guarantees that upstream.

    Always empty today: build_practitioner_storefront hardcodes [] and no
    profile supplies it. Rendered anyway so the field is not one more thing
    that reaches the payload and stops there.
    """
    items = view.get("featured_products") or []
    lis = []
    for p in items:
        if isinstance(p, dict):
            name = str(p.get("name") or "").strip()
            price = str(p.get("price") or "").strip()
        else:
            name, price = str(p).strip(), ""
        if name:
            lis.append(f"<li>{_esc(name)}"
                       + (f" <span class=\"price\">{_esc(price)}</span>" if price else "")
                       + "</li>")
    if not lis:
        return ""
    return f"<section><h2>Featured</h2><ul>{''.join(lis)}</ul></section>"


SITE_NAME = "Remedy Match"


def _absolute_photo_url(photo, canonical_url):
    """Turn a site-relative photo path into an absolute URL for og:image and
    twitter:image, which -- unlike the visible <img src> in the body -- must
    be absolute for Facebook, iMessage and Slack to render a card image at
    all. dashboard/practitioner_profile.py's sanitize_image_url deliberately
    permits a site-relative "/path" as one of its two legal shapes (correct
    for the body <img>, which resolves fine against the page's own host in a
    browser); passing that same relative value straight into a meta tag
    silently produces an imageless card, the exact failure this feature
    exists to fix, arriving through a legal input instead of a bug.

    canonical_url is already the fully-qualified PORTAL_BASE_URL + "/" + slug
    URL built by app.py's _render_practitioner_page -- its scheme+host is the
    only base this page has. When canonical_url is falsy (PORTAL_BASE_URL
    unset), there is no base to resolve a relative path against, so this
    returns "" and the caller omits the image tags entirely -- same contract
    as the canonical/og:url tags in that situation.
    """
    if not photo:
        return ""
    if not photo.startswith("/"):
        return photo  # already absolute -- sanitize_image_url's other legal shape
    if not canonical_url:
        return ""
    from urllib.parse import urlsplit
    parts = urlsplit(canonical_url)
    if not parts.scheme or not parts.netloc:
        return ""
    return f"{parts.scheme}://{parts.netloc}{photo}"


def _share_tags(view, title, desc, canonical_url):
    """Open Graph and Twitter Card tags.

    og:type is "profile" rather than "website" because this page is a person.
    The Twitter card type follows whether an ABSOLUTE image is available, not
    merely whether photo_url is set -- summary_large_image with no image tag
    renders as an empty grey box, which looks more broken than the small
    card, and a relative photo_url with no canonical base to absolutize
    against (see _absolute_photo_url) is exactly that case.

    og:url is omitted entirely when canonical_url is falsy, same contract and
    same reason as the <link rel="canonical"> tag in render_page_html: a
    relative or empty URL here is not a safe default, it is a wrong one, and
    omitting the tag is the neutral choice.
    """
    photo = str(view.get("photo_url") or "").strip()
    image = _absolute_photo_url(photo, canonical_url)
    tags = [
        '<meta property="og:type" content="profile">',
        f'<meta property="og:title" content="{_esc(title)}">',
        f'<meta property="og:description" content="{_esc(desc)}">',
    ]
    if canonical_url:
        tags.append(f'<meta property="og:url" content="{_esc(canonical_url)}">')
    tags.append(f'<meta property="og:site_name" content="{_esc(SITE_NAME)}">')
    tags.append(f'<meta name="twitter:card" content='
                f'"{"summary_large_image" if image else "summary"}">')
    tags.append(f'<meta name="twitter:title" content="{_esc(title)}">')
    tags.append(f'<meta name="twitter:description" content="{_esc(desc)}">')
    if image:
        tags.append(f'<meta property="og:image" content="{_esc(image)}">')
        tags.append(f'<meta name="twitter:image" content="{_esc(image)}">')
    return "".join(tags)


def build_jsonld(view, canonical_url):
    """Person plus ProfessionalService.

    Deliberately NOT MedicalBusiness or Physician. Those schema types assert a
    medical practice to Google in machine-readable form. Most practitioners
    here are health coaches, for whom that is inaccurate, and it is not a
    claim to emit from this domain on their behalf. Same reasoning as the
    credential-verification decision in section 1 of the spec.

    Fields are omitted when absent rather than emitted empty: an empty
    schema.org value is a worse signal than a missing one. `url` follows the
    same rule when canonical_url is falsy (PORTAL_BASE_URL unset) -- a wrong
    or relative URL asserted to Google is worse than the field being absent.
    """
    name = _display_name(view)
    person = {"@context": "https://schema.org", "@type": "Person",
              "name": name, "description": build_description(view)}
    if canonical_url:
        person["url"] = canonical_url
    if str(view.get("photo_url") or "").strip():
        person["image"] = str(view["photo_url"]).strip()

    service = {"@context": "https://schema.org", "@type": "ProfessionalService",
               "name": str(view.get("practice_name") or "").strip() or name}
    if canonical_url:
        service["url"] = canonical_url
    if str(view.get("location") or "").strip():
        service["address"] = str(view["location"]).strip()
    services = [str(s).strip() for s in (view.get("services") or []) if str(s).strip()]
    if services:
        service["serviceType"] = services
    return [person, service]


def _jsonld_tag(view, canonical_url):
    """Serialise the JSON-LD, escaping the whole class of characters that can
    end a <script> element early -- not just one spelling of the attack.

    Escaping only a literal `</script` (an earlier version of this function)
    misses `<!--<script>`, which contains no `</` at all. That sequence still
    breaks a real browser: the HTML tokenizer parsing <script> content reads
    `<!--` followed by `<script` as the start of "script data double escaped
    state", after which the block's own trailing `</script>` no longer closes
    the element -- everything that follows (the rest of <head>, all of
    <body>) is consumed as inert script text, and the visitor sees a blank
    page. There is no bounded list of trigger spellings for this, so the fix
    is to remove the character the tokenizer keys off at all: every `<` and
    `>` in the payload is escaped to a JSON unicode escape, which the
    tokenizer cannot recognise as markup. `&` is escaped too (some HTML
    entity contexts) along with the two line-terminator code points --
    U+2028/U+2029 -- that are legal JSON but illegal inside a JavaScript
    string literal, in case this block is ever read with a JS `eval`/regex
    instead of `JSON.parse`. This is the same complete technique Django's
    `json_script` filter uses. Every substitution stays inside a JSON string
    escape, so the block still parses with `json.loads` and every value reads
    back unchanged.
    """
    raw = json.dumps(build_jsonld(view, canonical_url), ensure_ascii=False)
    raw = (raw.replace("&", "\\u0026")
              .replace("<", "\\u003c")
              .replace(">", "\\u003e")
              .replace(" ", "\\u2028")
              .replace(" ", "\\u2029"))
    return '<script type="application/ld+json">' + raw + "</script>"


def render_page_html(view, *, canonical_url, bookable=False):
    """Render the complete document for one practitioner.

    `view` is the payload from public_surface.build_practitioner_storefront.
    `canonical_url`, when truthy, must be fully qualified and built by the
    caller from PORTAL_BASE_URL -- never from PUBLIC_BASE_URL, which is the
    funnel. A relative URL here is not a safe fallback: a browser resolves a
    relative <link rel="canonical"> against the CURRENT page's own host, so a
    bare "/<slug>" served from the funnel host would resolve to a funnel-host
    canonical, exactly the duplicate this tag exists to collapse.

    `canonical_url` may also be falsy (None or ""), for the one case where the
    caller has no correct absolute URL to give -- PORTAL_BASE_URL unset. In
    that case both <link rel="canonical"> and og:url are omitted entirely,
    and build_jsonld omits `url` from both entities. A missing canonical is
    neutral (Google picks its own, as on any page that never had one); a
    wrong one actively asserts the funnel URL is authoritative for this
    practitioner. Every other tag -- title, description, og:title/description
    /image, twitter tags, JSON-LD name/description, noindex, the body --
    still renders normally regardless of canonical_url.

    noindex is unconditional in section 5a. Section 5b introduces the content
    bar that decides when it may be lifted.

    `bookable` defaults to False so a caller that forgets it does not
    advertise a booking page that is not configured. Like every other input
    here, it arrives as an argument: this module reads no database.
    """
    name = _display_name(view)
    title = build_title(view)
    desc = build_description(view)
    body = (
        '<div class="wrap">'
        + _photo(view)
        + f"<h1>{_esc(name)}</h1>"
        + _line("tagline", view.get("tagline"))
        + _line("practice", view.get("practice_name"))
        + _book_block(view, bookable)
        + _logo(view)
        + _line("loc", view.get("location"))
        + _line("phone", view.get("practitioner_phone"))
        + _accepting(view)
        + _section("About", view.get("bio"))
        + _section("How I work", view.get("how_i_work"))
        + _services(view)
        + _featured(view)
        # Safe only because catalog_url is hardcoded today --
        # build_practitioner_storefront always sets it to "/begin/explore"
        # and no profile field can override it. _esc() does not scheme-check
        # its input, so a "javascript:" value would render inert here but
        # execute as a real link if this field is ever made
        # practitioner-authorable; that would need a scheme allowlist at
        # write time (the same shape as sanitize_image_url), not just escaping.
        + f'<p><a href="{_esc(view.get("catalog_url") or "/begin/explore")}">'
          "Browse the full catalog</a></p>"
        + f'<p class="disclosure">{_esc(view.get("profit_disclosure"))}</p>'
        + "</div>"
    )
    return (
        "<!doctype html>"
        '<html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>{_esc(title)}</title>"
        f'<meta name="description" content="{_esc(desc)}">'
        f"{_share_tags(view, title, desc, canonical_url)}"
        + (f'<link rel="canonical" href="{_esc(canonical_url)}">'
           if canonical_url else "")
        + '<meta name="robots" content="noindex">'
        f"{_jsonld_tag(view, canonical_url)}"
        f"{_STYLE}"
        "</head><body>"
        f"{body}"
        "</body></html>"
    )
