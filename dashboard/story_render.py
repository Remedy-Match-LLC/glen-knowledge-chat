"""Pure server-side renderer for story pages (/stories).

No Flask import: takes page dicts, returns HTML strings. Uses the mentor pages' fonts,
palette and brand bar so stories sit inside the same site.

Every piece of stored text is HTML-escaped. The giver's words are printed exactly as
stored: blank lines become paragraph breaks and nothing else changes. Every link
carries ?ref=<giver ref slug>&story=<story slug>, with ref left off when the giver
has no valid slug.
"""
import html
from urllib.parse import urlencode

from dashboard import mentor_render as _mr
from dashboard import story_pages as _sp

FDA_STATEMENT = (
    "The statements herein have not been evaluated by the Food and Drug Administration. "
    "This is not intended to diagnose, treat, cure, or prevent any disease."
)

_EXTRA_STYLE = (
    "<style>"
    ".story-kicker{color:var(--muted);font-size:14px;letter-spacing:0.04em;"
    "font-family:var(--heading);margin:18px 0 0;}"
    ".story-text{margin-top:22px;white-space:pre-wrap;overflow-wrap:anywhere;}"
    ".story-photo{margin:22px 0 4px;}"
    ".story-photo img{max-width:100%;height:auto;border-radius:12px;"
    "border:1px solid var(--border);}"
    ".story-links{margin-top:34px;border-top:1px solid var(--border);padding-top:16px;}"
    ".story-links ul{list-style:none;margin-top:8px;}"
    ".story-links li{margin-bottom:8px;}"
    ".preview-banner{background:#7a2e1f;color:#fdf4d8;text-align:center;"
    "font-family:var(--heading);font-weight:700;letter-spacing:0.08em;padding:10px 16px;}"
    ".preview-meta{max-width:var(--maxw);margin:12px auto 0;padding:12px 24px;"
    "font-size:13px;color:var(--muted);background:var(--surface);"
    "border:1px solid var(--border);border-radius:12px;}"
    ".preview-meta code{color:var(--cream);}"
    "</style>"
)

_FOOTER = (
    '<footer><p class="foot-note">' + html.escape(FDA_STATEMENT) + "</p></footer>"
)


def _esc(s):
    return html.escape(str(s if s is not None else ""), quote=True)


def tag_link(path, *, story_slug, ref_slug=""):
    """Add the tracking query to a site path. Returns "" for a path that is not a
    plain site path, so a bad link is dropped rather than rendered."""
    if not _sp.valid_link_path(path or ""):
        return ""
    params = []
    if ref_slug and _sp.valid_ref_slug(ref_slug):
        params.append(("ref", ref_slug))
    if _sp.valid_slug(story_slug or ""):
        params.append(("story", story_slug))
    return path + ("?" + urlencode(params) if params else "")


def _story_text(text):
    """The giver's words exactly as stored: escaped, nothing trimmed, split or joined.
    The block is styled white-space:pre-wrap, so spaces and line breaks show as typed."""
    return _esc(text if isinstance(text, str) else "")


def _document(title, meta_desc, body_inner, *, noindex=False, banner=""):
    robots = '<meta name="robots" content="noindex,nofollow">' if noindex else ""
    desc = f'<meta name="description" content="{_esc(meta_desc)}">' if meta_desc else ""
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        f"<title>{_esc(title)}</title>{desc}{robots}"
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        '<link rel="icon" type="image/png" href="/static/favicon.png">'
        f"{_mr._FONTS}{_mr._STYLE}{_EXTRA_STYLE}</head><body>"
        f"{banner}{_mr._BRANDBAR}"
        f'<section class="shell">{body_inner}</section>'
        f"{_FOOTER}</body></html>"
    )


