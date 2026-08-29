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
    "</style>"
)


def _esc(s):
    """Escape for both text nodes and attribute values.

    quote=True is not optional here: a raw double quote in a tagline would
    otherwise terminate a meta content="..." attribute and let the rest of
    the value be parsed as markup.
    """
    return html.escape(str(s or ""), quote=True)


def render_page_html(view, *, canonical_url):
    """Render the complete document for one practitioner.

    `view` is the payload from public_surface.build_practitioner_storefront.
    `canonical_url` is fully qualified and built by the caller from
    PORTAL_BASE_URL -- never from PUBLIC_BASE_URL, which is the funnel.

    noindex is unconditional in section 5a. Section 5b introduces the content
    bar that decides when it may be lifted.
    """
    name = view.get("practitioner_name") or view.get("slug") or "Practitioner"
    tagline = view.get("tagline") or ""
    return (
        "<!doctype html>"
        '<html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>{_esc(name)}</title>"
        '<meta name="robots" content="noindex">'
        f'<meta name="description" content="{_esc(tagline)}">'
        f"{_STYLE}"
        "</head><body>"
        f'<div class="wrap"><h1>{_esc(name)}</h1></div>'
        "</body></html>"
    )
