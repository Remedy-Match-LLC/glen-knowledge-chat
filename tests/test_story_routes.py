"""Story pages through the app: the flag, public routes, console preview, the gate
actions over /api/action, and click counting.

Imports app.py with the CI fake env (PINECONE_API_KEY etc.). An import failure is a
real failure here, never a skip.
Every page here is a fixture. No real testimony is in the repo.
"""
import importlib
import sqlite3
import sys
from pathlib import Path

import pytest

RAE_TOKEN = "tok-r-0001-zzzz"
OPS_TOKEN = "tok-o-0001-zzzz"
# A per-user token whose workspace user is literally named "owner": owner role, but
# not the master key, so it must not be able to publish.
NAMED_OWNER_TOKEN = "tok-n-0001-zzzz"

CONTENT = {
    "story": "Fixture story, paragraph one.\n\nParagraph two.",
    "links": [{"label": "A product", "path": "/begin/product/neuro-magnesium"},
              {"label": "The quiz", "path": "/begin/quiz"}],
}


def _load_app():
    repo_root = Path(__file__).resolve().parent.parent
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
    return importlib.import_module("app")


@pytest.fixture
def env(monkeypatch, tmp_path):
    a = _load_app()
    import dashboard
    if not dashboard.CONSOLE_SECRET:
        pytest.skip("needs CONSOLE_SECRET (fake) in the env")
    db = str(tmp_path / "chat_log.db")
    monkeypatch.setattr(a, "LOG_DB", db)
    monkeypatch.setenv("STORIES_ENABLED", "true")
    # Real token resolution for Rae (workspace_users + access_tokens, as _auth reads
    # them). No token scope maps to ops, so only the ops token's ROLE is patched; its
    # NAME still comes from the real lookup.
    real_role = a._role_for_token
    monkeypatch.setattr(a, "_role_for_token",
                        lambda t: "ops" if t == OPS_TOKEN else real_role(t))
    from dashboard import events as E
    from dashboard import product_reviews as pr
    from dashboard import story_pages as sp
    cx = sqlite3.connect(db)
    E.init_event_tables(cx)
    pr.init_table(cx)
    sp.init_table(cx)
    cur = cx.execute(
        "INSERT INTO product_reviews (product_slug, email, name, rating, body, kind, "
        "consent_public, consent_ref, status) VALUES "
        "('t1', 'giver@example.com', 'Jane Doe', 5, ?, 'testimonial', 1, 'msg-1', 'approved')",
        (CONTENT["story"],))
    tid = cur.lastrowid
    cx.execute("CREATE TABLE IF NOT EXISTS affiliate_signups (id INTEGER PRIMARY KEY "
               "AUTOINCREMENT, name TEXT, email TEXT, slug TEXT UNIQUE, status TEXT)")
    cx.execute("INSERT INTO affiliate_signups (name, email, slug, status) "
               "VALUES ('Jane', 'giver@example.com', 'jane', 'approved')")
    cx.execute("CREATE TABLE workspace_users (id INTEGER PRIMARY KEY AUTOINCREMENT, "
               "name TEXT NOT NULL UNIQUE, display_name TEXT DEFAULT '', scope TEXT NOT NULL)")
    cx.execute("CREATE TABLE access_tokens (token TEXT PRIMARY KEY, user_id INTEGER NOT NULL, "
               "last_used_at TEXT, revoked_at TEXT)")
    cx.execute("INSERT INTO workspace_users (id, name, scope) VALUES (1, 'rae', 'workspace:rae')")
    cx.execute("INSERT INTO workspace_users (id, name, scope) VALUES (2, 'opsperson', "
               "'workspace:opsperson')")
    cx.execute("INSERT INTO access_tokens (token, user_id) VALUES (?, 1)", (RAE_TOKEN,))
    cx.execute("INSERT INTO access_tokens (token, user_id) VALUES (?, 2)", (OPS_TOKEN,))
    cx.execute("INSERT INTO workspace_users (id, name, scope) VALUES (3, 'owner', 'workspace:glen')")
    cx.execute("INSERT INTO access_tokens (token, user_id) VALUES (?, 3)", (NAMED_OWNER_TOKEN,))
    cx.commit()
    cx.close()

    class Env:
        app = a
        client = a.app.test_client()
        glen = {"X-Console-Key": dashboard.CONSOLE_SECRET}
        rae = {"X-Console-Key": RAE_TOKEN}
        ops = {"X-Console-Key": OPS_TOKEN}
        testimonial_id = tid
        db_path = db

        def act(self, key, headers, **body):
            r = self.client.post(f"/api/action/story_page.{key}", json=body, headers=headers)
            assert r.status_code == 200, r.data
            return r.get_json()

        def row(self, slug):
            c = sqlite3.connect(self.db_path)
            try:
                return sp.get(c, slug)
            finally:
                c.close()

    return Env()


