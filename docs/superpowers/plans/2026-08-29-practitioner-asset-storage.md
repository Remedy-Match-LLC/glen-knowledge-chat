# Practitioner Asset Storage — host the images instead of hotlinking them (Section 2c)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A practitioner uploads their photo and logo to us instead of pasting a third-party URL, and the image travels through the same review gate as every other profile field.

**Architecture:** Blobs live in the sqlite LOG_DB, following `dashboard/client_photos.py`. Upload validation reuses the existing content-type allowlist and size cap. Upload writes the blob AND sets the draft's `photo_url`/`logo_url` to a site-relative path, so the image reaches the public page only by the route every other field takes. Serving is access-controlled: public for a published asset, practitioner-session-only for an unpublished one.

**Tech Stack:** Python 3, Flask, sqlite (LOG_DB) via `dashboard.db`, pytest.

**Spec:** `docs/superpowers/specs/2026-08-27-practitioner-website-design.md` (section 2)

**Depends on:** sections 2a and 2b, both merged and live. The draft store, the review gate, and `sanitize_image_url` all come from there.

## Global Constraints

- **`_write_live_profile` stays the ONLY function that stamps `profile_self_authored_at`.** There is a guard test. Uploading an asset must not publish anything by itself.
- **An upload sets a draft field; it does not publish.** The image becomes public exactly when Glen approves the draft that references it, through `publish_draft`. Do not add a second path.
- **Access rule, decided by Glen:** an asset is served publicly ONLY when it is the practitioner's currently-published `photo_url`/`logo_url`. An unpublished asset is served ONLY to that practitioner's own session. This is what makes the gate govern image *content*, not just which URL the page prints.
- **Never put the practitioner session token in an asset URL.** `_practitioner_session_pid()` accepts `?token=`, and a preview URL carrying a credential is shareable, loggable, and cacheable. The preview path uses the header or cookie transport only.
- Reuse `_PHOTO_TYPES` (`image/jpeg`, `image/png`, `image/webp`) and `_PHOTO_MAX` (5 MB) from `app.py`. Do not invent new limits.
- Reuse `_doc_response_content_type` and `_doc_safe_filename` for response hardening. Serve with `X-Content-Type-Options: nosniff`.
- **Caching differs from the sibling helper.** `_serve_bodymap_photo` sets `Cache-Control: private, no-store` because those are private client photos. A published practitioner asset is a public page image and wants public caching; an unpublished one must not be cached at all.
- Drafts and assets are sqlite (`?`); the live profile is Postgres (`%s`). Never mixed in one function.
- **Stage named files only.** NEVER `git add -A` or `git add .` in this shared checkout.
- **Never run the full test suite.** Run only named files.
- Work in `/tmp/wt-deploy-chat-a191e6ab`, branch `sess/a191e6ab-s2c`.

## Scope boundary

**In scope:** the blob store, upload with validation, access-controlled serving, wiring an upload into the draft, and the settings-page upload controls.

**Out of scope:** image resizing, cropping or format conversion; a CDN; migrating existing hotlinked URLs (practitioners keep whatever they have until they upload); and rendering, which is still section 5. An uploaded photo will be stored, published and served in the JSON payload and displayed nowhere until then, exactly as `tagline` and `how_i_work` are today.

## File Structure

- **Create `dashboard/practitioner_assets.py`** — the blob store. sqlite only, no Flask import, modelled on `dashboard/client_photos.py`.
- **Modify `app.py`** — one upload route, one serve route, one shared validation helper.
- **Modify `static/practitioner-settings.html`** — file inputs beside the existing URL fields.
- **Create `tests/test_practitioner_assets.py`** — store unit tests.
- **Create `tests/test_practitioner_asset_routes.py`** — upload and access-control route tests.

---

### Task 1: The asset store

