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


def test_nine_dated_lessons_all_public():
    lessons = _lessons()
    assert len(lessons) == 9
    assert {l.access for l in lessons} == {"public"}
    slugs = [l.slug for l in lessons]
    assert slugs == sorted(slugs), "lessons run in date order"


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
