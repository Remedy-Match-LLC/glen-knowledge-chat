"""Story pages: store, three-step gate, rendering and click counting.

Pure-module tests: they do not import app.py, so they boot no Flask app and send
no email. Route tests live in tests/test_story_routes.py.
"""
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from dashboard import product_reviews as pr  # noqa: E402
from dashboard import story_pages as sp  # noqa: E402
from dashboard import story_render as sr  # noqa: E402

CONTENT = {
    "story": "First paragraph of my story.\n\nSecond paragraph.",
    "links": [{"label": "A product page", "path": "/begin/product/neuro-magnesium"}],
}


def _testimonial(cx, *, consent=1, kind="testimonial", name="Jane Doe", body=None):
    pr.init_table(cx)
    cur = cx.execute(
        "INSERT INTO product_reviews (product_slug, email, name, rating, body, kind, "
        "consent_public, consent_ref, compliance_score, status) "
        "VALUES (?, ?, ?, 5, ?, ?, ?, 'msg-1', 90, 'approved')",
        (f"t-{name}", f"{name.replace(' ', '')}@example.com", name,
         body if body is not None else CONTENT["story"], kind, consent))
    cx.commit()
    return cur.lastrowid


@pytest.fixture
def cx(tmp_path):
    c = sqlite3.connect(str(tmp_path / "s.db"))
    sp.init_table(c)
    yield c
    c.close()


def _draft(cx, slug="jane-doe", **kw):
    tid = kw.pop("testimonial_id", None) or _testimonial(cx)
    return sp.create(cx, slug, testimonial_id=tid, content=kw.pop("content", CONTENT), **kw)


def _publish(cx, slug="jane-doe", **kw):
    p = _draft(cx, slug, **kw)
    p = sp.mark_checked(cx, slug, by="marketing", note="clean", content_hash=p["current_hash"])
    p = sp.mark_giver_approved(cx, slug, by="rae", consent_ref="gmail:abc",
                               content_hash=p["content_hash"])
    return sp.mark_published(cx, slug, by="glen", content_hash=p["content_hash"])


# ── store and gate ───────────────────────────────────────────────────────────

def test_create_defaults(cx):
    p = _draft(cx)
    assert p["state"] == "draft"
    assert p["name_line"] == "Jane D."           # first name plus last initial
    assert p["content"] == CONTENT
    assert p["content_hash"] == ""


def test_create_refuses_missing_or_non_testimonial_row(cx):
    with pytest.raises(sp.StoryError) as e:
        sp.create(cx, "x-y", testimonial_id=999, content=CONTENT)
    assert e.value.code == "testimonial_not_found"
    tid = _testimonial(cx, kind="product", name="Prod Uct")
    with pytest.raises(sp.StoryError) as e:
        sp.create(cx, "x-y", testimonial_id=tid, content=CONTENT)
    assert e.value.code == "testimonial_not_found"


def test_create_never_writes_product_reviews(cx):
    tid = _testimonial(cx)
    before = cx.execute("SELECT * FROM product_reviews").fetchall()
    p = sp.create(cx, "jane-doe", testimonial_id=tid, content=CONTENT)
    sp.mark_checked(cx, "jane-doe", by="m", note="ok", content_hash=p["current_hash"])
    assert cx.execute("SELECT * FROM product_reviews").fetchall() == before


@pytest.mark.parametrize("slug", ["Jane", "jane--doe", "-jane", "sitemap.xml", "a/b", ""])
def test_create_rejects_bad_slug(cx, slug):
    with pytest.raises(sp.StoryError) as e:
        sp.create(cx, slug, testimonial_id=_testimonial(cx), content=CONTENT)
    assert e.value.code == "bad_slug"