**Files:**
- Create: `dashboard/practitioner_assets.py`
- Test: `tests/test_practitioner_assets.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `KINDS: frozenset`, `init_table(cx) -> None`, `put(cx, pid, kind, blob, content_type) -> str`, `get(cx, pid, kind) -> dict|None`, `asset_path(pid, kind) -> str`.

`put` returns the site-relative path the draft field should hold. `asset_path` is the single place that path is constructed, so the route and the store cannot disagree about its shape.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_practitioner_assets.py
"""Unit tests for the practitioner asset blob store."""
import sqlite3

import pytest

from dashboard import practitioner_assets as pa

PID = "11111111-1111-1111-1111-111111111111"


@pytest.fixture
def cx(tmp_path):
    c = sqlite3.connect(str(tmp_path / "t.db"))
    c.row_factory = sqlite3.Row
    pa.init_table(c)
    return c


def test_kinds_are_photo_and_logo():
    assert pa.KINDS == frozenset({"photo", "logo"})


def test_asset_path_shape_is_site_relative():
    p = pa.asset_path(PID, "photo")
    assert p.startswith("/practitioner-asset/")
    assert p.endswith("/photo")
    assert PID in p


def test_get_returns_none_when_absent(cx):
    assert pa.get(cx, PID, "photo") is None


def test_put_then_get_round_trips_the_bytes(cx):
    pa.put(cx, PID, "photo", b"\x89PNG-bytes", "image/png")
    rec = pa.get(cx, PID, "photo")
    assert rec["blob"] == b"\x89PNG-bytes"
    assert rec["content_type"] == "image/png"


def test_put_returns_the_path_to_store_on_the_draft(cx):
    assert pa.put(cx, PID, "logo", b"x", "image/png") == pa.asset_path(PID, "logo")


def test_put_replaces_the_previous_asset_of_that_kind(cx):
    pa.put(cx, PID, "photo", b"old", "image/png")
    pa.put(cx, PID, "photo", b"new", "image/jpeg")
    rec = pa.get(cx, PID, "photo")
    assert rec["blob"] == b"new" and rec["content_type"] == "image/jpeg"


def test_photo_and_logo_are_independent(cx):
    pa.put(cx, PID, "photo", b"p", "image/png")
    pa.put(cx, PID, "logo", b"l", "image/png")
    assert pa.get(cx, PID, "photo")["blob"] == b"p"
    assert pa.get(cx, PID, "logo")["blob"] == b"l"


def test_put_rejects_an_unknown_kind(cx):
    with pytest.raises(ValueError):
        pa.put(cx, PID, "banner", b"x", "image/png")


def test_put_rejects_an_empty_blob(cx):
    with pytest.raises(ValueError):
        pa.put(cx, PID, "photo", b"", "image/png")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /tmp/wt-deploy-chat-a191e6ab && python3 -m pytest tests/test_practitioner_assets.py -v`
Expected: FAIL, `ModuleNotFoundError: dashboard.practitioner_assets`.

- [ ] **Step 3: Write minimal implementation**

```python
# dashboard/practitioner_assets.py
"""Practitioner-uploaded images, stored by us instead of hotlinked.

Spec: docs/superpowers/specs/2026-08-27-practitioner-website-design.md section 2.
Storage shape follows dashboard/client_photos.py: the bytes live in the sqlite
LOG_DB with their content type, keyed by owner.

Uploading an asset PUBLISHES NOTHING. The upload route stores the blob and puts
asset_path() into the practitioner's DRAFT; the image reaches the public page
only when Glen approves that draft, through the same publish_draft every other
field goes through. Keeping that single path is why this module has no notion
of "live".

sqlite only: `?` placeholders, no Postgres import. The live profile is Postgres
and is written in exactly one place, dashboard.practitioner_profile.
"""

import datetime

KINDS = frozenset({"photo", "logo"})


def _now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def asset_path(pid, kind):
    """The site-relative URL a draft stores for this asset.

    Single source of truth for the shape, so the serve route and the stored
    draft value cannot drift apart. Deliberately site-relative: section 2b's
    sanitize_image_url accepts a leading '/' precisely so this needs no
    validator change.
    """
    return f"/practitioner-asset/{pid}/{kind}"


def init_table(cx):
    """Create the asset table if absent. Idempotent; called on read paths,
    matching dashboard/referrals.py and dashboard/practitioner_drafts.py."""
    cx.execute("""CREATE TABLE IF NOT EXISTS practitioner_assets (
        practitioner_id TEXT NOT NULL,
        kind TEXT NOT NULL,
        image_blob BLOB NOT NULL,
        content_type TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        PRIMARY KEY (practitioner_id, kind))""")
    cx.commit()


def put(cx, pid, kind, blob, content_type):
    """Store one asset, replacing any previous asset of that kind.

    Returns the site-relative path the caller must write into the draft.
    Raises ValueError on an unknown kind or empty bytes: a silent no-op here
    would leave the draft pointing at an asset that does not exist.
    """
    if kind not in KINDS:
        raise ValueError(f"unknown asset kind {kind!r}")
    if not blob:
        raise ValueError("refusing to store an empty asset")
    init_table(cx)
    cx.execute(
        "INSERT INTO practitioner_assets"
        " (practitioner_id, kind, image_blob, content_type, updated_at)"
        " VALUES (?,?,?,?,?)"
        " ON CONFLICT(practitioner_id, kind) DO UPDATE SET"
        " image_blob=excluded.image_blob, content_type=excluded.content_type,"
        " updated_at=excluded.updated_at",
        (str(pid), kind, blob, content_type, _now()))
    cx.commit()
    return asset_path(pid, kind)


def get(cx, pid, kind):
    """The stored bytes and content type, or None."""
    init_table(cx)
    row = cx.execute(
        "SELECT image_blob, content_type, updated_at FROM practitioner_assets"
        " WHERE practitioner_id=? AND kind=?", (str(pid), kind)).fetchone()
    if not row or row[0] is None:
        return None
    return {"blob": row[0], "content_type": row[1] or "image/jpeg",
            "updated_at": row[2]}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /tmp/wt-deploy-chat-a191e6ab && python3 -m pytest tests/test_practitioner_assets.py -v`
