"""Practitioner site slug namespace: one canonical slug, zero or more alternates.

Spec: docs/superpowers/specs/2026-08-27-practitioner-website-design.md section 1.

A practitioner has exactly one CANONICAL slug (affiliate_signups.slug, minted by
dashboard.affiliate_dashboard._mint_affiliate_slug) and zero or more ALTERNATES.
The canonical serves content. Alternates 301 to it and never render, so they
cannot compete with the canonical as duplicate content.

Both kinds share ONE namespace: an alternate may collide with neither another
alternate, nor any canonical, nor any reserved route segment.

Imports no Flask app, so it is unit-testable on its own.
"""

import re

MIN_LEN = 3
MAX_LEN = 40

# Rejects leading, trailing, and doubled hyphens by construction.
_SHAPE_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


class SlugError(ValueError):
    """A proposed slug is malformed, reserved, or already taken."""


def normalize(raw):
    """Lowercase and strip. Does NOT rewrite an invalid slug into a valid one:
    normalizing away a bad character would silently hand back a slug the
    practitioner did not ask for."""
    return (raw or "").strip().lower()


def check_shape(slug):
    """Raise SlugError unless `slug` is 3-40 chars of lowercase alphanumerics
    separated by single internal hyphens."""
    if not slug:
        raise SlugError("slug is empty")
    if len(slug) < MIN_LEN:
        raise SlugError(f"slug must be at least {MIN_LEN} characters")
    if len(slug) > MAX_LEN:
        raise SlugError(f"slug must be at most {MAX_LEN} characters")
    if not _SHAPE_RE.match(slug):
        raise SlugError(
            "slug must be lowercase letters, digits, and single internal hyphens")


# Words we do not route today but may want to. A slug claimed here would have to
# be broken later, and breaking a published URL is the one thing this design
# promises never to do.
EXTRA_RESERVED = frozenset({
    "about", "account", "accounts", "app", "apps", "auth", "billing", "blog",
    "book", "booking", "cart", "checkout", "contact", "docs", "faq", "help",
    "home", "index", "info", "login", "logout", "mail", "media", "news", "pages",
    "press", "pricing", "profile", "profiles", "register", "root", "search",
    "settings", "shop", "signin", "signup", "site", "sites", "store", "support",
    "team", "test", "user", "users", "www",
})


def route_segments(url_map):
    """The set of STATIC first path segments in a Werkzeug Map.

    Dynamic segments are skipped, so the practitioner catch-all `/<slug>` does
    not reserve itself into oblivion. The root rule contributes nothing.
    """
    out = set()
    for rule in url_map.iter_rules():
        parts = (rule.rule or "").split("/")
        if len(parts) < 2:
            continue
        first = parts[1]
        if not first or "<" in first:
            continue
        out.add(first.lower())
    return frozenset(out)


def reserved_for(url_map):
    """Every word a practitioner slug may not be: live route segments plus the
    static buffer of words we may want to route later."""
    return frozenset(route_segments(url_map) | EXTRA_RESERVED)


def check_not_reserved(slug, reserved):
    """Raise SlugError if `slug` is a reserved word."""
    if slug in reserved:
        raise SlugError(f"'{slug}' is reserved")
