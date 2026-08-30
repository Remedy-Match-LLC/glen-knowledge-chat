"""Server-rendered practitioner page.

Pure functions: payload dict in, HTML string out. No database access, no
Flask, no environment reads -- every input arrives as an argument. That is
what lets the whole surface be tested without a request context, and it is
why the canonical URL is passed in rather than derived here.

Why server-rendered at all: link-preview bots for iMessage, WhatsApp,
Facebook and Slack do not execute JavaScript. The JS storefront rendered a
blank preview card when a client texted their practitioner's link, which is
the referral motion this whole feature exists to serve.
"""
import html
import json

# The page is deliberately plain. It inherits nothing from the funnel's
# stylesheet because it is a practitioner's page on their own domain.
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
    practice = (view.get("practice_name") or "").strip()
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
        val = (view.get(key) or "").strip()
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
    url = (view.get("photo_url") or "").strip()
    if not url:
        return ""
    alt = _esc(view.get("practitioner_name") or "Practitioner")
    return f'<img class="photo" src="{_esc(url)}" alt="{alt}">'


def _line(css_class, text):
    text = (text or "").strip()
    return f'<p class="{css_class}">{_esc(text)}</p>' if text else ""


def _logo(view):
    url = (view.get("logo_url") or "").strip()
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
    """Render the answer either way.

    False is information a visitor needs before they compose an email. Showing
    nothing reads as "unknown", which is the one thing it is not.
    """
    return ('<p class="accepting">Accepting new clients</p>'
            if view.get("accepting_clients")
            else '<p class="accepting">Not currently accepting new clients</p>')


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


def _share_tags(view, title, desc, canonical_url):
    """Open Graph and Twitter Card tags.

    og:type is "profile" rather than "website" because this page is a person.
    The Twitter card type follows the photo: summary_large_image with no image
    renders as an empty grey box, which looks more broken than the small card.

    og:url is omitted entirely when canonical_url is falsy, same contract and
    same reason as the <link rel="canonical"> tag in render_page_html: a
    relative or empty URL here is not a safe default, it is a wrong one, and
    omitting the tag is the neutral choice.
    """
    photo = (view.get("photo_url") or "").strip()
    tags = [
        '<meta property="og:type" content="profile">',
        f'<meta property="og:title" content="{_esc(title)}">',
        f'<meta property="og:description" content="{_esc(desc)}">',
    ]
    if canonical_url:
        tags.append(f'<meta property="og:url" content="{_esc(canonical_url)}">')
    tags.append(f'<meta property="og:site_name" content="{_esc(SITE_NAME)}">')
    tags.append(f'<meta name="twitter:card" content='
                f'"{"summary_large_image" if photo else "summary"}">')
    tags.append(f'<meta name="twitter:title" content="{_esc(title)}">')
    tags.append(f'<meta name="twitter:description" content="{_esc(desc)}">')
    if photo:
        tags.append(f'<meta property="og:image" content="{_esc(photo)}">')
        tags.append(f'<meta name="twitter:image" content="{_esc(photo)}">')
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
    if (view.get("photo_url") or "").strip():
        person["image"] = view["photo_url"].strip()

    service = {"@context": "https://schema.org", "@type": "ProfessionalService",
               "name": (view.get("practice_name") or "").strip() or name}
    if canonical_url:
        service["url"] = canonical_url
    if (view.get("location") or "").strip():
        service["address"] = view["location"].strip()
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


def render_page_html(view, *, canonical_url):
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
        + _logo(view)
        + _line("loc", view.get("location"))
        + _accepting(view)
        + _section("About", view.get("bio"))
        + _section("How I work", view.get("how_i_work"))
        + _services(view)
        + _featured(view)
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