Expected: PASS, 9 tests.

- [ ] **Step 5: Commit**

```bash
cd /tmp/wt-deploy-chat-a191e6ab
git add dashboard/practitioner_assets.py tests/test_practitioner_assets.py
git commit -m "feat(assets): practitioner image blob store"
```

---

### Task 2: Upload, and put the path on the draft

**Files:**
- Modify: `app.py`
- Test: `tests/test_practitioner_asset_routes.py`

**Interfaces:**
- Consumes: `put`, `asset_path` from Task 1; `_PHOTO_TYPES`, `_PHOTO_MAX`, `_practitioner_session_pid` (existing in app.py); `practitioner_drafts.get_draft`/`upsert_draft`.
- Produces: `POST /api/practitioner/asset/<kind>`.

The upload does two things in one transaction: store the blob, and set the draft's corresponding URL field to `asset_path`. If it only stored the blob, the practitioner would upload an image and nothing would change.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_practitioner_asset_routes.py
"""Upload and access control for practitioner-hosted images."""
import io
import os
import sqlite3

import pytest

if not os.environ.get("PINECONE_API_KEY"):
    pytest.skip("needs doppler env for import app", allow_module_level=True)

import app as appmod
from dashboard import practitioner_assets as pa
from dashboard import practitioner_drafts as pd

PID = "11111111-1111-1111-1111-111111111111"
PNG = b"\x89PNG\r\n\x1a\n" + b"x" * 32


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setattr(appmod, "LOG_DB", str(tmp_path / "chat_log.db"))
    monkeypatch.setitem(appmod.app.config, "TESTING", True)
    return appmod.app.test_client()


def _upload(client, kind="photo", data=PNG, ctype="image/png", name="p.png"):
    return client.post(f"/api/practitioner/asset/{kind}",
                       data={"file": (io.BytesIO(data), name, ctype)},
                       content_type="multipart/form-data")


def test_upload_requires_a_signed_in_practitioner(client, monkeypatch):
    monkeypatch.setattr(appmod, "_practitioner_session_pid", lambda: None)
    assert _upload(client).status_code == 401


def test_upload_rejects_an_unknown_kind(client, monkeypatch):
    monkeypatch.setattr(appmod, "_practitioner_session_pid", lambda: PID)
    assert _upload(client, kind="banner").status_code == 404


def test_upload_rejects_a_disallowed_content_type(client, monkeypatch):
    monkeypatch.setattr(appmod, "_practitioner_session_pid", lambda: PID)
    r = _upload(client, data=b"%PDF-1.4", ctype="application/pdf", name="x.pdf")
    assert r.status_code == 400


