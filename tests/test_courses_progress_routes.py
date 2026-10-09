import importlib, sqlite3
import pytest
from tests.courses_fixture import write_sample_course
_MHOST = "http://mentorshipu.test"


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


def _token(appmod, email="w@x.com"):
    from dashboard import course_tokens
    with sqlite3.connect(appmod.LOG_DB) as cx:
        course_tokens.init_course_tokens_table(cx)
        return course_tokens.mint_course_token(cx, email, "W")


def test_watched_marks_for_resolved_learner(client):
    c, appmod = client
    tok = _token(appmod, "w@x.com")
    r = c.post(f"/api/courses/ash-intro/01-intro/01-out-takes/watched?token={tok}", base_url=_MHOST)
    assert r.status_code == 200 and r.get_json()["ok"] is True
    from dashboard import course_progress as cp
    with sqlite3.connect(appmod.LOG_DB) as cx:
        assert "01-out-takes" in cp.watched_lessons(cx, "w@x.com", "ash-intro", "01-intro")


def test_watched_unauthorized_without_token(client):
    c, _ = client
    r = c.post("/api/courses/ash-intro/01-intro/01-out-takes/watched", base_url=_MHOST)
    assert r.status_code == 401


def test_homework_stores_and_returns_feedback(client, monkeypatch):
    c, appmod = client
    from dashboard import homework_analysis
    monkeypatch.setattr(homework_analysis, "analyze",
                        lambda module, assignment, submission: {"rating": "Good", "feedback": "Nice, go deeper."})
    tok = _token(appmod, "hw@x.com")
    r = c.post(f"/api/courses/ash-intro/01-intro/homework?token={tok}",
               json={"payload": "my reflection"}, base_url=_MHOST)
    assert r.status_code == 200
    j = r.get_json()
    assert j["ok"] is True and j["rating"] == "Good" and "deeper" in j["feedback"]
    from dashboard import course_progress as cp
    with sqlite3.connect(appmod.LOG_DB) as cx:
        hw = cp.homework(cx, "hw@x.com", "ash-intro", "01-intro")
    assert hw["payload"] == "my reflection" and hw["submitted_at"]


def test_homework_records_even_if_ai_fails(client, monkeypatch):
    c, appmod = client
    from dashboard import homework_analysis
    monkeypatch.setattr(homework_analysis, "analyze",
                        lambda *a, **k: {"rating": "", "feedback": ""})  # AI down/empty
    tok = _token(appmod, "hw2@x.com")
    r = c.post(f"/api/courses/ash-intro/01-intro/homework?token={tok}",
               json={"payload": "still counts"}, base_url=_MHOST)
    assert r.status_code == 200
    from dashboard import course_progress as cp
    with sqlite3.connect(appmod.LOG_DB) as cx:
        assert cp.homework(cx, "hw2@x.com", "ash-intro", "01-intro")["submitted_at"]  # submitted regardless


def test_homework_unauthorized_without_token(client):
    c, _ = client
    r = c.post("/api/courses/ash-intro/01-intro/homework", json={"payload": "x"}, base_url=_MHOST)
    assert r.status_code == 401


def test_first_request_backfills_and_a_new_lesson_keeps_credit(client, monkeypatch, tmp_path):
    c, appmod = client
    import courses_blueprint
    from dashboard import course_progress as cp, courses_content as cc
    with sqlite3.connect(appmod.LOG_DB) as cx:
        cp.init_progress_tables(cx)
        for l in ("01-out-takes", "02-welcome"):
            cp.mark_watched(cx, "done@x.com", "ash-intro", "01-intro", l)
        cp.record_homework(cx, "done@x.com", "ash-intro", "01-intro", "reflection")
    monkeypatch.setattr(courses_blueprint, "_completion_backfill_done", False)
    monkeypatch.setattr(courses_blueprint, "_completion_backfill_next", 0.0)
    c.get("/learn", base_url=_MHOST)
    with sqlite3.connect(appmod.LOG_DB) as cx:
        assert cp.completed_at(cx, "done@x.com", "ash-intro", "01-intro")
    # A deploy adds a lesson to the module.
    root = tmp_path / "courses" / "ash-intro"
    (root / "01-intro" / "03-new.md").write_text("---\ntitle: New\naccess: member\n---\nx\n")
    y = (root / "course.yaml").read_text().replace("      - 02-welcome\n", "      - 02-welcome\n      - 03-new\n")
    (root / "course.yaml").write_text(y)
    mod = next(m for m in cc.load_course("ash-intro").modules if m.slug == "01-intro")
    assert [l.slug for l in mod.lessons][-1] == "03-new"
    with sqlite3.connect(appmod.LOG_DB) as cx:
        assert cp.module_completed(cx, "done@x.com", "ash-intro", "01-intro",
                                   [l.slug for l in mod.lessons]) is True