def _create(env, slug="jane-doe", ref_slug=""):
    res = env.act("create", env.rae, slug=slug, testimonial_id=env.testimonial_id,
                  content=CONTENT, name_line="Jane Doe, Teacher", ref_slug=ref_slug)
    assert res["status"] == "done" and res["result"]["ok"], res
    return res["result"]["content_hash"]


def _to(env, state, slug="jane-doe", ref_slug=""):
    h = _create(env, slug, ref_slug)
    if state == "draft":
        return h
    r = env.act("check", env.ops, slug=slug, note="clean", content_hash=h)["result"]
    assert r["ok"] and r["state"] == "checked", r
    if state == "checked":
        return h
    r = env.act("giver_approve", env.rae, slug=slug, consent_ref="gmail:1", content_hash=h)["result"]
    assert r["ok"] and r["state"] == "giver_approved", r
    if state == "giver_approved":
        return h
    r = env.act("publish", env.glen, slug=slug, content_hash=h)["result"]
    assert r["ok"] and r["state"] == "published", r
    return h


# ── flag and public routes ───────────────────────────────────────────────────

def test_flag_off_404_everywhere(env, monkeypatch):
    _to(env, "published")
    monkeypatch.delenv("STORIES_ENABLED", raising=False)
    for path in ("/stories", "/stories/sitemap.xml", "/stories/jane-doe"):
        assert env.client.get(path).status_code == 404, path
    monkeypatch.setenv("STORIES_ENABLED", "true")
    for path in ("/stories", "/stories/sitemap.xml", "/stories/jane-doe"):
        assert env.client.get(path).status_code == 200, path


@pytest.mark.parametrize("state", ["draft", "checked", "giver_approved"])
def test_unpublished_states_are_404(env, state):
    _to(env, state)
    assert env.client.get("/stories/jane-doe").status_code == 404
    assert b"jane-doe" not in env.client.get("/stories").data
    assert b"jane-doe" not in env.client.get("/stories/sitemap.xml").data


def test_published_is_200_with_words_and_fda(env):
    _to(env, "published", ref_slug="jane")
    r = env.client.get("/stories/jane-doe")
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "Fixture story, paragraph one." in body and "Jane Doe, Teacher" in body
    assert "not been evaluated by the Food and Drug Administration" in body
    assert "/begin/product/neuro-magnesium?ref=jane&amp;story=jane-doe" in body
    assert "not published" not in body


def test_unknown_slug_404(env):
    assert env.client.get("/stories/nobody-here").status_code == 404
    assert env.client.get("/stories/Bad..Slug").status_code == 404


def test_withdraw_takes_page_down(env):
    _to(env, "published")
    r = env.act("withdraw", env.ops, slug="jane-doe")["result"]
    assert r["ok"] and r["state"] == "withdrawn"
    assert env.client.get("/stories/jane-doe").status_code == 404
    assert b"jane-doe" not in env.client.get("/stories").data


def test_index_lists_only_published(env):
    _to(env, "published", slug="pub-one")
    _create(env, slug="draft-one")
    body = env.client.get("/stories").get_data(as_text=True)
    assert "/stories/pub-one" in body and "/stories/draft-one" not in body
    xml = env.client.get("/stories/sitemap.xml").get_data(as_text=True)
    assert "/stories/pub-one" in xml and "/stories/draft-one" not in xml


# ── console ──────────────────────────────────────────────────────────────────

def test_console_preview_any_state(env, monkeypatch):
    _create(env)
    monkeypatch.delenv("STORIES_ENABLED", raising=False)   # preview works before launch
    r = env.client.get("/console/stories/jane-doe/preview", headers=env.glen)
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "not published" in body and "noindex" in body
    assert "Fixture story, paragraph one." in body
    assert env.client.get("/console/stories/jane-doe/preview").status_code == 401
    assert env.client.get("/console/stories/jane-doe/preview",
                          headers={"X-Console-Key": "wrong"}).status_code == 401