def test_upload_rejects_an_oversized_file(client, monkeypatch):
    monkeypatch.setattr(appmod, "_practitioner_session_pid", lambda: PID)
    big = b"x" * (appmod._PHOTO_MAX + 1)
    assert _upload(client, data=big).status_code == 400


def test_upload_stores_the_blob_and_sets_the_draft_field(client, monkeypatch):
    monkeypatch.setattr(appmod, "_practitioner_session_pid", lambda: PID)
    r = _upload(client)
    assert r.status_code == 200
    assert r.get_json()["url"] == pa.asset_path(PID, "photo")
    cx = sqlite3.connect(appmod.LOG_DB)
    cx.row_factory = sqlite3.Row
    assert pa.get(cx, PID, "photo")["blob"] == PNG
    assert pd.get_draft(cx, PID)["fields"]["photo_url"] == pa.asset_path(PID, "photo")


def test_upload_leaves_the_draft_unapproved(client, monkeypatch):
    """Uploading an image must not publish it. The draft goes back to 'draft'
    like any other edit, and Glen still has to approve."""
    monkeypatch.setattr(appmod, "_practitioner_session_pid", lambda: PID)
    _upload(client)
    cx = sqlite3.connect(appmod.LOG_DB)
    cx.row_factory = sqlite3.Row
    assert pd.get_draft(cx, PID)["status"] == "draft"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /tmp/wt-deploy-chat-a191e6ab && doppler run --project remedy-match --config dev -- python3 -m pytest tests/test_practitioner_asset_routes.py -v`
Expected: FAIL with 404s, the route does not exist.

- [ ] **Step 3: Write minimal implementation**

Add to `app.py`, near the other `/api/practitioner/` routes:

```python
@app.route("/api/practitioner/asset/<kind>", methods=["POST"])
def api_practitioner_asset_upload(kind):
    """Practitioner uploads their own photo or logo.

    Stores the bytes AND points the draft's matching URL field at them, in one
    transaction. Storing without updating the draft would leave the image
    invisible; updating without storing would leave a dangling path.

    This PUBLISHES NOTHING. The draft returns to 'draft' like any other edit
    and Glen still has to approve it, which is what makes the review gate cover
    image content and not merely which URL the page prints.
    """
    from dashboard import practitioner_assets as _pa
    from dashboard import practitioner_drafts as _pd
    pid = _practitioner_session_pid()
    if not pid:
        return jsonify({"ok": False, "error": "not signed in"}), 401
    if kind not in _pa.KINDS:
        return ("", 404)
    f = request.files.get("file") if request.files else None
    blob = f.read() if f else b""
    if not blob:
        return jsonify({"ok": False, "error": "no image uploaded"}), 400
    ctype = (getattr(f, "mimetype", "") or "").lower()
    if ctype not in _PHOTO_TYPES:
        return jsonify({"ok": False, "error": "use a JPG, PNG, or WEBP image"}), 400
    if len(blob) > _PHOTO_MAX:
        return jsonify({"ok": False, "error": "image too large (max 5 MB)"}), 400
    field = "photo_url" if kind == "photo" else "logo_url"
    with _db_lock, db.connect(LOG_DB) as cx:
        cx.row_factory = sqlite3.Row
        url = _pa.put(cx, pid, kind, blob, ctype)
        _pd.init_tables(cx)
        draft = _pd.get_draft(cx, pid)
        fields = dict((draft or {}).get("fields") or {})
        fields[field] = url
        _pd.upsert_draft(cx, pid, fields)
    return jsonify({"ok": True, "url": url})
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /tmp/wt-deploy-chat-a191e6ab && doppler run --project remedy-match --config dev -- python3 -m pytest tests/test_practitioner_asset_routes.py -v`
Expected: PASS, 6 tests.

- [ ] **Step 5: Commit**

```bash
cd /tmp/wt-deploy-chat-a191e6ab
git add app.py tests/test_practitioner_asset_routes.py
git commit -m "feat(assets): upload an image and point the draft at it"
```

---

### Task 3: Access-controlled serving

This is the task Glen made a specific ruling about. Read it fully.

**Files:**
- Modify: `app.py`
- Test: `tests/test_practitioner_asset_routes.py`

**Interfaces:**
- Consumes: `get`, `asset_path` from Task 1; `practitioner_profile.profile_for_slug` is NOT used here (it is slug-keyed and provenance-gated); the published value is read directly from Postgres.
- Produces: `GET /practitioner-asset/<pid>/<kind>`.

**The access rule, in full.** Serve the bytes when EITHER:
1. the asset's path is the practitioner's currently **published** `photo_url`/`logo_url` in the live `practitioners` row, in which case it is public; or
2. the requester **is that practitioner**, by session, in which case it is their own unpublished draft image and must not be cached or indexed.

Otherwise 404. Not 403: a 403 confirms the asset exists, and whether a given practitioner has uploaded a logo is not something an anonymous caller should be able to probe.

**Never accept the session token from the query string on this route.** `_practitioner_session_pid()` reads `?token=` among four transports. An `<img src>` carrying a credential would be logged by every proxy and shareable by copy-paste. Read the header or cookie only.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_practitioner_asset_routes.py

def _published(monkeypatch, value):
    """Stub the live-row read the serve route uses to decide 'is this public'."""
    monkeypatch.setattr(appmod, "_practitioner_published_asset_url",
                        lambda pid, kind: value)


def test_serve_404s_when_no_asset_exists(client, monkeypatch):
    _published(monkeypatch, "")
    monkeypatch.setattr(appmod, "_practitioner_session_pid", lambda: None)
    assert client.get(f"/practitioner-asset/{PID}/photo").status_code == 404


def test_serve_is_public_when_the_asset_is_published(client, monkeypatch):
    monkeypatch.setattr(appmod, "_practitioner_session_pid", lambda: PID)
    _upload(client)
    monkeypatch.setattr(appmod, "_practitioner_session_pid", lambda: None)
    _published(monkeypatch, pa.asset_path(PID, "photo"))
    r = client.get(f"/practitioner-asset/{PID}/photo")
    assert r.status_code == 200
    assert r.data == PNG
    assert r.headers.get("X-Content-Type-Options") == "nosniff"


def test_serve_404s_for_a_stranger_when_the_asset_is_unpublished(client, monkeypatch):
    """The gate covers image CONTENT: an image Glen has not approved is not
    public, even to someone who guesses the URL."""
    monkeypatch.setattr(appmod, "_practitioner_session_pid", lambda: PID)
    _upload(client)
    monkeypatch.setattr(appmod, "_practitioner_session_pid", lambda: None)
    _published(monkeypatch, "")
    assert client.get(f"/practitioner-asset/{PID}/photo").status_code == 404


def test_serve_shows_an_unpublished_asset_to_its_own_practitioner(client, monkeypatch):
    monkeypatch.setattr(appmod, "_practitioner_session_pid", lambda: PID)
    _upload(client)
    _published(monkeypatch, "")
    r = client.get(f"/practitioner-asset/{PID}/photo")
    assert r.status_code == 200
    assert "no-store" in (r.headers.get("Cache-Control") or "")


def test_serve_404s_for_a_different_practitioner(client, monkeypatch):
    monkeypatch.setattr(appmod, "_practitioner_session_pid", lambda: PID)
    _upload(client)
    monkeypatch.setattr(appmod, "_practitioner_session_pid",
                        lambda: "22222222-2222-2222-2222-222222222222")
    _published(monkeypatch, "")
    assert client.get(f"/practitioner-asset/{PID}/photo").status_code == 404


def test_published_asset_is_publicly_cacheable(client, monkeypatch):
    monkeypatch.setattr(appmod, "_practitioner_session_pid", lambda: PID)
    _upload(client)
    monkeypatch.setattr(appmod, "_practitioner_session_pid", lambda: None)
    _published(monkeypatch, pa.asset_path(PID, "photo"))
    cc = client.get(f"/practitioner-asset/{PID}/photo").headers.get("Cache-Control") or ""
    assert "public" in cc
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /tmp/wt-deploy-chat-a191e6ab && doppler run --project remedy-match --config dev -- python3 -m pytest tests/test_practitioner_asset_routes.py -v -k serve`
Expected: FAIL, `AttributeError: module 'app' has no attribute '_practitioner_published_asset_url'`.

