# tests/test_coach_signup_api.py
import io
import sqlite3
from unittest import mock
import app as appmod
from dashboard import coach_directory as _cd


def _client():
    appmod.app.config["TESTING"] = True
    return appmod.app.test_client()


def _form(*, name="Cora", focus="sleep", capacity=3, with_video=True):
    data = {"name": name, "focus": focus, "capacity": str(capacity)}
    if with_video:
        data["video"] = (io.BytesIO(b"\x00\x01fakevideo"), "clip.mp4")
    return data


def test_signup_requires_practitioner_session():
    with mock.patch.object(appmod, "_practitioner_session_pid", return_value=None):
        r = _client().post("/api/practitioner/coach-profile?token=bad",
                           data=_form(), content_type="multipart/form-data")
    assert r.status_code == 401


def test_signup_certified_uploads_video_and_lists():
    c = _client()
    with mock.patch.object(appmod, "_practitioner_session_pid", return_value="pid1"), \
         mock.patch("dashboard.practitioner_portal.practitioner_email_by_id", return_value="ok@x.com"), \
         mock.patch.object(appmod, "_coach_cert_ok", return_value=True):
        r = c.post("/api/practitioner/coach-profile?token=t", data=_form(),
                   content_type="multipart/form-data")
    d = r.get_json()
    assert d["ok"] and d["cert_ok"] is True and d["listed"] is True and d["capacity"] == 3
    assert d["intro_video_url"].startswith("/portal-asset/coach-") and d["intro_video_url"].endswith(".mp4")
    with sqlite3.connect(appmod.LOG_DB) as cx:
        cx.row_factory = sqlite3.Row
        _cd.init_coach_tables(cx)
        assert any(v["name"] == "Cora" for v in _cd.list_active(cx))


def test_capacity_clamped_and_zero_unlists():
    c = _client()
    with mock.patch.object(appmod, "_practitioner_session_pid", return_value="pid2"), \
         mock.patch("dashboard.practitioner_portal.practitioner_email_by_id", return_value="cap@x.com"), \
         mock.patch.object(appmod, "_coach_cert_ok", return_value=True):
        hi = c.post("/api/practitioner/coach-profile?token=t", data=_form(capacity=99),
                    content_type="multipart/form-data").get_json()
        zero = c.post("/api/practitioner/coach-profile?token=t",
                      data=_form(capacity=0, with_video=False),
                      content_type="multipart/form-data").get_json()
    assert hi["capacity"] == 12                    # clamped to 12
    assert zero["capacity"] == 0 and zero["listed"] is False   # 0 = not taking members
    with sqlite3.connect(appmod.LOG_DB) as cx:
        cx.row_factory = sqlite3.Row
        _cd.init_coach_tables(cx)
        assert _cd.get_volunteer(cx, "cap@x.com")["active"] == 0


def test_signup_uncertified_not_listed():
    c = _client()
    with mock.patch.object(appmod, "_practitioner_session_pid", return_value="pid3"), \
         mock.patch("dashboard.practitioner_portal.practitioner_email_by_id", return_value="no@x.com"), \
         mock.patch.object(appmod, "_coach_cert_ok", return_value=False):
        d = c.post("/api/practitioner/coach-profile?token=t", data=_form(),
                   content_type="multipart/form-data").get_json()
    assert d["cert_ok"] is False and d["listed"] is False
    with sqlite3.connect(appmod.LOG_DB) as cx:
        cx.row_factory = sqlite3.Row
        _cd.init_coach_tables(cx)
        assert _cd.get_volunteer(cx, "no@x.com")["cert_ok"] == 0  # stored, not listed


def test_coach_cert_ok_fail_closed():
    with sqlite3.connect(appmod.LOG_DB) as cx:
        cx.row_factory = sqlite3.Row
        with mock.patch.object(appmod, "_is_certification_student", return_value=False):
            assert appmod._coach_cert_ok(cx, "nobody@nowhere.com") is False


# Glen, 2026-09-13: certification students may be listed as 1:1 coaches before
# they finish. The completion rules still qualify a finished coach on their own.