def test_create_rejects_bad_link_and_bad_ref(cx):
    tid = _testimonial(cx)
    bad = {"story": "x", "links": [{"label": "l", "path": "https://evil.example/"}]}
    with pytest.raises(sp.StoryError):
        sp.create(cx, "a-b", testimonial_id=tid, content=bad)
    bad2 = {"story": "x", "links": [{"label": "l", "path": "//evil.example/x"}]}
    with pytest.raises(sp.StoryError):
        sp.create(cx, "a-b", testimonial_id=tid, content=bad2)
    with pytest.raises(sp.StoryError) as e:
        sp.create(cx, "a-b", testimonial_id=tid, content=CONTENT, ref_slug="bad slug!")
    assert e.value.code == "bad_ref_slug"


def test_full_gate_publishes(cx):
    p = _publish(cx)
    assert p["state"] == "published"
    assert p["compliance_by"] == "marketing" and p["compliance_note"] == "clean"
    assert p["giver_consent_ref"] == "gmail:abc" and p["giver_approved_by"] == "rae"
    assert p["published_by"] == "glen" and p["published_at"]
    assert p["content_hash"] == p["current_hash"]
    assert sp.is_public(p)


@pytest.mark.parametrize("state", ["draft", "checked", "giver_approved", "withdrawn"])
def test_only_published_is_public(cx, state):
    p = _draft(cx)
    if state in ("checked", "giver_approved"):
        p = sp.mark_checked(cx, "jane-doe", by="m", note="ok", content_hash=p["current_hash"])
    if state == "giver_approved":
        p = sp.mark_giver_approved(cx, "jane-doe", by="r", consent_ref="c",
                                   content_hash=p["content_hash"])
    if state == "withdrawn":
        p = sp.withdraw(cx, "jane-doe", by="r")
    assert p["state"] == state
    assert not sp.is_public(p)
    assert sp.list_published(cx) == []


def test_gate_order_enforced(cx):
    p = _draft(cx)
    h = p["current_hash"]
    with pytest.raises(sp.StoryError) as e:
        sp.mark_giver_approved(cx, "jane-doe", by="r", consent_ref="c", content_hash=h)
    assert e.value.code == "wrong_state"
    with pytest.raises(sp.StoryError) as e:
        sp.mark_published(cx, "jane-doe", by="glen", content_hash=h)
    assert e.value.code == "wrong_state"
    sp.mark_checked(cx, "jane-doe", by="m", note="ok", content_hash=h)
    with pytest.raises(sp.StoryError) as e:
        sp.mark_published(cx, "jane-doe", by="glen", content_hash=h)
    assert e.value.code == "wrong_state"
    with pytest.raises(sp.StoryError) as e:   # step 1 cannot run twice
        sp.mark_checked(cx, "jane-doe", by="m", note="ok", content_hash=h)
    assert e.value.code == "wrong_state"


def test_step_requirements(cx):
    p = _draft(cx)
    h = p["current_hash"]
    with pytest.raises(sp.StoryError) as e:
        sp.mark_checked(cx, "jane-doe", by="m", note="  ", content_hash=h)
    assert e.value.code == "missing_note"
    sp.mark_checked(cx, "jane-doe", by="m", note="ok", content_hash=h)
    with pytest.raises(sp.StoryError) as e:
        sp.mark_giver_approved(cx, "jane-doe", by="r", consent_ref="", content_hash=h)
    assert e.value.code == "missing_consent_ref"


def test_step1_needs_public_consent(cx):
    tid = _testimonial(cx, consent=0)
    p = sp.create(cx, "jane-doe", testimonial_id=tid, content=CONTENT)
    with pytest.raises(sp.StoryError) as e:
        sp.mark_checked(cx, "jane-doe", by="m", note="ok", content_hash=p["current_hash"])
    assert e.value.code == "no_consent"