- [ ] **Step 3: Write minimal implementation**

Add to `app.py`:

```python
def _practitioner_published_asset_url(pid, kind):
    """The practitioner's currently PUBLISHED photo_url/logo_url, or "".

    Reads the live Postgres row, because "published" is exactly what
    _write_live_profile wrote there. Fails closed: any error yields "", which
    makes the asset private rather than public.
    """
    column = "photo_url" if kind == "photo" else "logo_url"
    try:
        from db_supabase import supabase_cursor
        with supabase_cursor() as cur:
            cur.execute(
                f"SELECT {column} FROM practitioners WHERE id=%s", (str(pid),))
            row = cur.fetchone()
        return (row or {}).get(column) or ""
    except Exception as e:
        print(f"[practitioner-asset] published lookup failed: {e!r}", flush=True)
        return ""


def _practitioner_session_pid_no_query():
    """Like _practitioner_session_pid but WITHOUT the ?token= transport.

    An <img src> is copied, logged by proxies and cached. A session token in
    that URL is a credential in a shareable string, so the asset route accepts
    the header and cookie transports only.
    """
    token = (request.headers.get("X-Practitioner-Token") or "").strip()
    if not token:
        token = (request.cookies.get("rm_practitioner_session") or "").strip()
    return _pp.practitioner_id_from_session(token) if token else None


@app.route("/practitioner-asset/<pid>/<kind>")
def practitioner_asset_serve(pid, kind):
    """Serve a practitioner image.

    Public ONLY when this asset is the practitioner's published photo/logo.
    Otherwise visible only to that practitioner, so an image Glen has not
    approved is not on the internet. Anything else is a 404, never a 403: a
    403 would confirm the asset exists, and whether a practitioner has
    uploaded a logo is not an anonymous caller's business.
    """
    from dashboard import practitioner_assets as _pa
    if kind not in _pa.KINDS:
        return ("", 404)
    published = _practitioner_published_asset_url(pid, kind) == _pa.asset_path(pid, kind)
    if not published and _practitioner_session_pid_no_query() != str(pid):
        return ("", 404)
    with db.connect(LOG_DB) as cx:
        cx.row_factory = sqlite3.Row
        rec = _pa.get(cx, pid, kind)
    if not rec:
        return ("", 404)
    ctype, disp = _doc_response_content_type(rec["content_type"])
    resp = Response(rec["blob"], mimetype=ctype)
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Content-Disposition"] = f'{disp}; filename="{_doc_safe_filename(kind)}"'
    # A published asset is a public page image and should cache. An unpublished
    # one is a private preview and must not be cached anywhere.
    resp.headers["Cache-Control"] = ("public, max-age=300" if published
                                     else "private, no-store")
    return resp
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /tmp/wt-deploy-chat-a191e6ab && doppler run --project remedy-match --config dev -- python3 -m pytest tests/test_practitioner_asset_routes.py -v`
Expected: PASS, 12 tests.

