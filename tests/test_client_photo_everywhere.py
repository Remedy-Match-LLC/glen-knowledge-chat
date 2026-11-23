"""Client photos everywhere (Glen, 2026-11-23).

"They put the photo into their portal. Then it shows in their portal and in our
Biofield Intake. Make it show on other pages or cards related to them in our console.
They could also upload it to chat, or attach to an email to us."

Covers: the intake form's photo upload, the console thumbnails, has_photo across two
merged addresses, and the Photo Inbox review list. Gmail is faked; no network, no mail.
"""
import base64
import io
import os
import re
import sqlite3
from pathlib import Path

os.environ.setdefault("OPENAI_API_KEY", "sk-dummy")
os.environ.setdefault("PINECONE_API_KEY", "pc-dummy")

import pytest

from dashboard import biofield_prereqs as bp
from dashboard import client_photos as cph
from dashboard import person_aliases as pal
from dashboard import photo_inbox as pin

STATIC = Path(__file__).resolve().parents[1] / "static"
JPG = b"\xff\xd8\xff\xe0" + b"j" * 20000
PNG = b"\x89PNG\r\n\x1a\n" + b"p" * 20000
WEBP = b"RIFF\x00\x00\x00\x00WEBP" + b"w" * 20000


@pytest.fixture
def env(monkeypatch, tmp_path):
    import app as appmod
    import dashboard as _dash
    monkeypatch.setattr(appmod, "LOG_DB", str(tmp_path / "chat_log.db"))
    monkeypatch.setattr(appmod, "send_evox_setup_link", lambda *a, **k: ("test", None))
    monkeypatch.setattr(appmod, "_trigger_document_extraction", lambda *_a: None)
    # A real gate: a secret is configured and the test client presents no key.
    monkeypatch.setattr(appmod, "CONSOLE_SECRET", "test-console-secret")
    monkeypatch.setattr(_dash, "CONSOLE_SECRET", "test-console-secret", raising=False)
    appmod.app.config["TESTING"] = True
    appmod._init_people_table()
    return appmod, appmod.app.test_client()


OWNER = {"X-Console-Key": "test-console-secret"}


def _cx(appmod):
    return sqlite3.connect(appmod.LOG_DB)


def _intake_token(client, email="pat@x.com"):
    r = client.post("/api/intake/public/start",
                    json={"name": "Pat Doe", "email": email, "tos_agreed": True})
    tok = r.get_json().get("token")
    assert tok, r.get_json()
    return tok


def _photo(client, tok, data=JPG, ctype="image/jpeg", name="me.jpg"):
    return client.post(f"/api/intake/public/photo?token={tok}",
                       data={"photo": (io.BytesIO(data), name, ctype)},
                       content_type="multipart/form-data")


# ── 1. Intake form upload ────────────────────────────────────────────────────

def test_intake_photo_is_stored_as_the_clients_own_and_counts_for_biofield(env):
    appmod, c = env
    tok = _intake_token(c)
    with _cx(appmod) as cx:
        assert bp.has_photo(cx, "pat@x.com") is False
    r = _photo(c, tok)
    assert r.status_code == 200 and r.get_json() == {"ok": True, "stored": True}
    assert b"base64" not in r.data and len(r.data) < 200      # never echoes the image
    with _cx(appmod) as cx:
        rec = cph.get(cx, "pat@x.com")
        assert rec["blob"] == JPG and rec["source"] == "portal-self"
        assert bp.has_photo(cx, "pat@x.com") is True


@pytest.mark.parametrize("data,ctype", [
    (JPG, "image/gif"),                    # type not allowed
    (b"GIF89a" + b"x" * 100, "image/jpeg"),  # declared jpeg, bytes are not
    (PNG, "image/jpeg"),                   # declared jpeg, bytes are png
    (b"<svg></svg>", "image/svg+xml"),
], ids=["gif-type", "gif-bytes", "png-bytes", "svg"])
def test_intake_photo_refuses_wrong_type_or_magic_bytes(env, data, ctype):
    appmod, c = env
    tok = _intake_token(c)
    assert _photo(c, tok, data, ctype).status_code == 400
    with _cx(appmod) as cx:
        assert cph.get(cx, "pat@x.com") is None


def test_intake_photo_refuses_oversize(env):
    appmod, c = env
    tok = _intake_token(c)
    big = b"\xff\xd8\xff" + b"x" * (appmod._PHOTO_MAX + 1)
    assert _photo(c, tok, big).status_code == 400
    with _cx(appmod) as cx:
        assert cph.get(cx, "pat@x.com") is None


def test_intake_photo_needs_a_valid_token(env):
    _a, c = env
    assert _photo(c, "not-a-token").status_code == 401


