"""Where a client lands after signing in: a portal hash route, carried through login.

A Plan report links to https://illtowell.com/portal/me#chat (Glen, 2026-10-20). A
browser never sends the part after "#" to the server, and the sign-in link is often
opened in another browser or a mail app, so the hash would be lost at login. The
portal page passes it to /portal/login as ?next=<key>; the login page sends it with
the request; the server keeps it on the emailed link and sends the client to
/portal/me#<key> once signed in.

Only a key from ROUTE_KEYS survives. Anything else, an unknown key or a URL, is
dropped, so `next` can never redirect anywhere but the client's own portal.
ROUTE_KEYS mirrors PORTAL_HASH_ROUTES in static/client-portal.html;
tests/test_portal_login_next.py fails when the two disagree.
"""
import re

ROUTE_KEYS = frozenset((
    "biofield", "biofield-latest", "biofield-order", "recs", "recommendations",
    "remedies", "intake", "health", "documents", "offers", "photo", "cart", "shop",
    "finder", "calendar", "chat",
))

_SHAPE = re.compile(r"[a-z-]{1,40}")


def clean_next(value):
    """The route key in `value`, or "" when it is not one we know. A leading "#" is
    allowed, so the login page may send location.hash as it is."""
    key = str(value or "").strip().lower()
    if key.startswith("#"):
        key = key[1:]
    if not _SHAPE.fullmatch(key) or key not in ROUTE_KEYS:
        return ""
    return key


def portal_me(value=""):
    """/portal/me, with the route's hash when `value` is a known key."""
    key = clean_next(value)
    return "/portal/me#" + key if key else "/portal/me"


def query(value=""):
    """"&next=<key>" for a link that already has a query string, else ""."""
    key = clean_next(value)
    return "&next=" + key if key else ""