- [ ] **Step 5: Mutation-test the access rule**

This is the security boundary of the plan, so prove it refuses. Temporarily delete the `if not published and ...: return ("", 404)` guard, re-run, and confirm BOTH `test_serve_404s_for_a_stranger_when_the_asset_is_unpublished` and `test_serve_404s_for_a_different_practitioner` go RED. Restore and confirm GREEN. Both raw transcripts in your report. A guard that has never been observed refusing is not known to work.

- [ ] **Step 6: Commit**

```bash
cd /tmp/wt-deploy-chat-a191e6ab
git add app.py tests/test_practitioner_asset_routes.py
git commit -m "feat(assets): serve published assets publicly, drafts to their owner only"
```

---

### Task 4: The settings-page upload controls

**Files:**
- Modify: `static/practitioner-settings.html`
- Test: `tests/test_practitioner_profile_routes.py`

**Interfaces:**
- Consumes: `POST /api/practitioner/asset/<kind>` from Task 2.
- Produces: nothing consumed later.

Without this the upload route has no caller, and the whole plan is unreachable by a human. That failure has happened four times in this project already, so treat the wiring as the deliverable.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_practitioner_profile_routes.py

def test_settings_page_offers_image_upload_controls():
    """A field a practitioner cannot use is a field that does not exist."""
    html = pathlib.Path(appmod.STATIC, "practitioner-settings.html").read_text(encoding="utf-8")
    for ident in ("sf-photo-file", "sf-logo-file"):
        assert ident in html, f"settings page has no upload control for {ident}"