def test_console_page_serves(env):
    r = env.client.get("/console/stories")
    assert r.status_code == 200 and b"/api/console/stories" in r.data


def test_console_list_requires_key(env):
    _create(env)
    assert env.client.get("/api/console/stories").status_code == 401
    r = env.client.get("/api/console/stories", headers=env.ops)
    assert r.status_code == 200
    assert [p["story_slug"] for p in r.get_json()["pages"]] == ["jane-doe"]


def test_actions_refuse_without_a_key(env):
    r = env.client.post("/api/action/story_page.create", json={"slug": "x-y"})
    assert r.status_code == 401


def test_gate_order_enforced_over_actions(env):
    h = _create(env)
    r = env.act("publish", env.glen, slug="jane-doe", content_hash=h)["result"]
    assert not r["ok"] and r["error"] == "wrong_state"
    r = env.act("giver_approve", env.rae, slug="jane-doe", consent_ref="c", content_hash=h)["result"]
    assert not r["ok"] and r["error"] == "wrong_state"
    assert env.row("jane-doe")["state"] == "draft"


def test_publish_is_glen_only(env):
    h = _to(env, "giver_approved")
    # Rae's token carries the owner role, but step 3 is Glen's.
    r = env.act("publish", env.rae, slug="jane-doe", content_hash=h)
    assert r["status"] == "done" and r["result"]["error"] == "owner_only"
    # A token user NAMED "owner" is still not the master key.
    r = env.act("publish", {"X-Console-Key": NAMED_OWNER_TOKEN}, slug="jane-doe", content_hash=h)
    assert r["status"] == "done" and r["result"]["error"] == "owner_only"
    # Ops is refused by the action's permission.
    r = env.act("publish", env.ops, slug="jane-doe", content_hash=h)
    assert r["status"] == "denied"
    assert env.row("jane-doe")["state"] == "giver_approved"
    r = env.act("publish", env.glen, slug="jane-doe", content_hash=h)["result"]
    assert r["ok"] and r["state"] == "published"


def test_edit_after_publish_resets_and_takes_page_down(env):
    h = _to(env, "published")
    r = env.act("edit", env.rae, slug="jane-doe", name_line="Jane D.", content_hash=h)["result"]
    assert r["ok"] and r["state"] == "draft"
    assert env.client.get("/stories/jane-doe").status_code == 404
    row = env.row("jane-doe")
    assert row["published_at"] == "" and row["compliance_at"] == "" and row["content_hash"] == ""


def test_stale_hash_refused_over_actions(env):
    h = _create(env)
    env.act("edit", env.rae, slug="jane-doe", content={"story": "New words.", "links": []},
            content_hash=h)
    r = env.act("check", env.ops, slug="jane-doe", note="ok", content_hash=h)["result"]
    assert not r["ok"] and r["error"] == "hash_mismatch"


# ── click counting ───────────────────────────────────────────────────────────

def _clicks(env):
    c = sqlite3.connect(env.db_path)
    try:
        return c.execute("SELECT story_slug, target_path, session_hash FROM story_clicks").fetchall()
    finally:
        c.close()


def _cookie_client(env, session="sess-aaaa-1111"):
    c = env.app.app.test_client()
    c.set_cookie("amg_session", session)
    return c


def test_click_recorded_once_per_session_on_a_stored_link(env):
    _to(env, "published")
    c = _cookie_client(env)
    assert c.get("/begin/quiz?story=jane-doe").status_code == 200
    rows = _clicks(env)
    assert len(rows) == 1 and rows[0][:2] == ("jane-doe", "/begin/quiz")
    assert "sess-aaaa-1111" not in rows[0][2]          # only a hash is stored
    c.get("/begin/quiz?story=jane-doe&utm_source=x&email=a@b.c")
    assert len(_clicks(env)) == 1                       # same session, same link
    _cookie_client(env, "sess-bbbb-2222").get("/begin/quiz?story=jane-doe")
    assert len(_clicks(env)) == 2
    stored = " ".join(" ".join(r) for r in _clicks(env))
    assert "utm_source" not in stored and "a@b.c" not in stored and "?" not in stored


