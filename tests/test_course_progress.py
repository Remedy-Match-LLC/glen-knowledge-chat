import sqlite3
import pytest
from dashboard import course_progress as cp


@pytest.fixture
def cx():
    c = sqlite3.connect(":memory:")
    cp.init_progress_tables(c)
    yield c
    c.close()


def test_watched_is_idempotent_and_scoped(cx):
    cp.mark_watched(cx, "A@x.com", "ash", "02-body", "01-a")
    cp.mark_watched(cx, "a@x.com", "ash", "02-body", "01-a")  # normalized + idempotent
    assert cp.watched_lessons(cx, "a@x.com", "ash", "02-body") == {"01-a"}
    cp.mark_watched(cx, "a@x.com", "ash", "02-body", "02-b")
    assert cp.watched_lessons(cx, "a@x.com", "ash", "02-body") == {"01-a", "02-b"}
    assert cp.watched_lessons(cx, "a@x.com", "ash", "03-mind") == set()


def test_homework_upsert(cx):
    assert cp.homework(cx, "m@x.com", "ash", "02-body") is None
    cp.record_homework(cx, "m@x.com", "ash", "02-body", "my reflection", ai_rating="good", ai_feedback="go deeper")
    hw = cp.homework(cx, "m@x.com", "ash", "02-body")
    assert hw["payload"] == "my reflection" and hw["ai_rating"] == "good" and hw["submitted_at"]
    cp.record_homework(cx, "m@x.com", "ash", "02-body", "revised")  # upsert, no ai fields
    assert cp.homework(cx, "m@x.com", "ash", "02-body")["payload"] == "revised"


def test_module_completed_requires_all_watched_and_homework(cx):
    lessons = ["01-a", "02-b"]
    assert cp.module_completed(cx, "c@x.com", "ash", "02-body", lessons) is False
    cp.mark_watched(cx, "c@x.com", "ash", "02-body", "01-a")
    cp.record_homework(cx, "c@x.com", "ash", "02-body", "done")
    assert cp.module_completed(cx, "c@x.com", "ash", "02-body", lessons) is False  # 02-b not watched
    cp.mark_watched(cx, "c@x.com", "ash", "02-body", "02-b")
    assert cp.module_completed(cx, "c@x.com", "ash", "02-body", lessons) is True
    # no lessons → not complete, for a learner with no completion on record
    assert cp.module_completed(cx, "fresh@x.com", "ash", "02-body", []) is False


def test_reads_never_raise_on_broken_cx():
    class Boom:
        def execute(self, *a, **k): raise RuntimeError("db down")
    assert cp.watched_lessons(Boom(), "a", "b", "c") == set()
    assert cp.homework(Boom(), "a", "b", "c") is None
    assert cp.module_completed(Boom(), "a", "b", "c", ["x"]) is False


# ── Completion is kept when a module changes (Glen, 2026-10-09) ──────────────

def _complete(cx, email, lessons, module="09-terrain"):
    for l in lessons:
        cp.mark_watched(cx, email, "ash", module, l)
    cp.record_homework(cx, email, "ash", module, "done")


def test_completion_survives_a_new_lesson(cx):
    _complete(cx, "k@x.com", ["01-a", "02-b"])
    assert cp.module_completed(cx, "k@x.com", "ash", "09-terrain", ["01-a", "02-b"]) is True
    # A lesson is added to the module. Credit already earned stays.
    assert cp.module_completed(cx, "k@x.com", "ash", "09-terrain", ["01-a", "02-b", "03-new"]) is True


def test_unrecorded_learner_still_needs_every_current_lesson(cx):
    # Nobody checked this learner before the lesson was added, and no backfill ran.
    _complete(cx, "n@x.com", ["01-a", "02-b"])
    assert cp.module_completed(cx, "n@x.com", "ash", "09-terrain", ["01-a", "02-b", "03-new"]) is False
    assert cp.completed_at(cx, "n@x.com", "ash", "09-terrain") is None


