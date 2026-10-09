import importlib
import sqlite3

import pytest

from tests.courses_fixture import write_sample_course

_MHOST = "http://mentorshipu.test"
_COURSE = "ash-intro"
_MODULE = "01-intro"
_LESSON = "02-welcome"
_LESSON_SLUGS = ["01-out-takes", "02-welcome"]


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("COURSES_ROOT", str(tmp_path / "courses"))
    monkeypatch.setenv("MENTORSHIP_BASE_URL", _MHOST)
    write_sample_course(str(tmp_path / "courses"))
    import app as appmod
    importlib.reload(appmod)
    monkeypatch.setattr(appmod, "send_mentorship_setup_link", lambda *a, **k: ("test", None))
    appmod.app.config["TESTING"] = True
    return appmod.app.test_client(), appmod


def _mint_token(appmod, email):
    from dashboard import course_tokens
    with sqlite3.connect(appmod.LOG_DB) as cx:
        course_tokens.init_course_tokens_table(cx)
        return course_tokens.mint_course_token(cx, email, "T")


def _complete_module(appmod, email):
    from dashboard import course_progress as cp
    with sqlite3.connect(appmod.LOG_DB) as cx:
        for lesson_slug in _LESSON_SLUGS:
            cp.mark_watched(cx, email, _COURSE, _MODULE, lesson_slug)
        cp.record_homework(cx, email, _COURSE, _MODULE, "my takeaways")


def _seed_status(appmod, email, status):
    from dashboard import module_certifications as mc
    with sqlite3.connect(appmod.LOG_DB) as cx:
        mc.record_purchase(cx, email, _COURSE, _MODULE, f"ch_{status}", 20000)
        if status == "approved":
            mc.approve(cx, email, _COURSE, _MODULE, "2026-01-01T00:00:00Z")


def _lesson_url(tok):
    return f"/learn/{_COURSE}/{_MODULE}/{_LESSON}?token={tok}"


def test_upsell_button_shown_when_completed_no_cert_row(client, monkeypatch):
    c, appmod = client
    monkeypatch.setenv("STRIPE_MODULE_CERT_PRICE_ID", "price_modcert")
    email = "a@example.com"
    tok = _mint_token(appmod, email)
    _complete_module(appmod, email)

    r = c.get(_lesson_url(tok), base_url=_MHOST)
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "Certify this module" in body
    assert f"/api/courses/{_COURSE}/{_MODULE}/certify" in body
    assert "muCertify" in body


def test_upsell_shows_pending_and_hides_button(client, monkeypatch):
    c, appmod = client
    monkeypatch.setenv("STRIPE_MODULE_CERT_PRICE_ID", "price_modcert")
    email = "b@example.com"
    tok = _mint_token(appmod, email)
    _complete_module(appmod, email)
    _seed_status(appmod, email, "pending")

    r = c.get(_lesson_url(tok), base_url=_MHOST)
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "pending review" in body
    assert "muCertify" not in body


def test_upsell_shows_approved(client, monkeypatch):
    c, appmod = client
    monkeypatch.setenv("STRIPE_MODULE_CERT_PRICE_ID", "price_modcert")
    email = "c@example.com"
    tok = _mint_token(appmod, email)
    _complete_module(appmod, email)
    _seed_status(appmod, email, "approved")

    r = c.get(_lesson_url(tok), base_url=_MHOST)
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "Module certified" in body


def test_no_upsell_when_module_not_completed(client, monkeypatch):
    c, appmod = client
    monkeypatch.setenv("STRIPE_MODULE_CERT_PRICE_ID", "price_modcert")
    email = "d@example.com"
    tok = _mint_token(appmod, email)

    r = c.get(_lesson_url(tok), base_url=_MHOST)
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "Certify this module" not in body
    assert "muCertify" not in body
    assert "Module certified" not in body
    assert "pending review" not in body


def test_no_upsell_when_price_unset(client, monkeypatch):
    c, appmod = client
    monkeypatch.delenv("STRIPE_MODULE_CERT_PRICE_ID", raising=False)
    email = "e@example.com"
    tok = _mint_token(appmod, email)
    _complete_module(appmod, email)

    r = c.get(_lesson_url(tok), base_url=_MHOST)
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "Certify this module" not in body
    assert "muCertify" not in body


def test_upsell_hidden_after_a_reset(client, monkeypatch, tmp_path):
    import os
    c, appmod = client
    monkeypatch.setenv("STRIPE_MODULE_CERT_PRICE_ID", "price_modcert")
    email = "r@example.com"
    tok = _mint_token(appmod, email)
    _complete_module(appmod, email)
    from dashboard import course_progress as cp
    with sqlite3.connect(appmod.LOG_DB) as cx:
        assert cp.module_completed(cx, email, _COURSE, _MODULE, _LESSON_SLUGS) is True
        cx.execute("UPDATE course_module_completed SET completed_at='2001-01-01T00:00:00Z'")
    base = os.path.join(str(tmp_path), "courses", _COURSE)
    with open(os.path.join(base, _MODULE, "03-extra.md"), "w") as f:
        f.write("---\ntitle: Extra\naccess: member\ndownloads: []\n---\n<p>New.</p>\n")
    y = open(os.path.join(base, "course.yaml")).read().replace(
        "      - 02-welcome\n", "      - 02-welcome\n      - 03-extra\n")
    open(os.path.join(base, "course.yaml"), "w").write(y)
    assert "Certify this module" in c.get(_lesson_url(tok), base_url=_MHOST).get_data(as_text=True)
    y = y.replace("    title: Introduction\n", "    title: Introduction\n    completion_reset: '2001-01-01'\n")
    open(os.path.join(base, "course.yaml"), "w").write(y)
    assert "Certify this module" not in c.get(_lesson_url(tok), base_url=_MHOST).get_data(as_text=True)
