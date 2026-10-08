"""Story pages: a published page per giver story, served at /stories/<slug>.

Spec: platform/plans/stories-phase-1-spec.md (vault), approved by Glen 2026-10-08.

A story page points at one `product_reviews` row (kind='testimonial') by id. This
module READS that row and never writes it.

The publishing gate has three steps, in order, each recorded with who and when:

    draft --compliance--> checked --giver_approve--> giver_approved --publish--> published

Any change to the content, the name line, the ref slug or the testimonial id sends the
page back to `draft` and clears every step stamp. Each step also takes the hash of the
content the approver saw. A step refuses when that hash differs from the page as stored,
and steps 2 and 3 also refuse when the stored hash from the previous step no longer
matches. Every step is a compare-and-set UPDATE, so two approvers cannot race a stale
page through.

Works on SQLite and Postgres through dashboard.db: '?' placeholders, no lastrowid,
INSERT OR IGNORE (translated to ON CONFLICT DO NOTHING on Postgres).
"""
import datetime
import hashlib
import json
import re

STATES = ("draft", "checked", "giver_approved", "published", "withdrawn")

# Story slug: lowercase words joined by single hyphens. No dots, so it can never be
# "sitemap.xml", and no slashes.
SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
SLUG_MAX = 80
# Same pattern as app._REF_SLUG_RE (a test pins the two together). Kept here so this
# module does not import app.
REF_SLUG_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
# A link target is a site-relative path only: no scheme, no host, no query string.
# The tracking query is added at render time.
LINK_PATH_RE = re.compile(r"^/(?!/)[A-Za-z0-9/_.\-]*$")
PHOTO_SRC_RE = re.compile(r"^(?:/static/[A-Za-z0-9/_.\-]+|https://[A-Za-z0-9.\-]+/[A-Za-z0-9/_.\-%]*)$")

STORY_MAX = 20000
NAME_LINE_MAX = 200
LABEL_MAX = 200
LINKS_MAX = 20
TARGET_PATH_MAX = 200

_COLS = (
    "story_slug", "testimonial_id", "name_line", "content_json", "ref_slug", "state",
    "compliance_at", "compliance_by", "compliance_note",
    "giver_approved_at", "giver_approved_by", "giver_consent_ref",
    "published_at", "published_by", "withdrawn_at", "withdrawn_by",
    "content_hash", "created_at", "updated_at",
)
_STEP_STAMPS = (
    "compliance_at", "compliance_by", "compliance_note",
    "giver_approved_at", "giver_approved_by", "giver_consent_ref",
    "published_at", "published_by",
)


class StoryError(ValueError):
    """A refused operation. `code` is a short machine-readable reason."""

    def __init__(self, code, detail=""):
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


def _now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def init_table(cx):
    cx.execute(
        "CREATE TABLE IF NOT EXISTS story_pages ("
        "story_slug TEXT PRIMARY KEY, "
        "testimonial_id INTEGER NOT NULL DEFAULT 0, "
        "name_line TEXT NOT NULL DEFAULT '', "
        "content_json TEXT NOT NULL DEFAULT '{}', "
        "ref_slug TEXT NOT NULL DEFAULT '', "
        "state TEXT NOT NULL DEFAULT 'draft', "
        "compliance_at TEXT NOT NULL DEFAULT '', "
        "compliance_by TEXT NOT NULL DEFAULT '', "
        "compliance_note TEXT NOT NULL DEFAULT '', "
        "giver_approved_at TEXT NOT NULL DEFAULT '', "
        "giver_approved_by TEXT NOT NULL DEFAULT '', "
        "giver_consent_ref TEXT NOT NULL DEFAULT '', "
        "published_at TEXT NOT NULL DEFAULT '', "
        "published_by TEXT NOT NULL DEFAULT '', "
        "withdrawn_at TEXT NOT NULL DEFAULT '', "
        "withdrawn_by TEXT NOT NULL DEFAULT '', "
        "content_hash TEXT NOT NULL DEFAULT '', "
        "created_at TEXT NOT NULL DEFAULT '', "
        "updated_at TEXT NOT NULL DEFAULT '')"
    )
    cx.execute(
        "CREATE TABLE IF NOT EXISTS story_clicks ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "story_slug TEXT NOT NULL, "
        "target_path TEXT NOT NULL, "
        "session_hash TEXT NOT NULL, "
        "clicked_at TEXT NOT NULL, "
        "UNIQUE(story_slug, target_path, session_hash))"
    )
    cx.commit()


# ── validation ───────────────────────────────────────────────────────────────

def valid_slug(slug):
    s = slug or ""
    return len(s) <= SLUG_MAX and bool(SLUG_RE.match(s))


