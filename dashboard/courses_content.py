from __future__ import annotations

import datetime
import os
import re
from dataclasses import dataclass

import frontmatter
import markdown as _md
import yaml

VALID_ACCESS = ("public", "member", "paid")


@dataclass
class Lesson:
    slug: str
    title: str
    access: str
    rumble_id: str
    downloads: list
    body_md: str
    module_slug: str
    course_slug: str


@dataclass
class Module:
    slug: str
    title: str
    lessons: list
    # ISO date. Completion recorded before it no longer counts; set only on Glen's word.
    completion_reset: str = ""


@dataclass
class Course:
    slug: str
    title: str
    description: str
    modules: list
    # A course.yaml may switch these off, e.g. a highlights reel with nothing to
    # hand in and no credential. Both default on, so existing courses are unchanged.
    homework: bool = True
    certifiable: bool = True


def _reset_date(v) -> str:
    """A module's completion_reset as YYYY-MM-DD (a UTC date), or "" if absent or
    not in that form. Ignored rather than guessed: a misread date would void
    credit learners earned. tests/test_courses_lint.py fails on a bad one."""
    if isinstance(v, datetime.date):
        return v.isoformat()[:10]
    s = str(v or "").strip()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        return ""
    try:
        datetime.date.fromisoformat(s)  # rejects a date that does not exist, e.g. 2026-99-99
    except ValueError:
        return ""
    return s


def courses_root() -> str:
    env = os.environ.get("COURSES_ROOT")
    if env:
        return env
    return os.path.join(os.path.dirname(os.path.dirname(__file__)), "courses")


def load_lesson(path: str, course_slug: str, module_slug: str) -> Lesson:
    post = frontmatter.load(path)
    meta = post.metadata or {}
    return Lesson(
        slug=os.path.splitext(os.path.basename(path))[0],
        title=str(meta.get("title", "")).strip(),
        access=str(meta.get("access", "")).strip().lower(),
        rumble_id=str(meta.get("rumble_id", "")).strip(),
        downloads=list(meta.get("downloads") or []),
        body_md=post.content,
        module_slug=module_slug,
        course_slug=course_slug,
    )


def load_course(course_slug: str, root: str | None = None) -> Course:
    root = root or courses_root()
    cdir = os.path.join(root, course_slug)
    with open(os.path.join(cdir, "course.yaml")) as f:
        spec = yaml.safe_load(f) or {}
    modules = []
    for m in spec.get("modules", []) or []:
        lessons = []
        for lslug in m.get("lessons", []) or []:
            lp = os.path.join(cdir, m["slug"], f"{lslug}.md")
            lessons.append(load_lesson(lp, course_slug, m["slug"]))
        modules.append(Module(slug=m["slug"], title=str(m.get("title", "")), lessons=lessons,
                              completion_reset=_reset_date(m.get("completion_reset"))))
    return Course(
        slug=course_slug,
        title=str(spec.get("title", "")),
        description=str(spec.get("description", "")),
        modules=modules,
        # Fail closed: only a bare YAML true, or no key at all, turns these on.
        # A quoted "false" must not quietly bring back homework or the paid cert.
        homework=spec.get("homework", True) is True,
        certifiable=spec.get("certifiable", True) is True,
    )


def list_courses(root: str | None = None) -> list:
    root = root or courses_root()
    out = []
    if not os.path.isdir(root):
        return out
    for name in sorted(os.listdir(root)):
        if os.path.isfile(os.path.join(root, name, "course.yaml")):
            out.append(load_course(name, root))
    return out


def render_body(body_md: str) -> str:
    return _md.markdown(body_md or "", extensions=["extra"])
