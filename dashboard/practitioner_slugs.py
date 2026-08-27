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