def test_backfill_records_current_completers_only(cx):
    _complete(cx, "done@x.com", ["01-a", "02-b"])
    _complete(cx, "half@x.com", ["01-a"])
    cp.mark_watched(cx, "nohw@x.com", "ash", "09-terrain", "01-a")
    cp.mark_watched(cx, "nohw@x.com", "ash", "09-terrain", "02-b")
    n = cp.backfill_completions(cx, {("ash", "09-terrain"): ["01-a", "02-b"]})
    assert n == 1
    assert cp.completed_at(cx, "done@x.com", "ash", "09-terrain")
    assert cp.completed_at(cx, "half@x.com", "ash", "09-terrain") is None
    assert cp.completed_at(cx, "nohw@x.com", "ash", "09-terrain") is None
    # Then the module grows; the recorded completer keeps credit.
    assert cp.module_completed(cx, "done@x.com", "ash", "09-terrain", ["01-a", "02-b", "03-new"]) is True
    assert cp.backfill_completions(cx, {("ash", "09-terrain"): ["01-a", "02-b"]}) == 0  # idempotent


def test_backfill_skips_modules_it_was_not_given(cx):
    _complete(cx, "d@x.com", ["01-a"], module="02-body")
    assert cp.backfill_completions(cx, {("ash", "09-terrain"): ["01-a"]}) == 0
    assert cp.completed_at(cx, "d@x.com", "ash", "02-body") is None


def test_reset_date_clears_older_credit(cx):
    _complete(cx, "r@x.com", ["01-a"])
    assert cp.module_completed(cx, "r@x.com", "ash", "09-terrain", ["01-a"]) is True
    cx.execute("UPDATE course_module_completed SET completed_at='2001-01-01T00:00:00Z'")
    lessons = ["01-a", "02-new"]
    # Glen resets the module: credit recorded before the reset date no longer counts,
    # and the learner is judged against the current lessons again.
    assert cp.module_completed(cx, "r@x.com", "ash", "09-terrain", lessons, reset_before="2001-01-02") is False
    cp.mark_watched(cx, "r@x.com", "ash", "09-terrain", "02-new")
    assert cp.module_completed(cx, "r@x.com", "ash", "09-terrain", lessons, reset_before="2001-01-02") is True
    assert cp.completed_at(cx, "r@x.com", "ash", "09-terrain") > "2001-01-02"


def test_reset_date_does_not_touch_newer_credit(cx):
    _complete(cx, "s@x.com", ["01-a"])
    assert cp.module_completed(cx, "s@x.com", "ash", "09-terrain", ["01-a"]) is True
    assert cp.module_completed(cx, "s@x.com", "ash", "09-terrain", ["01-a", "02-new"],
                               reset_before="2000-01-01") is True


def test_completion_record_reads_never_raise_and_roll_back():
    class Boom:
        rolled = 0
        def execute(self, *a, **k): raise RuntimeError("db down")
        def rollback(self): Boom.rolled += 1
    assert cp.completed_at(Boom(), "a", "b", "c") is None
    assert Boom.rolled == 1
    assert cp.backfill_completions(Boom(), {("b", "c"): ["x"]}) == -1  # failure is not "0 new"


def test_credit_from_the_reset_day_itself_does_not_count(cx):
    _complete(cx, "day@x.com", ["01-a"])
    assert cp.module_completed(cx, "day@x.com", "ash", "09-terrain", ["01-a"]) is True
    lessons = ["01-a", "02-new"]
    cx.execute("UPDATE course_module_completed SET completed_at='2026-10-09T05:00:00Z'")
    assert cp.module_completed(cx, "day@x.com", "ash", "09-terrain", lessons, reset_before="2026-10-09") is False
    cx.execute("UPDATE course_module_completed SET completed_at='2026-10-10T00:00:01Z'")
    assert cp.module_completed(cx, "day@x.com", "ash", "09-terrain", lessons, reset_before="2026-10-09") is True


def test_backfill_restamps_a_voided_record_only_if_complete_now(cx):
    _complete(cx, "v@x.com", ["01-a", "02-new"])
    _complete(cx, "w@x.com", ["01-a"])
    for e in ("v@x.com", "w@x.com"):
        cp.module_completed(cx, e, "ash", "09-terrain", ["01-a"])
    cx.execute("UPDATE course_module_completed SET completed_at='2001-01-01T00:00:00Z'")
    n = cp.backfill_completions(cx, {("ash", "09-terrain"): ["01-a", "02-new"]},
                                {("ash", "09-terrain"): "2001-01-01"})
    assert n == 1
    assert cp.completed_at(cx, "v@x.com", "ash", "09-terrain") > "2001-01-02"
    assert cp.completed_at(cx, "w@x.com", "ash", "09-terrain") == "2001-01-01T00:00:00Z"