@pytest.mark.parametrize("after", ["checked", "giver_approved", "published"])
@pytest.mark.parametrize("field", ["content", "name_line", "ref_slug", "testimonial_id"])
def test_edit_after_any_step_resets_to_draft(cx, after, field):
    p = _draft(cx)
    other_tid = _testimonial(cx, name="Other Giver")
    p = sp.mark_checked(cx, "jane-doe", by="m", note="ok", content_hash=p["current_hash"])
    if after in ("giver_approved", "published"):
        p = sp.mark_giver_approved(cx, "jane-doe", by="r", consent_ref="c",
                                   content_hash=p["content_hash"])
    if after == "published":
        p = sp.mark_published(cx, "jane-doe", by="glen", content_hash=p["content_hash"])
    assert p["state"] == after
    change = {"content": {"story": "Changed words.", "links": []},
              "name_line": "Jane Doe, Teacher", "ref_slug": "jane",
              "testimonial_id": other_tid}[field]
    p = sp.update(cx, "jane-doe", expected_hash=p["current_hash"], **{field: change})
    assert p["state"] == "draft"
    for col in ("compliance_at", "compliance_by", "compliance_note", "giver_approved_at",
                "giver_consent_ref", "published_at", "published_by", "content_hash"):
        assert p[col] == "", col
    assert not sp.is_public(p)


def test_edit_with_no_change_is_a_noop(cx):
    p = _draft(cx)
    p = sp.mark_checked(cx, "jane-doe", by="m", note="ok", content_hash=p["current_hash"])
    p = sp.update(cx, "jane-doe", expected_hash=p["current_hash"], content=CONTENT,
                  name_line=p["name_line"])
    assert p["state"] == "checked"


def test_step1_refuses_a_hash_the_reviewer_did_not_see(cx):
    _draft(cx)
    with pytest.raises(sp.StoryError) as e:
        sp.mark_checked(cx, "jane-doe", by="m", note="ok", content_hash="0" * 64)
    assert e.value.code == "hash_mismatch"


def _tamper(cx, slug="jane-doe"):
    """Change the stored content WITHOUT going through update(), so the state stays
    where it was. Only the stored-hash check can catch this."""
    cx.execute("UPDATE story_pages SET content_json=? WHERE story_slug=?",
               (json.dumps({"story": "Tampered.", "links": []}), slug))
    cx.commit()


def test_step2_refuses_when_content_changed_since_step1(cx):
    p = _draft(cx)
    p = sp.mark_checked(cx, "jane-doe", by="m", note="ok", content_hash=p["current_hash"])
    old = p["content_hash"]
    _tamper(cx)
    with pytest.raises(sp.StoryError) as e:
        sp.mark_giver_approved(cx, "jane-doe", by="r", consent_ref="c", content_hash=old)
    assert e.value.code == "hash_mismatch"
    # Even presenting the tampered page's own hash does not pass: step 1 never saw it.
    cur = sp.get(cx, "jane-doe")["current_hash"]
    with pytest.raises(sp.StoryError) as e:
        sp.mark_giver_approved(cx, "jane-doe", by="r", consent_ref="c", content_hash=cur)
    assert e.value.code == "hash_mismatch"


def test_step2_refuses_a_different_presented_hash(cx):
    p = _draft(cx)
    sp.mark_checked(cx, "jane-doe", by="m", note="ok", content_hash=p["current_hash"])
    with pytest.raises(sp.StoryError) as e:
        sp.mark_giver_approved(cx, "jane-doe", by="r", consent_ref="c", content_hash="f" * 64)
    assert e.value.code == "hash_mismatch"


def test_step3_refuses_when_content_changed_since_step2(cx):
    p = _draft(cx)
    p = sp.mark_checked(cx, "jane-doe", by="m", note="ok", content_hash=p["current_hash"])
    p = sp.mark_giver_approved(cx, "jane-doe", by="r", consent_ref="c",
                               content_hash=p["content_hash"])
    old = p["content_hash"]
    _tamper(cx)
    with pytest.raises(sp.StoryError) as e:
        sp.mark_published(cx, "jane-doe", by="glen", content_hash=old)
    assert e.value.code == "hash_mismatch"
    assert sp.get(cx, "jane-doe")["state"] == "giver_approved"