def test_click_only_on_a_path_the_story_links_to(env):
    """A token-bearing path, or any path the story does not link to, stores nothing."""
    _to(env, "published")
    c = _cookie_client(env)
    c.get("/portal/secret-token-abc123?story=jane-doe")
    c.get("/begin/explore?story=jane-doe")
    c.get("/begin/quiz/extra?story=jane-doe")
    assert _clicks(env) == []


def test_cookieless_requests_store_nothing_and_mint_no_session(env):
    """/begin/quiz mints its own amg_session for a new visitor. The hook must not add
    one, and must not count the visit: the visitor had no session when it arrived."""
    _to(env, "published")

    def amg(r):
        return [h for h in r.headers.getlist("Set-Cookie") if h.startswith("amg_session=")]

    base = amg(env.app.app.test_client().get("/begin/quiz"))
    for _ in range(40):
        r = env.app.app.test_client().get("/begin/quiz?story=jane-doe")   # cookieless each time
        assert len(amg(r)) == len(base) <= 1
    assert _clicks(env) == []


@pytest.mark.parametrize("state", ["draft", "checked", "giver_approved"])
def test_click_not_recorded_for_unpublished(env, state):
    _to(env, state)
    _cookie_client(env).get("/begin/quiz?story=jane-doe")
    assert _clicks(env) == []


def test_click_not_recorded_with_flag_off_or_bad_slug_or_bot(env, monkeypatch):
    _to(env, "published")
    c = _cookie_client(env)
    c.get("/begin/quiz?story=Bad%20Slug")
    c.get("/begin/quiz?story=jane-doe", headers={"User-Agent": "Googlebot/2.1"})
    monkeypatch.delenv("STORIES_ENABLED", raising=False)
    c.get("/begin/quiz?story=jane-doe")
    assert _clicks(env) == []


def _rm_ref(r):
    return [h.split(";", 1)[0] for h in r.headers.getlist("Set-Cookie") if h.startswith("rm_ref=")]


def _add_affiliate(env, slug, status="approved"):
    c = sqlite3.connect(env.db_path)
    c.execute("INSERT INTO affiliate_signups (name, email, slug, status) VALUES (?, ?, ?, ?)",
              (slug, f"{slug}@example.com", slug, status))
    c.commit()
    c.close()


def test_ref_handler_baseline_sets_rm_ref(env):
    """/begin/explore consumes ?ref= itself. Without ?story= that is unchanged."""
    _add_affiliate(env, "other-aff")
    r = env.app.app.test_client().get("/begin/explore?ref=other-aff")
    assert _rm_ref(r) == ["rm_ref=other-aff"]


@pytest.mark.parametrize("state", ["published", "draft"])
def test_foreign_ref_on_a_story_link_cannot_set_rm_ref(env, state):
    """Another affiliate's ref on a story link: the handler's own rm_ref is removed."""
    _to(env, state, ref_slug="jane")
    _add_affiliate(env, "other-aff")
    for path in ("/begin/explore", "/begin/quiz"):
        r = env.app.app.test_client().get(f"{path}?ref=other-aff&story=jane-doe")
        assert _rm_ref(r) == [], path
        r = env.app.app.test_client().get(f"{path}?ref=other-aff&story=no-such-story")
        assert _rm_ref(r) == [], path


def test_storys_own_ref_must_be_an_approved_affiliate(env):
    _to(env, "published", ref_slug="jane")
    r = env.app.app.test_client().get("/begin/explore?ref=jane&story=jane-doe")
    assert _rm_ref(r) == ["rm_ref=jane"]
    r = env.app.app.test_client().get("/begin/quiz?ref=jane&story=jane-doe")
    assert _rm_ref(r) == ["rm_ref=jane"]               # credited by the hook itself
    c = sqlite3.connect(env.db_path)
    c.execute("UPDATE affiliate_signups SET status='pending' WHERE slug='jane'")
    c.commit()
    c.close()
    r = env.app.app.test_client().get("/begin/explore?ref=jane&story=jane-doe")
    assert _rm_ref(r) == []