def test_certification_student_qualifies_before_finishing():
    asked = []
    with sqlite3.connect(appmod.LOG_DB) as cx:
        cx.row_factory = sqlite3.Row
        with mock.patch("dashboard.cert_submissions.list_for_email", return_value=[]), \
             mock.patch.object(appmod, "_is_certification_student",
                               side_effect=lambda e: asked.append(e) or True):
            assert appmod._coach_cert_ok(cx, "student@x.com") is True
    assert asked == ["student@x.com"], "the student check was never reached"


def test_finished_coach_qualifies_without_the_student_role():
    with sqlite3.connect(appmod.LOG_DB) as cx:
        cx.row_factory = sqlite3.Row
        with mock.patch("dashboard.cert_submissions.list_for_email", return_value=[]), \
             mock.patch("dashboard.cert_rules.evaluate", return_value={"complete": True}), \
             mock.patch.object(appmod, "_is_certification_student", return_value=False):
            assert appmod._coach_cert_ok(cx, "done@x.com") is True


def test_neither_finished_nor_a_student_is_not_listed():
    asked = []
    with sqlite3.connect(appmod.LOG_DB) as cx:
        cx.row_factory = sqlite3.Row
        with mock.patch("dashboard.cert_submissions.list_for_email", return_value=[]), \
             mock.patch.object(appmod, "_is_certification_student",
                               side_effect=lambda e: asked.append(e) or False):
            assert appmod._coach_cert_ok(cx, "neither@x.com") is False
    assert asked == ["neither@x.com"], "refused before the student check ran"


def test_a_broken_completion_check_still_consults_the_student_role():
    """An error reading cert submissions must not hide a real student, and must
    not list a non-student either."""
    with sqlite3.connect(appmod.LOG_DB) as cx:
        cx.row_factory = sqlite3.Row
        with mock.patch("dashboard.cert_submissions.list_for_email",
                        side_effect=RuntimeError("db down")):
            with mock.patch.object(appmod, "_is_certification_student", return_value=True):
                assert appmod._coach_cert_ok(cx, "s@x.com") is True
            with mock.patch.object(appmod, "_is_certification_student", return_value=False):
                assert appmod._coach_cert_ok(cx, "n@x.com") is False


def test_signup_by_a_student_lists_them():
    """End to end through the route, with only the two lookups faked."""
    c = _client()
    with mock.patch.object(appmod, "_practitioner_session_pid", return_value="pid5"), \
         mock.patch("dashboard.practitioner_portal.practitioner_email_by_id", return_value="stu@x.com"), \
         mock.patch("dashboard.cert_submissions.list_for_email", return_value=[]), \
         mock.patch.object(appmod, "_is_certification_student", return_value=True):
        d = c.post("/api/practitioner/coach-profile?token=t", data=_form(name="Stu"),
                   content_type="multipart/form-data").get_json()
    assert d["cert_ok"] is True and d["listed"] is True
    with sqlite3.connect(appmod.LOG_DB) as cx:
        cx.row_factory = sqlite3.Row
        _cd.init_coach_tables(cx)
        assert any(v["name"] == "Stu" for v in _cd.list_active(cx))


def test_edit_without_video_preserves_url():
    c = _client()
    with mock.patch.object(appmod, "_practitioner_session_pid", return_value="pid4"), \
         mock.patch("dashboard.practitioner_portal.practitioner_email_by_id", return_value="keep@x.com"), \
         mock.patch.object(appmod, "_coach_cert_ok", return_value=True):
        first = c.post("/api/practitioner/coach-profile?token=t", data=_form(),
                       content_type="multipart/form-data").get_json()
        assert first["intro_video_url"].startswith("/portal-asset/coach-")
        original_url = first["intro_video_url"]
        second = c.post("/api/practitioner/coach-profile?token=t",
                        data=_form(capacity=5, with_video=False),
                        content_type="multipart/form-data").get_json()
    assert second["intro_video_url"] == original_url
