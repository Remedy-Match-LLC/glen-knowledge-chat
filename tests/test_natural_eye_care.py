"""Natural Eye Care: public education on eye conditions.

Glen approved the drusen text on 2026-10-30 and named the course. He approved the macular
degeneration, cataract and glaucoma texts on 2026-10-31. The lesson is education
only. A product link, product name or price beside drusen or AMD would be a drug claim, so
these tests hold the lesson to PubMed links and no product language.
"""
import importlib
import os
import re

import pytest

from dashboard import courses_content as cc

_MHOST = "http://mentorshipu.test"
REPO_COURSES = os.path.join(os.path.dirname(os.path.dirname(__file__)), "courses")
SLUG = "natural-eye-care"
# PubMed links per lesson, as approved. A change here means the approved text changed.
PUBMED_LINKS = {"macular-degeneration": 21, "drusen": 22, "diabetic-retinopathy": 23, "dry-eye": 36, "retinitis-pigmentosa": 28}
# The only non-PubMed link allowed: the practitioner finder, which carries no products.
OTHER_LINKS = {
    "cataract": ["https://courses.mentorshipu.com/practitioner-finder"],
    "glaucoma": ["https://courses.mentorshipu.com/practitioner-finder"],
}
CLOSING_LINE = "Keep working with your own eye doctor throughout."
CLOSING = {"diabetic-retinopathy": "Keep working with your own eye doctor and diabetes doctor throughout."}
PRODUCT_WORDS = re.compile(
    r"remedy ?match|healing oasis|macular wellness|lipid[- ]?(cleanse|zyme)|ocuheal|ocuflow|iop program|lens-?zyme|curcu-guard|iron ?out|retina renew|blood cleanse|aces eye|wholomega|nerve pulse|moisture eyes"
    r"|neuro ?magnesium|\bbuy\b|add to cart|\$\d", re.I)


def _lessons():
    course = cc.load_course(SLUG, root=REPO_COURSES)
    return [l for m in course.modules for l in m.lessons]


def test_lessons_are_the_approved_ones_and_all_public():
    lessons = _lessons()
    assert [l.slug for l in lessons] == list(PUBMED_LINKS)
    assert {l.access for l in lessons} == {"public"}


def test_lesson_links_only_to_pubmed_and_names_no_product():
    for l in _lessons():
        links = re.findall(r'href="([^"]+)"', l.body_md)
        assert links, l.slug
        others = [u for u in links if not u.startswith("https://pubmed.ncbi.nlm.nih.gov/")]
        assert others == OTHER_LINKS.get(l.slug, []), (l.slug, others)
        assert not PRODUCT_WORDS.search(l.body_md), PRODUCT_WORDS.search(l.body_md).group(0)


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


def test_lesson_opens_for_a_signed_out_visitor_with_its_sources(client):
    for l in _lessons():
        r = client.get(f"/learn/{SLUG}/{l.module_slug}/{l.slug}", base_url=_MHOST)
        assert r.status_code == 200, l.slug
        html = r.get_data(as_text=True)
        assert html.count('href="https://pubmed.ncbi.nlm.nih.gov/') == l.body_md.count('href="https://pubmed') == PUBMED_LINKS[l.slug]
        assert CLOSING.get(l.slug, CLOSING_LINE) in html


def test_course_is_listed_on_the_course_index(client):
    r = client.get("/learn", base_url=_MHOST)
    assert r.status_code == 200
    assert b'href="/learn/natural-eye-care"' in r.data