def test_settings_page_posts_to_the_asset_endpoint():
    html = pathlib.Path(appmod.STATIC, "practitioner-settings.html").read_text(encoding="utf-8")
    assert "/api/practitioner/asset/" in html


def test_settings_page_does_not_put_the_session_token_in_an_image_url():
    """An <img src> with ?token= is a credential in a shareable, loggable URL."""
    html = pathlib.Path(appmod.STATIC, "practitioner-settings.html").read_text(encoding="utf-8")
    assert "practitioner-asset/" not in html.replace("/api/practitioner/asset/", "") \
        or "token=" not in html.split("practitioner-asset/")[1][:200]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /tmp/wt-deploy-chat-a191e6ab && doppler run --project remedy-match --config dev -- python3 -m pytest tests/test_practitioner_profile_routes.py -v -k upload_controls`
Expected: FAIL on the missing `sf-photo-file` identifier.

- [ ] **Step 3: Write minimal implementation**

In `static/practitioner-settings.html`, beside the existing `sf-photo` and `sf-logo-url` text inputs, add a file input for each:

```html
      <input type="file" id="sf-photo-file" accept="image/jpeg,image/png,image/webp">
      <input type="file" id="sf-logo-file" accept="image/jpeg,image/png,image/webp">
```

On `change`, POST the file as `multipart/form-data` under the field name `file` to `/api/practitioner/asset/photo` or `/api/practitioner/asset/logo`, sending the practitioner token in the `X-Practitioner-Token` header exactly as the existing save does. On success, write the returned `url` into the matching text input, mark the storefront card dirty using the same `markDirty` path the other fields use, and show the returned path so the practitioner can see it took.

On failure, surface the server's `error` message in the existing message element rather than a generic string. The server messages are already written for a practitioner to read.

Follow the page's existing `fetch` and message conventions exactly. Do not restyle the page.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /tmp/wt-deploy-chat-a191e6ab && doppler run --project remedy-match --config dev -- python3 -m pytest tests/test_practitioner_profile_routes.py -v`
Expected: PASS.

- [ ] **Step 5: Syntax-check the page's JavaScript**

Extract the `<script>` blocks and run `node --check` on them, reporting the raw result and confirming the extraction was non-empty. This page has no browser coverage, so that check is the only thing between a typo and a settings page that silently stops working.

- [ ] **Step 6: Run the full affected set**

Run: `cd /tmp/wt-deploy-chat-a191e6ab && doppler run --project remedy-match --config dev -- python3 -m pytest tests/test_practitioner_assets.py tests/test_practitioner_asset_routes.py tests/test_practitioner_profile.py tests/test_practitioner_profile_routes.py tests/test_practitioner_drafts.py tests/test_practitioner_review_console.py -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
cd /tmp/wt-deploy-chat-a191e6ab
git add static/practitioner-settings.html tests/test_practitioner_profile_routes.py
git commit -m "feat(settings): upload a photo and logo instead of pasting a URL"
```

---

## Done criteria

- A practitioner can upload a photo and a logo and see the path appear in the matching field.
- The upload sets a draft field and publishes nothing; the draft still needs Glen's approval.
- An unpublished asset is visible to its own practitioner and to nobody else, proven by a mutation test.
- A published asset is public and cacheable; an unpublished one carries `no-store`.
- No asset URL anywhere contains a session token.
- Practitioners who already have a working hotlinked URL keep it; nothing migrates them.

## Deploy note

No migration. The asset table is created lazily by `init_table`, exactly as the drafts table is.

The images are stored in the sqlite LOG_DB on Render's persistent disk, so they count against that disk and are not on a CDN. At 5 MB a practitioner and two assets each, fifty practitioners is under a gigabyte, which is fine at this scale. If the practitioner roster grows past a few hundred, move the blobs to object storage before the disk becomes the constraint.

**Uploaded images are not displayed anywhere yet.** As with `tagline` and `how_i_work`, the storefront page does not render `photo_url` or `logo_url` until section 5 adds server-rendering. An upload will be stored, approved, published and served in the JSON payload, and invisible on the page until then.