def test_step3_refuses_a_different_presented_hash(cx):
    p = _draft(cx)
    p = sp.mark_checked(cx, "jane-doe", by="m", note="ok", content_hash=p["current_hash"])
    sp.mark_giver_approved(cx, "jane-doe", by="r", consent_ref="c", content_hash=p["content_hash"])
    with pytest.raises(sp.StoryError) as e:
        sp.mark_published(cx, "jane-doe", by="glen", content_hash="f" * 64)
    assert e.value.code == "hash_mismatch"


def test_withdraw_keeps_the_record(cx):
    _publish(cx)
    p = sp.withdraw(cx, "jane-doe", by="rae")
    assert p["state"] == "withdrawn" and p["withdrawn_by"] == "rae"
    assert p["published_by"] == "glen"          # history kept
    assert sp.list_published(cx) == []


def test_list_published_only(cx):
    _publish(cx, "pub-one")
    _draft(cx, "draft-one", testimonial_id=_testimonial(cx, name="Ann Bee"))
    assert [p["story_slug"] for p in sp.list_published(cx)] == ["pub-one"]
    assert {p["story_slug"] for p in sp.list_all(cx)} == {"pub-one", "draft-one"}


def test_works_with_sqlite_row_factory(tmp_path):
    """/api/action sets cx.row_factory = sqlite3.Row; the store must still work."""
    c = sqlite3.connect(str(tmp_path / "r.db"))
    c.row_factory = sqlite3.Row
    sp.init_table(c)
    p = _publish(c)
    assert p["state"] == "published"


# ── clicks ───────────────────────────────────────────────────────────────────

def test_click_recorded_once_and_nothing_personal(cx):
    h = sp.hash_session("session-uuid-123")
    assert sp.record_click(cx, "jane-doe", "/begin/product/x", h) is True
    assert sp.record_click(cx, "jane-doe", "/begin/product/x", h) is False
    assert sp.record_click(cx, "jane-doe", "/begin/product/y", h) is True
    assert sp.record_click(cx, "jane-doe", "/begin/product/x", sp.hash_session("other")) is True
    cols = [r[1] for r in cx.execute("PRAGMA table_info(story_clicks)").fetchall()]
    assert cols == ["id", "story_slug", "target_path", "session_hash", "clicked_at"]
    rows = cx.execute("SELECT session_hash FROM story_clicks").fetchall()
    assert all("session-uuid-123" not in r[0] for r in rows)
    counts = {(c["story_slug"], c["target_path"]): c["clicks"] for c in sp.click_counts(cx)}
    assert counts == {("jane-doe", "/begin/product/x"): 2, ("jane-doe", "/begin/product/y"): 1}


# ── render ───────────────────────────────────────────────────────────────────

def _rendered_story(out):
    """The story block's text exactly as a browser would read it from the source."""
    import html as _html
    import re as _re
    m = _re.search(r'<div class="story-text">(.*?)</div>', out, _re.S)
    assert m, "story block missing"
    return _html.unescape(m.group(1))


WEIRD_STORY = ("Line one  with two spaces.\nLine two, then a gap:\n\n\n"
               "   \n\tIndented & <quoted> \"words\" O'Neil.\n  trailing  ")


def test_render_story_text_exactly_as_stored(cx):
    p = _draft(cx, content={"story": WEIRD_STORY, "links": []})
    out = sr.render_page_html(p)
    assert _rendered_story(out) == WEIRD_STORY
    assert "white-space:pre-wrap" in out          # the browser shows spaces and breaks
    assert "<quoted>" not in out                   # still escaped


