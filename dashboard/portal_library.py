"""Email-keyed ebook entitlement store. A grant records that an email is
entitled to an ebook slug (from a site opt-in). Keyed by EMAIL, not the portal
token, because tokens rotate while the email identity is durable.

Table name is `ebook_grants` and the primary key is the natural composite
(email, ebook_slug) — deliberately NO surrogate `id INTEGER PRIMARY KEY`.
On Postgres (prod) a bare `INTEGER PRIMARY KEY` is a plain, non-identity column,
so an INSERT that omits `id` fails with a null-PK violation; only
`INTEGER PRIMARY KEY AUTOINCREMENT` is translated to a PG identity column by
pgcompat. The composite PK sidesteps that entirely and is portable across both
backends. (A prior revision used the non-portable form under the name
`portal_library`; this is a fresh, correctly-shaped table.)
"""
from datetime import datetime, timezone


def _now():
    return datetime.now(timezone.utc).isoformat()


def init_table(cx) -> None:
    cx.execute(
        "CREATE TABLE IF NOT EXISTS ebook_grants ("
        "  email TEXT,"
        "  ebook_slug TEXT,"
        "  granted_at TEXT,"
        "  source_site TEXT,"
        "  PRIMARY KEY (email, ebook_slug))")


def grant(cx, email, slug, source_site="") -> bool:
    email = (email or "").strip().lower()
    slug = (slug or "").strip()
    cur = cx.execute(
        "INSERT OR IGNORE INTO ebook_grants (email, ebook_slug, granted_at, source_site) "
        "VALUES (?,?,?,?)", (email, slug, _now(), (source_site or "").strip()))
    return cur.rowcount == 1


def list_for_email(cx, email) -> list:
    email = (email or "").strip().lower()
    rows = cx.execute(
        "SELECT ebook_slug, granted_at, source_site FROM ebook_grants "
        "WHERE email=? ORDER BY granted_at DESC, ebook_slug", (email,)).fetchall()
    return [{"slug": r[0], "granted_at": r[1], "source_site": r[2]} for r in rows]


def has(cx, email, slug) -> bool:
    email = (email or "").strip().lower()
    return cx.execute(
        "SELECT 1 FROM ebook_grants WHERE email=? AND ebook_slug=?",
        (email, (slug or "").strip())).fetchone() is not None


# --- Links Glen adds to one client's Library (2026-10-13) ---------------------------
# A protocol page or handout for one client: Linda Maher's Constitutional Hydrotherapy
# and Titration were the first. Keyed by email like the grants above, with the URL in
# the composite key, so adding the same link twice is a no-op. Only https links are
# stored, because the portal renders them as clickable anchors.

def init_links_table(cx) -> None:
    cx.execute(
        "CREATE TABLE IF NOT EXISTS portal_library_links ("
        "  email TEXT,"
        "  url TEXT,"
        "  title TEXT,"
        "  added_at TEXT,"
        "  added_by TEXT,"
        "  PRIMARY KEY (email, url))")


def clean_link(url, title):
    """(url, title) when both are acceptable, else raises ValueError with the reason."""
    from urllib.parse import urlsplit
    if not isinstance(url, str) or not isinstance(title, str):
        raise ValueError("The link and its title must be text.")
    url = url.strip()
    title = " ".join(title.split())
    if url[:8].lower() == "https://":
        url = "https://" + url[8:]       # stored lowercase, as the page checks it
    parts = urlsplit(url)
    if (not url.startswith("https://") or not parts.netloc
            or any(c in url for c in ' "<>\'')):
        raise ValueError("The link must be a full https:// address.")
    if not title:
        raise ValueError("The link needs a title.")
    if len(url) > 1000 or len(title) > 120:
        raise ValueError("The link or its title is too long.")
    return url, title


def add_link(cx, email, url, title, added_by="") -> bool:
    email = (email or "").strip().lower()
    if not email:
        raise ValueError("No client email.")
    url, title = clean_link(url, title)
    init_links_table(cx)
    cur = cx.execute(
        "INSERT OR IGNORE INTO portal_library_links (email, url, title, added_at, added_by) "
        "VALUES (?,?,?,?,?)", (email, url, title, _now(), (added_by or "")[:40]))
    return cur.rowcount == 1


def remove_link(cx, email, url) -> bool:
    init_links_table(cx)
    url = (url or "").strip() if isinstance(url, str) else ""
    if url[:8].lower() == "https://":
        url = "https://" + url[8:]       # stored that way by clean_link
    cur = cx.execute("DELETE FROM portal_library_links WHERE email=? AND url=?",
                     ((email or "").strip().lower(), url))
    return cur.rowcount == 1


def links_for_email(cx, email) -> list:
    init_links_table(cx)
    rows = cx.execute(
        "SELECT url, title, added_at FROM portal_library_links WHERE email=? "
        "ORDER BY added_at, title", ((email or "").strip().lower(),)).fetchall()
    return [{"url": r[0], "title": r[1], "added_at": r[2]} for r in rows]
