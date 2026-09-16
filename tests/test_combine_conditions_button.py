"""The Clinical summary control for Glen's combine. The row you are on survives and
the one you pick is absorbed, so the label on the button has to say which way round
it goes."""
import re

from dashboard.biofield_report_html import render_author_html, render_clinical_checklist

def _row(label):
    return {"label": label, "checked": False, "covered_by": "", "layer": None,
            "common_remedies": [], "stress_pattern": "", "remembered_pattern": "",
            "pattern_is_suggested": False}


ROWS = [_row("Cataracts"), _row("Cataract"), _row("Dry eye")]


def test_each_row_offers_the_other_rows_to_absorb():
    h = render_clinical_checklist(ROWS, [])
    block = h[h.index('data-label="Cataracts"'):h.index('data-label="Cataract"')]
    opts = re.findall(r"<option value=\"([^\"]+)\">", block)
    assert "Cataract" in opts and "Dry eye" in opts
    assert "Cataracts" not in opts          # a condition cannot absorb itself


def test_the_button_says_which_way_round_it_goes():
    h = render_clinical_checklist(ROWS, [])
    assert "Combine into this" in h


def test_a_lone_row_offers_no_combine():
    h = render_clinical_checklist([_row("Cataracts")], [])
    assert "Combine into this" not in h


def test_the_handler_is_on_the_page():
    rep = {"test_id": "a7", "client": {"name": "S", "email": "s@f.co"}, "date": "",
           "layers": [], "schedule": []}
    h = render_author_html(rep, [], "", clinical_checklist=ROWS)
    assert "function combineClinicalItems" in h
    assert "/author/a7/clinical-items/combine" in h