def test_render_page_has_words_exactly_and_fda_line(cx):
    p = _publish(cx)
    out = sr.render_page_html(p)
    assert _rendered_story(out) == CONTENT["story"]
    assert sr.FDA_STATEMENT in out
    assert out.count(sr.FDA_STATEMENT) == 1
    assert "<img" not in out                   # no photo unless supplied
    assert "noindex" not in out
    assert "Jane D." in out


def test_render_photo_only_when_present(cx):
    content = dict(CONTENT, photo={"src": "/static/stories/jane.jpg", "alt": "Jane"})
    p = _draft(cx, content=content)
    out = sr.render_page_html(p)
    assert '<img src="/static/stories/jane.jpg" alt="Jane">' in out


def test_render_escapes_hostile_text():
    page = {
        "story_slug": "x-y", "ref_slug": "",
        "name_line": '<script>alert("n")</script>',
        "content": {"story": '<img src=x onerror=alert(1)>\n\n"</p><script>bad()</script>',
                    "links": [{"label": '<b onmouseover="x">hi</b>', "path": "/begin/product/x"}]},
    }
    out = sr.render_page_html(page)
    assert "<script>alert" not in out and "<script>bad" not in out
    assert "<img src=x" not in out and "<b onmouseover" not in out
    assert "&lt;script&gt;alert(&quot;n&quot;)&lt;/script&gt;" in out
    idx = sr.render_index_html([page])
    assert "<script>alert" not in idx


def test_render_drops_a_non_site_link():
    page = {"story_slug": "x-y", "ref_slug": "", "name_line": "N",
            "content": {"story": "s", "links": [
                {"label": "bad", "path": "javascript:alert(1)"},
                {"label": "ok", "path": "/begin/product/x"}]}}
    out = sr.render_page_html(page)
    assert "javascript:" not in out
    assert 'href="/begin/product/x?story=x-y"' in out


def test_link_tagging_with_and_without_ref():
    assert sr.tag_link("/begin/product/x", story_slug="jane-doe", ref_slug="jane") == \
        "/begin/product/x?ref=jane&story=jane-doe"
    assert sr.tag_link("/begin/product/x", story_slug="jane-doe", ref_slug="") == \
        "/begin/product/x?story=jane-doe"


@pytest.mark.parametrize("bad", ["bad slug", "a&b=c", "x" * 65, "<x>"])
def test_invalid_ref_slug_dropped_at_render(bad):
    assert sr.tag_link("/begin/product/x", story_slug="jane-doe", ref_slug=bad) == \
        "/begin/product/x?story=jane-doe"


def test_rendered_page_links_carry_ref_and_story(cx):
    p = _publish(cx, ref_slug="jane")
    out = sr.render_page_html(p)
    assert 'href="/begin/product/neuro-magnesium?ref=jane&amp;story=jane-doe"' in out


def test_preview_marked_not_published_and_noindex(cx):
    p = _draft(cx)
    out = sr.render_preview_html(p, testimonial=sp.get_testimonial(cx, p["testimonial_id"]))
    assert "not published" in out and "noindex" in out
    assert p["current_hash"] in out
    assert "matches the testimonial wording: <code>yes" in out


def test_index_and_sitemap_list_given_pages(cx):
    p = _publish(cx)
    idx = sr.render_index_html([p])
    assert 'href="/stories/jane-doe"' in idx
    xml = sr.render_sitemap_xml([p], "https://myhealingoasis.com/")
    assert "<loc>https://myhealingoasis.com/stories/jane-doe</loc>" in xml


# ── dry-run script ───────────────────────────────────────────────────────────

def test_dry_run_script_renders_from_json(tmp_path):
    f = tmp_path / "page.json"
    f.write_text(json.dumps({"slug": "jane-doe", "name_line": "Jane Doe, Teacher",
                             "ref_slug": "jane", "content": CONTENT}))
    out = subprocess.run([sys.executable, str(REPO / "scripts" / "story_dry_run.py"), str(f)],
                         capture_output=True, text=True, check=True).stdout
    assert "Jane Doe, Teacher" in out and sr.FDA_STATEMENT in out
    assert "?ref=jane&amp;story=jane-doe" in out
    prev = subprocess.run([sys.executable, str(REPO / "scripts" / "story_dry_run.py"), str(f),
                           "--preview"], capture_output=True, text=True, check=True).stdout
    assert "not published" in prev