def test_story_without_ref_slug_credits_nobody(env):
    _to(env, "published", ref_slug="")
    _add_affiliate(env, "other-aff")
    r = env.app.app.test_client().get("/begin/explore?ref=other-aff&story=jane-doe")
    assert _rm_ref(r) == []


# ── consent, stale edits, actor names ────────────────────────────────────────

def _consent(env, consent=None, status=None):
    c = sqlite3.connect(env.db_path)
    if consent is not None:
        c.execute("UPDATE product_reviews SET consent_public=? WHERE id=?", (consent, env.testimonial_id))
    if status is not None:
        c.execute("UPDATE product_reviews SET status=? WHERE id=?", (status, env.testimonial_id))
    c.commit()
    c.close()


@pytest.mark.parametrize("revoke", [{"consent": 0}, {"status": "withdrawn"}, {"status": "rejected"}])
def test_revoked_consent_404s_page_index_and_sitemap(env, revoke):
    _to(env, "published")
    assert env.client.get("/stories/jane-doe").status_code == 200
    _consent(env, **revoke)
    assert env.client.get("/stories/jane-doe").status_code == 404
    assert b"jane-doe" not in env.client.get("/stories").data
    assert b"jane-doe" not in env.client.get("/stories/sitemap.xml").data
    _cookie_client(env).get("/begin/quiz?story=jane-doe")
    assert _clicks(env) == []


def test_publish_refused_when_consent_revoked(env):
    h = _to(env, "giver_approved")
    _consent(env, consent=0)
    r = env.act("publish", env.glen, slug="jane-doe", content_hash=h)["result"]
    assert not r["ok"] and r["error"] == "no_consent"


def test_two_sequential_stale_saves(env):
    loaded = _create(env)
    r = env.act("edit", env.rae, slug="jane-doe", name_line="Jane D., Teacher",
                content_hash=loaded)["result"]
    assert r["ok"]
    r = env.act("edit", env.ops, slug="jane-doe", name_line="Jane D., Nurse",
                content_hash=loaded)["result"]
    assert not r["ok"] and r["error"] == "conflict"
    assert env.row("jane-doe")["name_line"] == "Jane D., Teacher"
    r = env.act("edit", env.ops, slug="jane-doe", name_line="Jane D., Nurse")["result"]
    assert not r["ok"] and r["error"] == "conflict"     # no hash at all is stale too


def test_stamps_and_audit_carry_person_names(env):
    h = _create(env)
    env.act("check", env.rae, slug="jane-doe", note="clean", content_hash=h)
    env.act("giver_approve", env.ops, slug="jane-doe", consent_ref="gmail:1", content_hash=h)
    env.act("publish", env.glen, slug="jane-doe", content_hash=h)
    env.act("withdraw", env.rae, slug="jane-doe")
    row = env.row("jane-doe")
    assert row["compliance_by"] == "rae"
    assert row["giver_approved_by"] == "opsperson"
    assert row["published_by"] == "glen"
    assert row["withdrawn_by"] == "rae"
    c = sqlite3.connect(env.db_path)
    actors = {r[0] for r in c.execute(
        "SELECT actor FROM events WHERE action_key LIKE 'story_page.%'")}
    c.close()
    assert actors == {"rae", "opsperson", "owner"}
    assert RAE_TOKEN[:8] not in actors and OPS_TOKEN[:8] not in actors


# ── route namespace ──────────────────────────────────────────────────────────

def test_stories_not_shadowed_by_portal_catch_all(env, monkeypatch):
    """On the portal host /<slug> is the practitioner catch-all. /stories must still
    be the stories index there, and 'stories' must be a reserved first segment."""
    from dashboard import practitioner_slugs as ps
    assert "stories" in ps.route_segments(env.app.app.url_map)
    _to(env, "published")
    monkeypatch.setattr(env.app, "_on_portal_host", lambda: True)
    r = env.client.get("/stories")
    assert r.status_code == 200 and b"/stories/jane-doe" in r.data
    adapter = env.app.app.url_map.bind("myhealingoasis.com")
    assert adapter.match("/stories")[0] == "stories_index"
    assert adapter.match("/stories/jane-doe")[0] == "story_page"


def test_ref_slug_pattern_matches_app():
    a = _load_app()
    from dashboard import story_pages as sp
    assert sp.REF_SLUG_RE.pattern == a._REF_SLUG_RE.pattern
