"""Live Class Highlights: Glen's solo teaching from recorded Wednesday classes, one public lesson per date.

Glen ruled 2026-10-06 that these pages are public and hold only his own teaching. The videos
are unlisted on Rumble, so the lesson page is the only place they are listed.
"""
import importlib
import os

import pytest

from dashboard import courses_content as cc
from dashboard.courses_sanitize import sanitize_html

_MHOST = "http://mentorshipu.test"
REPO_COURSES = os.path.join(os.path.dirname(os.path.dirname(__file__)), "courses")
SLUG = "live-class-highlights"


def _lessons():
    course = cc.load_course(SLUG, root=REPO_COURSES)
    return [l for m in course.modules for l in m.lessons]


# One lesson per class. 23 and 30 September each had a MasterClass and a Group Coaching.
EXPECTED = [
    "2026-07-08-dfy-wellness-foresight-masterclass",
    "2026-07-15-dfy-wellness-foresight-masterclass",
    "2026-07-22-dfy-wellness-foresight-masterclass",
    "2026-08-19-free-wellness-whispering-masterclass",
    "2026-08-26-free-wellness-whispering-masterclass",
    "2026-09-23-free-wellness-whispering-masterclass",
    "2026-09-23-group-coaching",
    "2026-09-30-free-wellness-whispering-masterclass",
    "2026-09-30-group-coaching",
]


def test_exact_lessons_in_date_order_all_public():
    lessons = _lessons()
    assert [l.slug for l in lessons] == EXPECTED
    assert {l.access for l in lessons} == {"public"}


def test_each_lesson_keeps_exactly_one_rumble_video_after_sanitizing():
    for l in _lessons():
        clean = sanitize_html(l.body_md)
        assert clean.count("<iframe") == 1, l.slug
        assert 'src="https://rumble.com/embed/' in clean, l.slug


def test_each_lesson_states_participants_were_left_out():
    for l in _lessons():
        assert "left out to protect their privacy" in l.body_md, l.slug
        assert "<h3>Chapters</h3>" in l.body_md, l.slug


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("COURSES_ROOT", REPO_COURSES)
    monkeypatch.setenv("MENTORSHIP_BASE_URL", _MHOST)
    import app as appmod
    importlib.reload(appmod)
    monkeypatch.setattr(appmod, "send_mentorship_setup_link", lambda *a, **k: ("test", None))
    appmod.app.config["TESTING"] = True
    return appmod.app.test_client()


def test_every_lesson_opens_for_a_signed_out_visitor(client):
    for l in _lessons():
        r = client.get(f"/learn/{SLUG}/{l.module_slug}/{l.slug}", base_url=_MHOST)
        assert r.status_code == 200, l.slug
        assert b"rumble.com/embed/" in r.data, l.slug


def test_course_is_listed_on_the_course_index(client):
    r = client.get("/learn", base_url=_MHOST)
    assert r.status_code == 200
    assert b'href="/learn/live-class-highlights"' in r.data


def _client_and_app(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("COURSES_ROOT", REPO_COURSES)
    monkeypatch.setenv("MENTORSHIP_BASE_URL", _MHOST)
    import app as appmod
    importlib.reload(appmod)
    monkeypatch.setattr(appmod, "send_mentorship_setup_link", lambda *a, **k: ("test", None))
    appmod.app.config["TESTING"] = True
    return appmod.app.test_client(), appmod


def _signed_in_with_module_done(appmod, email="h@example.com"):
    """A learner who has watched every lesson and has a homework row, the state
    in which a certifiable course offers the $200 module certification."""
    import sqlite3
    from dashboard import course_progress as cp
    from dashboard import course_tokens
    lessons = _lessons()
    with sqlite3.connect(appmod.LOG_DB) as cx:
        course_tokens.init_course_tokens_table(cx)
        tok = course_tokens.mint_course_token(cx, email, "T")
        for l in lessons:
            cp.mark_watched(cx, email, SLUG, l.module_slug, l.slug)
        cp.record_homework(cx, email, SLUG, lessons[0].module_slug, "takeaways")
    return tok, lessons[0]


def test_no_homework_box_and_no_certification_offer(monkeypatch, tmp_path):
    c, appmod = _client_and_app(monkeypatch, tmp_path)
    monkeypatch.setenv("STRIPE_MODULE_CERT_PRICE_ID", "price_modcert")
    tok, lesson = _signed_in_with_module_done(appmod)
    r = c.get(f"/learn/{SLUG}/{lesson.module_slug}/{lesson.slug}?token={tok}", base_url=_MHOST)
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "left out to protect their privacy" in body
    assert "Homework" not in body
    assert "Certify this module" not in body
    assert "/certify" not in body


def test_certify_and_homework_routes_refuse_this_course(monkeypatch, tmp_path):
    c, appmod = _client_and_app(monkeypatch, tmp_path)
    monkeypatch.setenv("STRIPE_MODULE_CERT_PRICE_ID", "price_modcert")
    monkeypatch.setenv("STRIPE_ACTIVE", "true")
    tok, lesson = _signed_in_with_module_done(appmod)
    r = c.post(f"/api/courses/{SLUG}/{lesson.module_slug}/certify?token={tok}", json={}, base_url=_MHOST)
    assert r.status_code == 404
    assert r.get_json()["error"] == "not certifiable"
    r = c.post(f"/api/courses/{SLUG}/{lesson.module_slug}/homework?token={tok}",
               json={"payload": "my notes"}, base_url=_MHOST)
    assert r.status_code == 404
