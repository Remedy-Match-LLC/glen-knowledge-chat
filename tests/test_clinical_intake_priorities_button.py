"""Glen, 2026-09-16: "Clinical Summary needs to have a button to pull in known
priorities from Intake." The priorities are the intake's Top Health Goals table,
kept in the order the client typed them."""
import re

from dashboard.biofield_report_html import render_author_html, render_clinical_checklist


def _strip(html):
    """Just the intake block. The page's CSS contains 'font-weight' and its subtitle
    already says 'from intake', so a whole-page search matches the wrong things."""
    m = re.search(r"<ol class=intake-list>.*?</ol>", html, re.S)
    return m.group(0) if m else ""


def _html(priorities):
    rep = {"test_id": "a7", "client": {"name": "J", "email": "j@x.com"}, "date": "",
           "layers": [], "schedule": []}
    return render_author_html(rep, [], "", intake_priorities=priorities)


PRIORITIES = [
    {"concern": "eyes", "rating": 1, "years_since_onset": 2020},
    {"concern": "hormones", "rating": 2, "years_since_onset": None},
    {"concern": "weight", "rating": None, "years_since_onset": 20},
]


def test_button_and_handler_render_when_intake_has_priorities():
    h = _html(PRIORITIES)
    assert "Pull 3 from intake" in h
    assert "function pullIntakePriorities" in h
    assert "/author/a7/clinical-items" in h


def test_priorities_render_in_form_order_not_sorted_by_rating():
    strip = _strip(render_clinical_checklist([], [], intake_priorities=PRIORITIES))
    assert strip.index("eyes") < strip.index("hormones") < strip.index("weight")


def test_rating_and_onset_are_shown_beside_each_concern():
    strip = _strip(render_clinical_checklist([], [], intake_priorities=PRIORITIES))
    assert "rated 1" in strip
    assert "20 years" in strip
    # A missing rating must not render as None or 0.
    assert "rated None" not in strip and "None years" not in strip


def test_a_concern_already_on_the_checklist_is_marked_as_there():
    h = render_clinical_checklist([{"label": "Eyes", "checked": False, "covered_by": "",
                                    "layer": None, "common_remedies": [],
                                    "stress_pattern": "", "remembered_pattern": "",
                                    "pattern_is_suggested": False}],
                                  [], intake_priorities=PRIORITIES)
    assert "already listed" in _strip(h)


def test_nothing_renders_without_intake_priorities():
    h = render_clinical_checklist([], [], intake_priorities=[])
    assert "<div class=intake-priorities>" not in h   # the CSS always ships
    assert "Pull" not in h


def test_a_calendar_year_onset_reads_as_a_date_not_a_duration():
    """The column asks for 'years since onset' and clients answer both ways. Sharon
    Connour entered 2020 and 2012 (calendar years) while Steve Fox entered 3 and 15
    (durations). Rendering hers verbatim would read '2020 years'."""
    strip = _strip(render_clinical_checklist([], [], intake_priorities=[
        {"concern": "eyes", "rating": None, "years_since_onset": 2020},
        {"concern": "digestion", "rating": None, "years_since_onset": 15},
    ]))
    assert "since 2020" in strip
    assert "2020 years" not in strip
    assert "15 years" in strip
