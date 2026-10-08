"""Postgres only: the story-page store and gate on the production backend.

Prod runs Postgres, where lastrowid raises and one failed statement aborts the
transaction. Runs when STORIES_PG_TEST_DSN names a throwaway database; skipped
otherwise (CI).
"""
import json
import os

import pytest

DSN = os.environ.get("STORIES_PG_TEST_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="STORIES_PG_TEST_DSN not set")

CONTENT = {"story": "Fixture words.\n\nMore.", "links": [{"label": "P", "path": "/begin/product/x"}]}


@pytest.fixture
def cx(monkeypatch):
    monkeypatch.setenv("DB_BACKEND", "postgres")
    monkeypatch.setenv("PG_DSN", DSN)
    from dashboard import db
    c = db.connect("unused")
    for t in ("story_pages", "story_clicks", "product_reviews"):
        c.execute(f"DROP TABLE IF EXISTS {t}")
    c.commit()
    yield c
    c.rollback()
    for t in ("story_pages", "story_clicks", "product_reviews"):
        c.execute(f"DROP TABLE IF EXISTS {t}")
    c.commit()
    c.close()


def test_gate_and_clicks_on_postgres(cx):
    from dashboard import product_reviews as pr
    from dashboard import story_pages as sp
    sp.init_table(cx)
    # Before product_reviews exists, a lookup is "not found" and the connection survives.
    with pytest.raises(sp.StoryError) as e:
        sp.create(cx, "jane-doe", testimonial_id=1, content=CONTENT)
    assert e.value.code == "testimonial_not_found"
    assert cx.execute("SELECT 1").fetchone()[0] == 1
    pr.init_table(cx)
    tid = cx.execute(
        "INSERT INTO product_reviews (product_slug, email, name, rating, body, kind, "
        "consent_public) VALUES ('t', 'g@example.com', 'Jane Doe', 5, 'w', 'testimonial', 1) "
        "RETURNING id").fetchone()[0]
    cx.commit()
    p = sp.create(cx, "jane-doe", testimonial_id=tid, content=CONTENT, ref_slug="jane")
    assert p["state"] == "draft" and p["name_line"] == "Jane D."
    p = sp.mark_checked(cx, "jane-doe", by="m", note="ok", content_hash=p["current_hash"])
    p = sp.mark_giver_approved(cx, "jane-doe", by="r", consent_ref="c", content_hash=p["content_hash"])
    # A tampered row is refused at step 3.
    cx.execute("UPDATE story_pages SET content_json=? WHERE story_slug='jane-doe'",
               (json.dumps({"story": "x", "links": []}),))
    cx.commit()
    with pytest.raises(sp.StoryError) as e:
        sp.mark_published(cx, "jane-doe", by="glen", content_hash=p["content_hash"])
    assert e.value.code == "hash_mismatch"
    p = sp.update(cx, "jane-doe", content=CONTENT)
    assert p["state"] == "draft" and p["content_hash"] == ""
    p = sp.mark_checked(cx, "jane-doe", by="m", note="ok", content_hash=p["current_hash"])
    p = sp.mark_giver_approved(cx, "jane-doe", by="r", consent_ref="c", content_hash=p["content_hash"])
    p = sp.mark_published(cx, "jane-doe", by="glen", content_hash=p["content_hash"])
    assert p["state"] == "published"
    assert [x["story_slug"] for x in sp.list_published(cx)] == ["jane-doe"]
    h = sp.hash_session("s1")
    assert sp.record_click(cx, "jane-doe", "/begin/product/x", h) is True
    assert sp.record_click(cx, "jane-doe", "/begin/product/x", h) is False
    assert sp.click_counts(cx) == [{"story_slug": "jane-doe", "target_path": "/begin/product/x",
                                    "clicks": 1}]
    assert sp.withdraw(cx, "jane-doe", by="r")["state"] == "withdrawn"