def test_intake_photo_is_capped_per_token(env):
    appmod, c = env
    tok = _intake_token(c)
    codes = [_photo(c, tok).status_code for _ in range(appmod._INTAKE_PHOTOS_PER_TOKEN + 1)]
    assert codes[:-1] == [200] * appmod._INTAKE_PHOTOS_PER_TOKEN and codes[-1] == 429


def test_a_new_intake_session_never_replaces_a_photo_already_on_file(env):
    appmod, c = env
    with _cx(appmod) as cx:
        cph.put(cx, "pat@x.com", PNG, "image/png", source="fmp")
    tok = _intake_token(c)
    for _ in range(2):                     # a retry must not get through either
        j = _photo(c, tok).get_json()
        assert j["ok"] and j["stored"] is False
    with _cx(appmod) as cx:
        assert cph.get(cx, "pat@x.com")["blob"] == PNG


def test_intake_page_offers_the_photo_button_in_plain_words():
    page = (STATIC / "begin-intake.html").read_text()
    assert "/api/intake/public/photo" in page and 'f.id==="portrait"' in page
    assert "Add a photo of your face" in page
    block = page[page.index("function photoUploader"):page.index("// ---- mode selection")]
    assert "—" not in block


# ── 2. Thumbnails ────────────────────────────────────────────────────────────

THUMB_PAGES = ["console-client.html", "console-crm.html", "console-members.html",
               "console-biofield-portal.html", "console-handoffs.html", "console-orders.html"]


def test_thumb_script_sends_the_key_in_a_header_never_a_url():
    js = (STATIC / "client-photo-thumb.js").read_text()
    code = re.sub(r"/\*.*?\*/", "", js, flags=re.S)
    assert '"X-Console-Key"' in code and "/api/console/client-photo?email=" in code
    assert "key=" not in code and "console_key=" not in code
    assert "MAX_IN_FLIGHT = 4" in code and "IntersectionObserver" in code


@pytest.mark.parametrize("page", THUMB_PAGES)
def test_thumb_script_is_on_every_listed_page_and_used(page):
    html = (STATIC / page).read_text()
    assert '<script src="/static/client-photo-thumb.js"></script>' in html
    assert "ClientPhotoThumb.html(" in html


def test_console_photo_read_still_needs_the_console_key(env):
    _a, c = env
    assert c.get("/api/console/client-photo?email=a@x.com").status_code == 401


# ── 3. Two accounts, one person ──────────────────────────────────────────────

def test_has_photo_sees_a_photo_under_a_merged_address(tmp_path):
    with sqlite3.connect(str(tmp_path / "a.db")) as cx:
        pal.init_tables(cx)
        pal.add_alias(cx, "old@x.com", "new@x.com", 1)
        pal.add_alias(cx, "older@x.com", "old@x.com", 2)      # a chain
        cx.commit()
        assert not bp.has_photo(cx, "new@x.com")
        cph.put(cx, "older@x.com", JPG, "image/jpeg", source="portal-self")
        for e in ("new@x.com", "old@x.com", "older@x.com", "NEW@x.com "):
            assert bp.has_photo(cx, e), e
        assert not bp.has_photo(cx, "stranger@x.com")
        assert pal.same_person_emails(cx, "new@x.com") == ["new@x.com", "old@x.com", "older@x.com"]
        assert pal.same_person_emails(cx, "older@x.com") == ["older@x.com", "new@x.com", "old@x.com"]


def test_has_photo_without_the_alias_table_still_answers(tmp_path):
    with sqlite3.connect(str(tmp_path / "b.db")) as cx:
        assert bp.has_photo(cx, "a@x.com") is False
        cph.put(cx, "a@x.com", JPG, "image/jpeg", source="portal-self")
        assert bp.has_photo(cx, "a@x.com") is True


# ── 4. Photo Inbox ───────────────────────────────────────────────────────────

NEW_ROUTES = [("GET", "/api/console/photo-inbox"),
              ("POST", "/api/console/photo-inbox/scan"),
              ("GET", "/api/console/photo-inbox/1/image"),
              ("POST", "/api/console/photo-inbox/1/use"),
              ("POST", "/api/console/photo-inbox/1/dismiss")]


@pytest.mark.parametrize("method,path", NEW_ROUTES)
def test_every_new_console_route_refuses_a_signed_out_request(env, method, path):
    _a, c = env
    r = c.open(path, method=method)
    assert r.status_code == 401, (path, r.status_code)


class _Exec:
    def __init__(self, value):
        self._v = value

    def execute(self):
        return self._v


