"""Story pages through the app: the flag, public routes, console preview, the gate
actions over /api/action, and click counting.

Imports app.py, so it runs only with the CI fake env (PINECONE_API_KEY etc.).
Every page here is a fixture. No real testimony is in the repo.
"""
import importlib
import sqlite3
import sys
from pathlib import Path

import pytest

RAE_TOKEN = "rae-token-0001"
OPS_TOKEN = "ops-token-0001"

CONTENT = {
    "story": "Fixture story, paragraph one.\n\nParagraph two.",
    "links": [{"label": "A product", "path": "/begin/product/neuro-magnesium"}],
}


def _load_app():
    repo_root = Path(__file__).resolve().parent.parent
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
    try:
        return importlib.import_module("app")
    except Exception as e:  # noqa: BLE001 - missing env -> CI only
        pytest.skip(f"app not importable in this env: {e}")


@pytest.fixture
def env(monkeypatch, tmp_path):
    a = _load_app()
    import dashboard
    if not dashboard.CONSOLE_SECRET:
        pytest.skip("needs CONSOLE_SECRET (fake) in the env")
    db = str(tmp_path / "chat_log.db")
    monkeypatch.setattr(a, "LOG_DB", db)
    monkeypatch.setenv("STORIES_ENABLED", "true")
    roles = {RAE_TOKEN: "owner", OPS_TOKEN: "ops"}
    monkeypatch.setattr(a, "_role_for_token", lambda t: roles.get(t))
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
    # Ops is refused by the action's permission.
    r = env.act("publish", env.ops, slug="jane-doe", content_hash=h)
    assert r["status"] == "denied"
    assert env.row("jane-doe")["state"] == "giver_approved"
    r = env.act("publish", env.glen, slug="jane-doe", content_hash=h)["result"]
    assert r["ok"] and r["state"] == "published"


def test_edit_after_publish_resets_and_takes_page_down(env):
    _to(env, "published")
    r = env.act("edit", env.rae, slug="jane-doe", name_line="Jane D.")["result"]
    assert r["ok"] and r["state"] == "draft"
    assert env.client.get("/stories/jane-doe").status_code == 404
    row = env.row("jane-doe")
    assert row["published_at"] == "" and row["compliance_at"] == "" and row["content_hash"] == ""


def test_stale_hash_refused_over_actions(env):
    h = _create(env)
    env.act("edit", env.rae, slug="jane-doe", content={"story": "New words.", "links": []})
    r = env.act("check", env.ops, slug="jane-doe", note="ok", content_hash=h)["result"]
    assert not r["ok"] and r["error"] == "hash_mismatch"


# ── click counting ───────────────────────────────────────────────────────────

def _clicks(env):
    c = sqlite3.connect(env.db_path)
    try:
        return c.execute("SELECT story_slug, target_path, session_hash FROM story_clicks").fetchall()
    finally:
        c.close()


def test_click_recorded_once_per_session(env):
    _to(env, "published")
    r = env.client.get("/begin/quiz?story=jane-doe")
    assert r.status_code == 200
    rows = _clicks(env)
    assert len(rows) == 1 and rows[0][:2] == ("jane-doe", "/begin/quiz")
    # The test client keeps the amg_session cookie the hook set, so a reload is the same visitor.
    env.client.get("/begin/quiz?story=jane-doe")
    assert len(_clicks(env)) == 1
    sess = env.client.get_cookie("amg_session").value
    assert sess not in rows[0][2]          # only a hash is stored
    other = env.app.app.test_client()
    other.get("/begin/quiz?story=jane-doe")
    assert len(_clicks(env)) == 2


def test_click_reuses_a_session_the_page_sets(env):
    """'/' sets amg_session itself. The hook must count that session, not mint a second."""
    _to(env, "published")
    r = env.client.get("/?story=jane-doe")
    cookies = [h for h in r.headers.getlist("Set-Cookie") if h.startswith("amg_session=")]
    assert len(cookies) == 1
    assert len(_clicks(env)) == 1


@pytest.mark.parametrize("state", ["draft", "checked", "giver_approved"])
def test_click_not_recorded_for_unpublished(env, state):
    _to(env, state)
    env.client.get("/begin/quiz?story=jane-doe")
    assert _clicks(env) == []


def test_click_not_recorded_with_flag_off_or_bad_slug_or_bot(env, monkeypatch):
    _to(env, "published")
    env.client.get("/begin/quiz?story=Bad%20Slug")
    env.client.get("/begin/quiz?story=jane-doe", headers={"User-Agent": "Googlebot/2.1"})
    monkeypatch.delenv("STORIES_ENABLED", raising=False)
    env.client.get("/begin/quiz?story=jane-doe")
    assert _clicks(env) == []


def test_ref_persisted_only_when_it_is_the_storys_own(env):
    _to(env, "published", ref_slug="jane")
    r = env.client.get("/begin/quiz?ref=jane&story=jane-doe")
    assert any(h.startswith("rm_ref=jane") for h in r.headers.getlist("Set-Cookie"))
    # Another APPROVED affiliate's slug on this story's link is not credited.
    c = sqlite3.connect(env.db_path)
    c.execute("INSERT INTO affiliate_signups (name, email, slug, status) "
              "VALUES ('Other', 'o@example.com', 'other-aff', 'approved')")
    c.commit()
    c.close()
    other = env.app.app.test_client()
    r = other.get("/begin/quiz?ref=other-aff&story=jane-doe")
    assert not any(h.startswith("rm_ref=") for h in r.headers.getlist("Set-Cookie"))
    # The story's own slug is not credited unless it is an approved affiliate.
    c = sqlite3.connect(env.db_path)
    c.execute("UPDATE affiliate_signups SET status='pending' WHERE slug='jane'")
    c.commit()
    c.close()
    third = env.app.app.test_client()
    r = third.get("/begin/quiz?ref=jane&story=jane-doe")
    assert not any(h.startswith("rm_ref=") for h in r.headers.getlist("Set-Cookie"))


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
