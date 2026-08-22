"""Server-owned policy for personal referral links to public pages."""

from urllib.parse import urlencode, urlsplit


_STATIC_PAGES = {
    "/begin/explore": {
        "page_key": "education:explore",
        "title": "Explore Remedy Match",
        "kind": "education",
    },
    "/begin/tools": {
        "page_key": "education:tools",
        "title": "Recommended Tools & Partners",
        "kind": "education",
    },
}


def resolve_shareable_page(path, *, product_lookup=None):
    """Return normalized page metadata, or ``None`` when sharing is denied."""
    raw = (path or "").strip()
    if not raw or raw.startswith(("//", "http://", "https://")):
        return None
    try:
        parsed = urlsplit(raw)
    except ValueError:
        return None
    if parsed.scheme or parsed.netloc or parsed.query or parsed.fragment:
        return None
    clean = parsed.path.rstrip("/") or "/"
    static = _STATIC_PAGES.get(clean)
    if static:
        return {**static, "canonical_path": clean}

    prefix = "/begin/product/"
    if not clean.startswith(prefix):
        return None
    slug = clean[len(prefix):]
    if not slug or "/" in slug or slug in (".", "..") or product_lookup is None:
        return None
    try:
        product = product_lookup(slug)
    except Exception:
        return None
    if not product:
        return None
    canonical_slug = (product.get("slug") or slug).strip()
    if not canonical_slug or "/" in canonical_slug:
        return None
    return {
        "page_key": f"product:{canonical_slug}",
        "title": (product.get("name") or canonical_slug.replace("-", " ").title()).strip(),
        "kind": "product",
        "canonical_path": f"{prefix}{canonical_slug}",
    }


def canonical_share_url(public_base_url, page, affiliate_slug):
    """Build a same-site personal referral URL from trusted components."""
    base = (public_base_url or "").rstrip("/")
    slug = (affiliate_slug or "").strip()
    if not base or not page or not slug:
        return ""
    return f"{base}{page['canonical_path']}?{urlencode({'ref': slug, 'src': 'share'})}"