def valid_ref_slug(ref):
    return bool(REF_SLUG_RE.match(ref or ""))


def valid_link_path(path):
    return bool(LINK_PATH_RE.match(path or ""))


def normalize_content(content):
    """Validate and return the canonical content dict, or raise StoryError.

    Shape: {"story": str, "links": [{"label": str, "path": "/..."}],
            "photo": {"src": str, "alt": str}  (optional)}
    The story text is stored exactly as given. Nothing is trimmed inside it.
    """
    if isinstance(content, str):
        try:
            content = json.loads(content)
        except ValueError:
            raise StoryError("bad_content", "content is not JSON")
    if not isinstance(content, dict):
        raise StoryError("bad_content", "content must be an object")
    unknown = set(content) - {"story", "links", "photo"}
    if unknown:
        raise StoryError("bad_content", f"unknown keys: {sorted(unknown)}")
    story = content.get("story")
    if not isinstance(story, str) or not story.strip():
        raise StoryError("bad_content", "story text is required")
    if len(story) > STORY_MAX:
        raise StoryError("bad_content", "story text is too long")
    links_in = content.get("links") or []
    if not isinstance(links_in, list) or len(links_in) > LINKS_MAX:
        raise StoryError("bad_content", "links must be a list")
    links = []
    for ln in links_in:
        if not isinstance(ln, dict):
            raise StoryError("bad_content", "each link is an object")
        label = ln.get("label")
        path = ln.get("path")
        if not isinstance(label, str) or not label.strip() or len(label) > LABEL_MAX:
            raise StoryError("bad_content", "each link needs a label")
        if not isinstance(path, str) or not valid_link_path(path):
            raise StoryError("bad_link", f"link path must be a site path like /begin/product/x: {path!r}")
        links.append({"label": label, "path": path})
    out = {"story": story, "links": links}
    photo = content.get("photo")
    if photo:
        if not isinstance(photo, dict):
            raise StoryError("bad_content", "photo must be an object")
        src = photo.get("src")
        alt = photo.get("alt") or ""
        if not isinstance(src, str) or not PHOTO_SRC_RE.match(src):
            raise StoryError("bad_photo", "photo src must be /static/... or https://")
        if not isinstance(alt, str):
            raise StoryError("bad_photo", "photo alt must be text")
        out["photo"] = {"src": src, "alt": alt}
    return out