class _Strict:
    """Records every method called. Anything but list/get raises."""
    ALLOWED = {"users", "messages", "list", "get", "attachments"}

    def __init__(self, calls, kind, data):
        self._calls, self._kind, self._data = calls, kind, data

    def __getattr__(self, name):
        self._calls.append(f"{self._kind}.{name}")
        if name not in self.ALLOWED:
            raise AssertionError(f"forbidden Gmail call: {self._kind}.{name}")
        if name == "users":
            return lambda: _Strict(self._calls, "users", self._data)
        if name == "messages":
            return lambda: _Strict(self._calls, "messages", self._data)
        if name == "attachments":
            return lambda: _Strict(self._calls, "attachments", self._data)
        if self._kind == "messages" and name == "list":
            return lambda **kw: _Exec({"messages": [{"id": m} for m in self._data["msgs"]]})
        if self._kind == "messages" and name == "get":
            return lambda **kw: _Exec(self._data["msgs"][kw["id"]])
        if self._kind == "attachments" and name == "get":
            return lambda **kw: _Exec({"data": base64.urlsafe_b64encode(
                self._data["att"][kw["id"]]).decode().rstrip("=")})
        raise AssertionError(name)


def _msg(sender, parts, ts=1763900000000):
    return {"internalDate": str(ts), "payload": {
        "headers": [{"name": "From", "value": sender}],
        "parts": [{"partId": str(i), "filename": fn, "mimeType": mt,
                   "body": {"attachmentId": aid, "size": size}}
                  for i, (fn, mt, aid, size) in enumerate(parts)]}}


def _gmail(calls):
    data = {"msgs": {
        "m1": _msg("Pat Doe <Pat@X.com>", [("face.jpg", "image/jpeg", "a1", len(JPG)),
                                           ("logo.png", "image/png", "a2", 900),
                                           ("labs.pdf", "application/pdf", "a3", 5000)]),
        "m2": _msg("stranger@y.com", [("pic.jpg", "image/jpeg", "a4", len(JPG))]),
        "m3": _msg("Buyer <buyer@x.com>", [("IMG_1.webp", "image/webp", "a5", len(WEBP))]),
    }, "att": {"a1": JPG, "a2": PNG[:900], "a4": JPG, "a5": WEBP}}
    return _Strict(calls, "svc", data)


@pytest.fixture
def scan_env(env, monkeypatch):
    appmod, c = env
    from dashboard import inbox
    calls = []
    monkeypatch.setattr(inbox, "_get_gmail_service", lambda: _gmail(calls))
    with _cx(appmod) as cx:
        cx.execute("INSERT INTO people(email, name) VALUES(?,?)", ("pat@x.com", "Pat Doe"))
        from dashboard import orders as O
        O.init_orders_table(cx)
        cx.execute("INSERT INTO orders(email, created_at, source, external_ref) VALUES(?,?,?,?)",
                   ("buyer@x.com", "2026-11-01", "manual", "t-1"))
        cx.commit()
    return appmod, c, calls


def test_scan_enqueues_known_clients_only_and_only_reads_gmail(scan_env):
    appmod, c, calls = scan_env
    r = c.post("/api/console/photo-inbox/scan", headers=OWNER, json={})
    j = r.get_json()
    assert r.status_code == 200 and j["ok"], j
    assert j["added"] == 2 and j["unknown_sender"] == 1 and j["skipped"] == 1
    methods = {x.split(".", 1)[1] for x in calls}
    assert methods <= {"users", "messages", "list", "get", "attachments"}, calls
    assert not any(m in x for x in calls for m in ("modify", "trash", "send", "batch"))
    items = c.get("/api/console/photo-inbox", headers=OWNER).get_json()["items"]
    assert sorted(i["email"] for i in items) == ["buyer@x.com", "pat@x.com"]
    assert {i["source"] for i in items} == {"email"}
    assert [i["name"] for i in items if i["email"] == "pat@x.com"] == ["Pat Doe"]
    with _cx(appmod) as cx:                  # nothing reached a client record yet
        assert cph.get(cx, "pat@x.com") is None


def test_rescan_never_duplicates(scan_env):
    _a, c, _calls = scan_env
    c.post("/api/console/photo-inbox/scan", headers=OWNER, json={})
    j = c.post("/api/console/photo-inbox/scan", headers=OWNER, json={}).get_json()
    assert j["added"] == 0 and j["already"] == 2
    assert len(c.get("/api/console/photo-inbox", headers=OWNER).get_json()["items"]) == 2


def test_enqueue_dedupes_on_source_and_ref(tmp_path):
    with sqlite3.connect(str(tmp_path / "c.db")) as cx:
        assert pin.enqueue(cx, "a@x.com", "email", "m1:0", JPG, "image/jpeg") is True
        assert pin.enqueue(cx, "a@x.com", "email", "m1:0", JPG, "image/jpeg") is False
        assert pin.enqueue(cx, "a@x.com", "portal-chat", "m1:0", JPG, "image/jpeg") is True
        assert pin.enqueue(cx, "a@x.com", "email", "m2:0", b"GIF89a", "image/jpeg") is False
        assert cx.execute("SELECT COUNT(*) FROM photo_inbox").fetchone()[0] == 2