def test_dry_run_script_refuses_bad_content(tmp_path):
    f = tmp_path / "page.json"
    f.write_text(json.dumps({"slug": "jane-doe", "name_line": "J",
                             "content": {"story": "s", "links": [
                                 {"label": "x", "path": "https://evil.example/"}]}}))
    r = subprocess.run([sys.executable, str(REPO / "scripts" / "story_dry_run.py"), str(f)],
                       capture_output=True, text=True)
    assert r.returncode == 2 and "refused" in r.stderr


# ── review round 1 fixes ─────────────────────────────────────────────────────

def _giver_approved(cx, slug="jane-doe", **kw):
    p = _draft(cx, slug, **kw)
    p = sp.mark_checked(cx, slug, by="m", note="ok", content_hash=p["current_hash"])
    return sp.mark_giver_approved(cx, slug, by="r", consent_ref="c",
                                  content_hash=p["content_hash"])


def test_hash_covers_ref_slug_and_testimonial_id():
    base = sp.compute_hash(CONTENT, "N", "jane", 1)
    assert base != sp.compute_hash(CONTENT, "N", "other", 1)
    assert base != sp.compute_hash(CONTENT, "N", "jane", 2)


def test_ref_change_after_giver_approval_invalidates_every_step(cx):
    p = _giver_approved(cx, ref_slug="jane")
    old = p["content_hash"]
    p = sp.update(cx, "jane-doe", expected_hash=p["current_hash"], ref_slug="someone-else")
    assert p["state"] == "draft"
    for step in (
        lambda: sp.mark_checked(cx, "jane-doe", by="m", note="ok", content_hash=old),
        lambda: sp.mark_giver_approved(cx, "jane-doe", by="r", consent_ref="c", content_hash=old),
        lambda: sp.mark_published(cx, "jane-doe", by="glen", content_hash=old),
    ):
        with pytest.raises(sp.StoryError):
            step()
    assert sp.get(cx, "jane-doe")["state"] == "draft"


@pytest.mark.parametrize("column,value", [("ref_slug", "someone-else"), ("testimonial_id", None)])
def test_raw_ref_or_testimonial_change_refused_at_step3(cx, column, value):
    """Changed behind the gate's back (state stays giver_approved): the stored hash
    no longer matches, so step 3 refuses the old hash."""
    p = _giver_approved(cx, ref_slug="jane")
    if value is None:
        value = _testimonial(cx, name="Swap Ped")
    cx.execute(f"UPDATE story_pages SET {column}=? WHERE story_slug='jane-doe'", (value,))
    cx.commit()
    with pytest.raises(sp.StoryError) as e:
        sp.mark_published(cx, "jane-doe", by="glen", content_hash=p["content_hash"])
    assert e.value.code == "hash_mismatch"


@pytest.mark.parametrize("column", ["ref_slug", "testimonial_id"])
def test_cas_where_includes_ref_and_testimonial(cx, monkeypatch, column):
    """If the row's ref_slug or testimonial id changes between step 3's read and its
    write, the write must miss. Simulated by serving step 3 a stale read."""
    p = _giver_approved(cx, ref_slug="jane")
    stale = sp.get(cx, "jane-doe")
    value = "someone-else" if column == "ref_slug" else _testimonial(cx, name="Swap Ped")
    cx.execute(f"UPDATE story_pages SET {column}=? WHERE story_slug='jane-doe'", (value,))
    cx.commit()
    monkeypatch.setattr(sp, "get", lambda c, s: stale if stale else None)
    with pytest.raises(sp.StoryError) as e:
        sp.mark_published(cx, "jane-doe", by="glen", content_hash=p["content_hash"])
    assert e.value.code == "conflict"