def _dump(content):
    return json.dumps(content, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def compute_hash(content, name_line):
    """sha256 over the canonical content JSON and the name line."""
    if isinstance(content, str):
        content = json.loads(content or "{}")
    payload = _dump({"content": content, "name_line": name_line or ""})
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def default_name_line(full_name):
    """First name plus last initial: 'Jane Doe' -> 'Jane D.'"""
    parts = [p for p in (full_name or "").split() if p]
    if not parts:
        return ""
    if len(parts) == 1:
        return parts[0]
    return f"{parts[0]} {parts[-1][0].upper()}."


# ── testimonial (read only) ──────────────────────────────────────────────────

def get_testimonial(cx, testimonial_id):
    """Read one product_reviews row. Never writes, and never calls its init_table
    (which ALTERs the table). Returns None when absent or unreadable."""
    try:
        tid = int(testimonial_id)
    except (TypeError, ValueError):
        return None
    try:
        cur = cx.execute(
            "SELECT id, kind, name, body, original_body, consent_public, consent_ref, "
            "compliance_score, status FROM product_reviews WHERE id=?", (tid,))
        row = cur.fetchone()
    except Exception:  # noqa: BLE001 - missing table/column reads as "not found"
        try:
            cx.rollback()
        except Exception:  # noqa: BLE001
            pass
        return None
    if not row:
        return None
    keys = ("id", "kind", "name", "body", "original_body", "consent_public",
            "consent_ref", "compliance_score", "status")
    return {k: row[i] for i, k in enumerate(keys)}


def _require_testimonial(cx, testimonial_id):
    t = get_testimonial(cx, testimonial_id)
    if not t or (t.get("kind") or "") != "testimonial":
        raise StoryError("testimonial_not_found", f"no testimonial row {testimonial_id!r}")
    return t


# ── read ─────────────────────────────────────────────────────────────────────

def _row_to_page(row):
    page = {k: row[i] for i, k in enumerate(_COLS)}
    for k in _COLS:
        if page[k] is None:
            page[k] = 0 if k == "testimonial_id" else ""
    try:
        page["content"] = json.loads(page["content_json"] or "{}")
    except ValueError:
        page["content"] = {}
    page["slug"] = page["story_slug"]
    page["current_hash"] = compute_hash(page["content"], page["name_line"])
    return page


def get(cx, slug):
    init_table(cx)
    row = cx.execute(
        f"SELECT {', '.join(_COLS)} FROM story_pages WHERE story_slug=?", (slug or "",)
    ).fetchone()
    return _row_to_page(row) if row else None


def list_all(cx):
    init_table(cx)
    rows = cx.execute(
        f"SELECT {', '.join(_COLS)} FROM story_pages ORDER BY updated_at DESC").fetchall()
    return [_row_to_page(r) for r in rows]


def list_published(cx):
    """Published pages only, newest first."""
    init_table(cx)
    rows = cx.execute(
        f"SELECT {', '.join(_COLS)} FROM story_pages WHERE state='published' "
        "ORDER BY published_at DESC").fetchall()
    return [_row_to_page(r) for r in rows]


def is_public(page):
    return bool(page) and page.get("state") == "published"


# ── write ────────────────────────────────────────────────────────────────────

def create(cx, slug, *, testimonial_id, content, name_line="", ref_slug="", by=""):
    """Create a draft. Refuses an existing slug, a bad slug, or a missing testimonial."""
    init_table(cx)
    slug = (slug or "").strip()
    if not valid_slug(slug):
        raise StoryError("bad_slug", "lowercase letters, digits and single hyphens")
    if get(cx, slug):
        raise StoryError("exists", slug)
    t = _require_testimonial(cx, testimonial_id)
    content = normalize_content(content)
    name_line = (name_line or "").strip() or default_name_line(t.get("name") or "")
    if not name_line or len(name_line) > NAME_LINE_MAX:
        raise StoryError("bad_name_line", "a name line is required")
    ref_slug = (ref_slug or "").strip()
    if ref_slug and not valid_ref_slug(ref_slug):
        raise StoryError("bad_ref_slug", ref_slug)
    now = _now()
    cx.execute(
        "INSERT INTO story_pages (story_slug, testimonial_id, name_line, content_json, "
        "ref_slug, state, created_at, updated_at) VALUES (?, ?, ?, ?, ?, 'draft', ?, ?)",
        (slug, int(t["id"]), name_line, _dump(content), ref_slug, now, now))
    cx.commit()
    return get(cx, slug)


_UNSET = object()


def update(cx, slug, *, content=_UNSET, name_line=_UNSET, ref_slug=_UNSET,
           testimonial_id=_UNSET, by=""):
    """Edit a page. Any real change resets it to draft and clears every step stamp,
    so it must pass all three steps again. An edit that changes nothing is a no-op."""
    page = get(cx, slug)
    if not page:
        raise StoryError("not_found", slug)
    new = {
        "content_json": page["content_json"],
        "name_line": page["name_line"],
        "ref_slug": page["ref_slug"],
        "testimonial_id": page["testimonial_id"],
    }
    if content is not _UNSET:
        new["content_json"] = _dump(normalize_content(content))
    if name_line is not _UNSET:
        nl = (name_line or "").strip()
        if not nl or len(nl) > NAME_LINE_MAX:
            raise StoryError("bad_name_line", "a name line is required")
        new["name_line"] = nl
    if ref_slug is not _UNSET:
        rs = (ref_slug or "").strip()
        if rs and not valid_ref_slug(rs):
            raise StoryError("bad_ref_slug", rs)
        new["ref_slug"] = rs
    if testimonial_id is not _UNSET:
        new["testimonial_id"] = int(_require_testimonial(cx, testimonial_id)["id"])
    changed = any(new[k] != page[k] for k in new)
    if not changed:
        return page
    clears = ", ".join(f"{c}=''" for c in _STEP_STAMPS)
    cx.execute(
        "UPDATE story_pages SET content_json=?, name_line=?, ref_slug=?, testimonial_id=?, "
        f"state='draft', content_hash='', {clears}, updated_at=? WHERE story_slug=?",
        (new["content_json"], new["name_line"], new["ref_slug"], new["testimonial_id"],
         _now(), slug))
    cx.commit()
    return get(cx, slug)


def _cas(cx, sql, params):
    cur = cx.execute(sql, params)
    n = cur.rowcount
    cx.commit()
    if n != 1:
        raise StoryError("conflict", "the page changed while this step ran; reload it")


def mark_checked(cx, slug, *, by, note, content_hash):
    """Step 1, compliance. Needs a draft, a note, and the hash of the page reviewed."""
    page = get(cx, slug)
    if not page:
        raise StoryError("not_found", slug)
    if page["state"] != "draft":
        raise StoryError("wrong_state", f"step 1 needs draft, page is {page['state']}")
    if not (by or "").strip():
        raise StoryError("missing_by", "who ran the compliance check")
    if not (note or "").strip():
        raise StoryError("missing_note", "record the compliance result")
    if content_hash != page["current_hash"]:
        raise StoryError("hash_mismatch", "the page changed since it was reviewed")
    t = _require_testimonial(cx, page["testimonial_id"])
    if not int(t.get("consent_public") or 0):
        raise StoryError("no_consent", "the testimonial has no public consent")
    now = _now()
    _cas(cx,
         "UPDATE story_pages SET state='checked', compliance_at=?, compliance_by=?, "
         "compliance_note=?, content_hash=?, updated_at=? "
         "WHERE story_slug=? AND state='draft' AND content_json=? AND name_line=?",
         (now, by.strip(), note.strip(), page["current_hash"], now,
          slug, page["content_json"], page["name_line"]))
    return get(cx, slug)


def mark_giver_approved(cx, slug, *, by, consent_ref, content_hash):
    """Step 2, the giver approves the whole page. Needs a consent reference."""
    page = get(cx, slug)
    if not page:
        raise StoryError("not_found", slug)
    if page["state"] != "checked":
        raise StoryError("wrong_state", f"step 2 needs checked, page is {page['state']}")
    if not (by or "").strip():
        raise StoryError("missing_by", "who recorded the approval")
    if not (consent_ref or "").strip():
        raise StoryError("missing_consent_ref", "the giver's written approval reference")
    if page["content_hash"] != page["current_hash"]:
        raise StoryError("hash_mismatch", "the page changed since step 1")
    if content_hash != page["content_hash"]:
        raise StoryError("hash_mismatch", "the giver approved a different version")
    now = _now()
    _cas(cx,
         "UPDATE story_pages SET state='giver_approved', giver_approved_at=?, "
         "giver_approved_by=?, giver_consent_ref=?, updated_at=? "
         "WHERE story_slug=? AND state='checked' AND content_hash=? "
         "AND content_json=? AND name_line=?",
         (now, by.strip(), consent_ref.strip(), now,
          slug, page["content_hash"], page["content_json"], page["name_line"]))
    return get(cx, slug)


def mark_published(cx, slug, *, by, content_hash):
    """Step 3, Glen approves. The owner-only rule is enforced by the console action."""
    page = get(cx, slug)
    if not page:
        raise StoryError("not_found", slug)
    if page["state"] != "giver_approved":
        raise StoryError("wrong_state", f"step 3 needs giver_approved, page is {page['state']}")
    if not (by or "").strip():
        raise StoryError("missing_by", "who published")
    if page["content_hash"] != page["current_hash"]:
        raise StoryError("hash_mismatch", "the page changed since step 2")
    if content_hash != page["content_hash"]:
        raise StoryError("hash_mismatch", "this is not the version the giver approved")
    now = _now()
    _cas(cx,
         "UPDATE story_pages SET state='published', published_at=?, published_by=?, "
         "updated_at=? WHERE story_slug=? AND state='giver_approved' AND content_hash=? "
         "AND content_json=? AND name_line=?",
         (now, by.strip(), now,
          slug, page["content_hash"], page["content_json"], page["name_line"]))
    return get(cx, slug)


def withdraw(cx, slug, *, by=""):
    """Take a page down at once. The record and its stamps are kept."""
    page = get(cx, slug)
    if not page:
        raise StoryError("not_found", slug)
    now = _now()
    cx.execute(
        "UPDATE story_pages SET state='withdrawn', withdrawn_at=?, withdrawn_by=?, "
        "updated_at=? WHERE story_slug=?", (now, (by or "").strip(), now, slug))
    cx.commit()
    return get(cx, slug)


# ── clicks ───────────────────────────────────────────────────────────────────

def hash_session(session_id):
    """One-way hash of the anonymous session cookie. Only the hash is stored."""
    raw = ("story-click:" + (session_id or "")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:32]


def record_click(cx, story_slug, target_path, session_hash):
    """Count one click per (story, target, session). Returns True if it was new."""
    init_table(cx)
    if not valid_slug(story_slug) or not session_hash:
        return False
    target = (target_path or "")[:TARGET_PATH_MAX]
    if not target.startswith("/"):
        return False
    cur = cx.execute(
        "INSERT OR IGNORE INTO story_clicks (story_slug, target_path, session_hash, clicked_at) "
        "VALUES (?, ?, ?, ?)", (story_slug, target, session_hash, _now()))
    n = cur.rowcount
    cx.commit()
    return n == 1


def click_counts(cx):
    """Unique clicks per story and target, for the console."""
    init_table(cx)
    rows = cx.execute(
        "SELECT story_slug, target_path, COUNT(*) FROM story_clicks "
        "GROUP BY story_slug, target_path ORDER BY story_slug, target_path").fetchall()
    return [{"story_slug": r[0], "target_path": r[1], "clicks": int(r[2])} for r in rows]
