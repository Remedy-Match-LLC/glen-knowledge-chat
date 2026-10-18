from tests.courses_fixture import write_sample_course
from dashboard import courses_content as cc


def test_load_course_parses_structure(tmp_path):
    write_sample_course(str(tmp_path))
    course = cc.load_course("ash-intro", root=str(tmp_path))
    assert course.title == "ASH Intro"
    assert len(course.modules) == 2
    lessons = course.modules[0].lessons
    assert [l.slug for l in lessons] == ["01-out-takes", "02-welcome"]
    assert lessons[0].access == "public"
    assert lessons[0].rumble_id == ""  # optional/ignored in the Stage 1.5 model
    assert "rumble.com/embed/v1abcd" in lessons[0].body_md
    assert lessons[1].access == "member"
    assert "Welcome transcript" in lessons[1].body_md
    assert "youtube.com/embed/v2efgh" in lessons[1].body_md

    pro_module = course.modules[1]
    assert pro_module.slug == "03-pro"
    assert pro_module.title == "Advanced Practice"
    pro_lessons = pro_module.lessons
    assert [l.slug for l in pro_lessons] == ["01-advanced"]
    assert pro_lessons[0].access == "paid"
    assert "Advanced transcript" in pro_lessons[0].body_md
    assert "rumble.com/embed/v3ijkl" in pro_lessons[0].body_md


def test_list_courses_finds_course_dirs(tmp_path):
    write_sample_course(str(tmp_path))
    slugs = [c.slug for c in cc.list_courses(root=str(tmp_path))]
    assert slugs == ["ash-intro"]


def test_render_body_outputs_html():
    assert "<p>" in cc.render_body("hello **world**")


def test_module_completion_reset_is_read_from_course_yaml(tmp_path):
    from dashboard import courses_content as cc
    d = tmp_path / "c1" / "m1"
    d.mkdir(parents=True)
    (d / "l1.md").write_text("---\ntitle: L\naccess: paid\n---\nx\n")
    (tmp_path / "c1" / "course.yaml").write_text(
        "title: C\nmodules:\n  - slug: m1\n    title: M\n    completion_reset: 2026-10-09\n"
        "    lessons:\n      - l1\n")
    assert cc.load_course("c1", str(tmp_path)).modules[0].completion_reset == "2026-10-09"
    (tmp_path / "c1" / "course.yaml").write_text(
        "title: C\nmodules:\n  - slug: m1\n    title: M\n    lessons:\n      - l1\n")
    assert cc.load_course("c1", str(tmp_path)).modules[0].completion_reset == ""


def test_a_bad_completion_reset_is_ignored(tmp_path):
    from dashboard import courses_content as cc
    d = tmp_path / "c1" / "m1"
    d.mkdir(parents=True)
    (d / "l1.md").write_text("---\ntitle: L\naccess: paid\n---\nx\n")
    for bad in ("Oct 9 2026", "10/9/2026", "'2026-10-9'", "yes", "'2026-99-99'", "'2026-02-30'", "'20261009'", "20261009"):
        (tmp_path / "c1" / "course.yaml").write_text(
            f"title: C\nmodules:\n  - slug: m1\n    title: M\n    completion_reset: {bad}\n"
            "    lessons:\n      - l1\n")
        assert cc.load_course("c1", str(tmp_path)).modules[0].completion_reset == "", bad
    (tmp_path / "c1" / "course.yaml").write_text(
        "title: C\nmodules:\n  - slug: m1\n    title: M\n    completion_reset: '2026-10-09'\n"
        "    lessons:\n      - l1\n")
    assert cc.load_course("c1", str(tmp_path)).modules[0].completion_reset == "2026-10-09"