def test_scan_without_gmail_says_so(env, monkeypatch):
    _a, c = env
    from dashboard import inbox

    def boom():
        raise RuntimeError("no token")
    monkeypatch.setattr(inbox, "_get_gmail_service", boom)
    r = c.post("/api/console/photo-inbox/scan", headers=OWNER, json={})
    assert r.status_code == 503 and "Gmail" in r.get_json()["error"]


def test_use_stores_the_photo_and_flips_status(scan_env):
    appmod, c, _calls = scan_env
    c.post("/api/console/photo-inbox/scan", headers=OWNER, json={})
    items = c.get("/api/console/photo-inbox", headers=OWNER).get_json()["items"]
    pat = next(i for i in items if i["email"] == "pat@x.com")
    img = c.get(f"/api/console/photo-inbox/{pat['id']}/image", headers=OWNER).get_json()
    assert base64.b64decode(img["image"]) == JPG
    r = c.post(f"/api/console/photo-inbox/{pat['id']}/use", headers=OWNER)
    assert r.status_code == 200 and r.get_json()["status"] == "used"
    with _cx(appmod) as cx:
        rec = cph.get(cx, "pat@x.com")
        assert rec["blob"] == JPG and rec["source"] == "portal-self"
        assert bp.has_photo(cx, "pat@x.com")
        row = pin.get(cx, pat["id"])
        assert row["status"] == "used" and row["decided_by_role"] == "owner"
    assert c.post(f"/api/console/photo-inbox/{pat['id']}/use", headers=OWNER).status_code == 409
    left = c.get("/api/console/photo-inbox", headers=OWNER).get_json()["items"]
    assert [i["email"] for i in left] == ["buyer@x.com"]


def test_dismiss_stores_nothing(scan_env):
    appmod, c, _calls = scan_env
    c.post("/api/console/photo-inbox/scan", headers=OWNER, json={})
    items = c.get("/api/console/photo-inbox", headers=OWNER).get_json()["items"]
    buyer = next(i for i in items if i["email"] == "buyer@x.com")
    assert c.post(f"/api/console/photo-inbox/{buyer['id']}/dismiss",
                  headers=OWNER).get_json()["status"] == "dismissed"
    with _cx(appmod) as cx:
        assert cph.get(cx, "buyer@x.com") is None


def test_a_chat_photo_also_waits_in_the_photo_inbox(env):
    appmod, c = env
    from dashboard import client_portal as _cp
    with _cx(appmod) as cx:
        _cp.init_client_portal_table(cx)
        tok, _ = _cp.upsert_portal(cx, "c@x.com", "Cee", {})
    r = c.post(f"/api/portal/{tok}/chat/upload",
               data={"file": (io.BytesIO(PNG), "me.png", "image/png")},
               content_type="multipart/form-data")
    assert r.status_code == 200 and r.get_json()["kind"] == "image"
    items = c.get("/api/console/photo-inbox", headers=OWNER).get_json()["items"]
    assert [(i["email"], i["source"], i["source_ref"]) for i in items] == [
        ("c@x.com", "portal-chat", f"doc:{r.get_json()['id']}")]
    pdf = c.post(f"/api/portal/{tok}/chat/upload",
                 data={"file": (io.BytesIO(b"%PDF-1.4 x"), "a.pdf", "application/pdf")},
                 content_type="multipart/form-data")
    assert pdf.status_code == 200
    assert len(c.get("/api/console/photo-inbox", headers=OWNER).get_json()["items"]) == 1


def test_a_broken_photo_inbox_never_fails_the_chat_upload(env, monkeypatch):
    appmod, c = env
    from dashboard import client_portal as _cp

    def boom(*a, **k):
        raise RuntimeError("inbox down")
    monkeypatch.setattr(pin, "enqueue", boom)
    with _cx(appmod) as cx:
        _cp.init_client_portal_table(cx)
        tok, _ = _cp.upsert_portal(cx, "c@x.com", "Cee", {})
    r = c.post(f"/api/portal/{tok}/chat/upload",
               data={"file": (io.BytesIO(JPG), "me.jpg", "image/jpeg")},
               content_type="multipart/form-data")
    assert r.status_code == 200 and r.get_json()["ok"]


def test_photo_inbox_page_and_nav_link():
    page = (STATIC / "console-photo-inbox.html").read_text()
    assert "Check email for photos" in page and "Use as photo" in page and "Dismiss" in page
    assert "/api/console/photo-inbox/scan" in page and "key=" not in page
    nav = (STATIC / "op-nav.js").read_text()
    assert '{id:"photo-inbox",label:"Photo Inbox",href:"/console/photo-inbox"+qs}' in nav


def test_photo_inbox_page_is_served(env):
    _a, c = env
    r = c.get("/console/photo-inbox")
    assert r.status_code == 200 and b"Photo Inbox" in r.data
