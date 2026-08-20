import importlib
import sqlite3
import sys
from pathlib import Path


def _app(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("PORTAL_HUB_ENABLED", "1")
    monkeypatch.setenv("MENTORSHIP_BASE_URL", "https://courses.mentorshipu.test")
    monkeypatch.setenv("COURSES_ROOT", str(Path(__file__).resolve().parent.parent / "courses"))
    repo = Path(__file__).resolve().parent.parent
    if str(repo) not in sys.path:
        sys.path.insert(0, str(repo))
    import app as appmod
    importlib.reload(appmod)
    return appmod


def _portal(appmod, email):
    from dashboard import client_portal as cp
    with sqlite3.connect(appmod.LOG_DB) as cx:
        cp.init_client_portal_table(cx)
        return cp.ensure_token(cx, email, "Student")


def test_certification_student_sees_course_and_gets_direct_access(tmp_path, monkeypatch):
    appmod = _app(tmp_path, monkeypatch)
    monkeypatch.setattr(appmod, "_is_certification_student", lambda email: email == "student@x.com")
    tok = _portal(appmod, "student@x.com")
    client = appmod.app.test_client()

    library = client.get(f"/api/portal/{tok}/library").get_json()
    course = next(i for i in library["items"] if i.get("kind") == "course")
    assert course["slug"] == "ash-certification"

    response = client.get(course["course_url"], follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["Location"].startswith(
        "https://courses.mentorshipu.test/learn?token=")
    assert "&next=/learn/ash-certification" in response.headers["Location"]

    from dashboard import course_entitlements as ce
    with sqlite3.connect(appmod.LOG_DB) as cx:
        assert ce.paid_level_for(cx, "student@x.com") == 2


def test_unqualified_portal_cannot_open_paid_cert_course(tmp_path, monkeypatch):
    appmod = _app(tmp_path, monkeypatch)
    monkeypatch.setattr(appmod, "_is_certification_student", lambda email: False)
    tok = _portal(appmod, "visitor@x.com")
    client = appmod.app.test_client()

    library = client.get(f"/api/portal/{tok}/library").get_json()
    assert not any(i.get("kind") == "course" for i in library["items"])
    assert client.get(f"/api/portal/{tok}/courses/ash-certification").status_code == 403


def test_paid_entitlement_appears_without_roster_role(tmp_path, monkeypatch):
    appmod = _app(tmp_path, monkeypatch)
    monkeypatch.setattr(appmod, "_is_certification_student", lambda email: False)
    tok = _portal(appmod, "buyer@x.com")
    from dashboard import course_entitlements as ce
    with sqlite3.connect(appmod.LOG_DB) as cx:
        ce.grant_cert(cx, "buyer@x.com", source="test", stripe_ref="cs_test")

    library = appmod.app.test_client().get(f"/api/portal/{tok}/library").get_json()
    assert any(i.get("slug") == "ash-certification" for i in library["items"])


def test_initial_cohort_existing_course_token_opens_all_certification_links(tmp_path, monkeypatch):
    appmod = _app(tmp_path, monkeypatch)
    monkeypatch.setattr(appmod, "_is_certification_student", lambda email: email == "cohort@x.com")
    from dashboard import course_tokens
    with sqlite3.connect(appmod.LOG_DB) as cx:
        token = course_tokens.mint_course_token(cx, "cohort@x.com", "Cohort Student")

    client = appmod.app.test_client()
    home = client.get(f"/learn/ash-certification?token={token}",
                      base_url="https://courses.mentorshipu.test")
    body = home.get_data(as_text=True)
    assert home.status_code == 200
    assert '(certification module)' not in body
    assert '/learn/ash-certification/02-body/01-minding-body-1' in body
    assert '/learn/ash-certification/13-prognosis/04-prognosis-2-06-17-26' in body
    assert 'Pay in full' not in body

    paid = client.get(
        f"/learn/ash-certification/02-body/01-minding-body-1?token={token}",
        base_url="https://courses.mentorshipu.test")
    assert paid.status_code == 200
    assert b"Minding Body 1" in paid.data