def test_watching_the_last_lesson_records_completion(client, monkeypatch):
    c, appmod = client
    import courses_blueprint
    monkeypatch.setattr(courses_blueprint, "_completion_backfill_done", True)
    from dashboard import course_progress as cp
    with sqlite3.connect(appmod.LOG_DB) as cx:
        cp.init_progress_tables(cx)
        cp.mark_watched(cx, "last@x.com", "ash-intro", "01-intro", "02-welcome")
        cp.record_homework(cx, "last@x.com", "ash-intro", "01-intro", "reflection")
        assert cp.completed_at(cx, "last@x.com", "ash-intro", "01-intro") is None
    tok = _token(appmod, "last@x.com")
    r = c.post(f"/api/courses/ash-intro/01-intro/01-out-takes/watched?token={tok}", base_url=_MHOST)
    assert r.status_code == 200
    with sqlite3.connect(appmod.LOG_DB) as cx:
        assert cp.completed_at(cx, "last@x.com", "ash-intro", "01-intro")


def test_submitting_homework_records_completion(client, monkeypatch):
    c, appmod = client
    import courses_blueprint
    monkeypatch.setattr(courses_blueprint, "_completion_backfill_done", True)
    from dashboard import course_progress as cp, homework_analysis
    monkeypatch.setattr(homework_analysis, "analyze", lambda *a, **k: {"rating": "", "feedback": ""})
    with sqlite3.connect(appmod.LOG_DB) as cx:
        cp.init_progress_tables(cx)
        for l in ("01-out-takes", "02-welcome"):
            cp.mark_watched(cx, "hwlast@x.com", "ash-intro", "01-intro", l)
    tok = _token(appmod, "hwlast@x.com")
    r = c.post(f"/api/courses/ash-intro/01-intro/homework?token={tok}",
               json={"payload": "my reflection"}, base_url=_MHOST)
    assert r.status_code == 200
    with sqlite3.connect(appmod.LOG_DB) as cx:
        assert cp.completed_at(cx, "hwlast@x.com", "ash-intro", "01-intro")


def test_backfill_retries_after_a_failure_and_rescans_hourly(client, monkeypatch):
    c, appmod = client
    import courses_blueprint as cb
    from dashboard import course_progress as cp
    calls = []
    results = iter([-1, 0])
    monkeypatch.setattr(cp, "backfill_completions", lambda cx, lessons: calls.append(1) or next(results))
    clock = [1000.0]
    monkeypatch.setattr(cb._time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(cb, "_completion_backfill_done", False)
    monkeypatch.setattr(cb, "_completion_backfill_next", 0.0)
    c.get("/learn", base_url=_MHOST)
    assert len(calls) == 1                      # failed
    c.get("/learn", base_url=_MHOST)
    assert len(calls) == 1                      # not before the retry delay
    clock[0] += cb._BACKFILL_RETRY_S + 1
    c.get("/learn", base_url=_MHOST)
    assert len(calls) == 2                      # retried, succeeded
    clock[0] += cb._BACKFILL_RETRY_S + 1
    c.get("/learn", base_url=_MHOST)
    assert len(calls) == 2                      # success waits the full hour
    clock[0] += cb._BACKFILL_EVERY_S
    monkeypatch.setattr(cp, "backfill_completions", lambda cx, lessons: calls.append(1) or 0)
    c.get("/learn", base_url=_MHOST)
    assert len(calls) == 3