def _set_consent(cx, tid, consent=None, status=None):
    if consent is not None:
        cx.execute("UPDATE product_reviews SET consent_public=? WHERE id=?", (consent, tid))
    if status is not None:
        cx.execute("UPDATE product_reviews SET status=? WHERE id=?", (status, tid))
    cx.commit()


@pytest.mark.parametrize("revoke", [{"consent": 0}, {"status": "rejected"}, {"status": "withdrawn"}])
def test_steps_2_and_3_recheck_consent(cx, revoke):
    p = _draft(cx)
    tid = p["testimonial_id"]
    p = sp.mark_checked(cx, "jane-doe", by="m", note="ok", content_hash=p["current_hash"])
    _set_consent(cx, tid, **revoke)
    with pytest.raises(sp.StoryError) as e:
        sp.mark_giver_approved(cx, "jane-doe", by="r", consent_ref="c", content_hash=p["content_hash"])
    assert e.value.code == "no_consent"
    _set_consent(cx, tid, consent=1, status="approved")
    p = sp.mark_giver_approved(cx, "jane-doe", by="r", consent_ref="c", content_hash=p["content_hash"])
    _set_consent(cx, tid, **revoke)
    with pytest.raises(sp.StoryError) as e:
        sp.mark_published(cx, "jane-doe", by="glen", content_hash=p["content_hash"])
    assert e.value.code == "no_consent"


@pytest.mark.parametrize("step", [1, 2, 3])
def test_consent_rechecked_inside_the_compare_and_set(cx, monkeypatch, step):
    """Consent revoked between the step's read and its write: the Python check is
    fooled (it sees a consented row), so only the SQL re-check can refuse."""
    p = _draft(cx)
    tid = p["testimonial_id"]
    if step >= 2:
        p = sp.mark_checked(cx, "jane-doe", by="m", note="ok", content_hash=p["current_hash"])
    if step == 3:
        p = sp.mark_giver_approved(cx, "jane-doe", by="r", consent_ref="c",
                                   content_hash=p["content_hash"])
    fake = dict(sp.get_testimonial(cx, tid))
    _set_consent(cx, tid, consent=0)
    monkeypatch.setattr(sp, "get_testimonial", lambda c, t: fake)
    h = p["current_hash"] if step == 1 else p["content_hash"]
    with pytest.raises(sp.StoryError) as e:
        if step == 1:
            sp.mark_checked(cx, "jane-doe", by="m", note="ok", content_hash=h)
        elif step == 2:
            sp.mark_giver_approved(cx, "jane-doe", by="r", consent_ref="c", content_hash=h)
        else:
            sp.mark_published(cx, "jane-doe", by="glen", content_hash=h)
    assert e.value.code == "conflict"


def test_revoked_consent_takes_published_page_out_of_servable(cx):
    p = _publish(cx)
    assert sp.servable(cx, p) and [x["story_slug"] for x in sp.list_servable(cx)] == ["jane-doe"]
    _set_consent(cx, p["testimonial_id"], status="withdrawn")
    assert not sp.servable(cx, sp.get(cx, "jane-doe"))
    assert sp.list_servable(cx) == []


def test_stale_edit_refused(cx):
    p = _draft(cx)
    loaded = p["current_hash"]
    # First save from the loaded copy succeeds.
    sp.update(cx, "jane-doe", expected_hash=loaded, name_line="Jane Doe, Teacher")
    # A second save from the same, now stale, copy is refused.
    with pytest.raises(sp.StoryError) as e:
        sp.update(cx, "jane-doe", expected_hash=loaded, name_line="Jane Doe, Nurse")
    assert e.value.code == "conflict"
    assert sp.get(cx, "jane-doe")["name_line"] == "Jane Doe, Teacher"