def _page_body(page):
    content = page.get("content") or {}
    slug = page.get("story_slug") or page.get("slug") or ""
    ref = page.get("ref_slug") or ""
    parts = ['<main><p class="story-kicker">A story</p>',
             f"<h1>{_esc(page.get('name_line'))}</h1>"]
    photo = content.get("photo")
    if isinstance(photo, dict) and photo.get("src") and _sp.PHOTO_SRC_RE.match(photo["src"]):
        parts.append(f'<figure class="story-photo"><img src="{_esc(photo["src"])}" '
                     f'alt="{_esc(photo.get("alt") or "")}"></figure>')
    parts.append(f'<div class="story-text">{_story_text(content.get("story"))}</div>')
    items = []
    for ln in content.get("links") or []:
        if not isinstance(ln, dict):
            continue
        href = tag_link(ln.get("path") or "", story_slug=slug, ref_slug=ref)
        if not href:
            continue
        items.append(f'<li><a href="{_esc(href)}">{_esc(ln.get("label"))}</a></li>')
    if items:
        parts.append('<section class="story-links"><h2>Related pages</h2><ul>'
                     + "".join(items) + "</ul></section>")
    parts.append("</main>")
    return "".join(parts)


def _title(page):
    return f"{page.get('name_line') or 'A story'} · Stories"


def render_page_html(page):
    """The public page. Callers must check story_pages.is_public first."""
    return _document(_title(page), f"A story shared by {page.get('name_line') or ''}.",
                     _page_body(page))


def render_preview_html(page, *, testimonial=None):
    """Console preview of a page in any state. Marked not published, never indexed."""
    state = page.get("state") or "draft"
    banner = ('<div class="preview-banner">Preview, not published'
              + ("" if state == "published" else f" (state: {_esc(state)})") + "</div>")
    if state == "published":
        banner = '<div class="preview-banner">Preview (this page is published)</div>'
    story = (page.get("content") or {}).get("story") or ""
    meta = [f"State: <code>{_esc(state)}</code>",
            f"Content hash: <code>{_esc(page.get('current_hash'))}</code>",
            f"Hash at last step: <code>{_esc(page.get('content_hash') or 'none')}</code>",
            f"Ref slug: <code>{_esc(page.get('ref_slug') or 'none')}</code>",
            f"Testimonial id: <code>{_esc(page.get('testimonial_id'))}</code>"]
    if testimonial:
        body = testimonial.get("body") or ""
        meta.append("Story text matches the testimonial wording: <code>"
                    + ("yes" if body == story else "no") + "</code>")
        meta.append(f"Testimonial public consent: <code>"
                    f"{'yes' if int(testimonial.get('consent_public') or 0) else 'no'}</code>"
                    f", consent ref <code>{_esc(testimonial.get('consent_ref') or 'none')}</code>")
    else:
        meta.append("Testimonial row: <code>not found</code>")
    for label, key in (("Step 1", "compliance_at"), ("Step 2", "giver_approved_at"),
                       ("Step 3", "published_at")):
        meta.append(f"{label}: <code>{_esc(page.get(key) or 'not done')}</code>")
    banner += '<div class="preview-meta">' + "<br>".join(meta) + "</div>"
    return _document(_title(page), "", _page_body(page), noindex=True, banner=banner)


def render_index_html(pages):
    items = []
    for p in pages or []:
        slug = p.get("story_slug") or p.get("slug") or ""
        if not _sp.valid_slug(slug):
            continue
        items.append(f'<li><a href="/stories/{_esc(slug)}">{_esc(p.get("name_line"))}</a></li>')
    inner = "".join(items) or "<li>Stories are on the way.</li>"
    body = ("<main><h1>Stories</h1>"
            "<p>People who have worked with Dr. Glen Swartwout, in their own words.</p>"
            f'<ul class="index-list">{inner}</ul></main>')
    return _document("Stories", "People who have worked with Dr. Glen Swartwout, "
                     "in their own words.", body)


def render_sitemap_xml(pages, base_url):
    base = (base_url or "").rstrip("/")
    parts = []
    for p in pages or []:
        slug = p.get("story_slug") or p.get("slug") or ""
        if not _sp.valid_slug(slug):
            continue
        loc = html.escape(base + "/stories/" + slug, quote=True)
        stamp = (p.get("published_at") or p.get("updated_at") or "")[:10]
        lastmod = f"<lastmod>{html.escape(stamp)}</lastmod>" if stamp else ""
        parts.append(f"<url><loc>{loc}</loc>{lastmod}</url>")
    return ('<?xml version="1.0" encoding="UTF-8"?>'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
            + "".join(parts) + "</urlset>")
