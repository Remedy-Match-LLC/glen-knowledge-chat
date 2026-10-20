"""Glen's cautions paragraph (cautions_lead), review round 3, 2026-10-20. Each test
catches a change the first tests let through."""
import json
import sqlite3

import pytest

from dashboard import biofield_clinical_sections as cs
from dashboard import biofield_plan_report as pr
from tests.test_biofield_plan_frame import BERBERINE, _folder, _page, _remedy

LEAD = "If you experience intense symptoms like vertigo, please let me know right away."
BOX = "<div class=plan-cautions>"


@pytest.fixture
def cx(tmp_path):
    con = sqlite3.connect(str(tmp_path / "chat_log.db"))
    yield con
    con.close()


def test_the_loader_reports_cautions_lead_as_known(cx, tmp_path):
    """The shipped test reads report.get("unknown"), a key load_content never sets,
    so its assertion always passes. The real key is frame_lines_unknown."""
    report = pr.load_content(cx, _folder(tmp_path, {
        "cautions_lead": {"text": LEAD, "approved": True}}))
    assert "frame_lines_unknown" in report
    assert report["frame_lines_unknown"] == []
    assert report["frame_lines_applied"] == 1


def test_no_paragraph_and_no_caution_prints_no_empty_box(cx, tmp_path):
    """Intent 5: absent paragraph, nothing else changes. Kills M8."""
    pr.load_content(cx, _folder(tmp_path, {}))
    _remedy(cx, "Blood sugar", cs.FORMULATIONS, "Glucose Tolerance")
    assert BOX not in _page(cx)


def test_an_unapproved_paragraph_leaves_no_empty_box(cx, tmp_path):
    pr.load_content(cx, _folder(tmp_path, {"cautions_lead": {"text": LEAD,
                                                             "approved": False}}))
    _remedy(cx, "Blood sugar", cs.FORMULATIONS, "Glucose Tolerance")
    assert BOX not in _page(cx)


@pytest.mark.parametrize("cautions", [None, BERBERINE])
def test_the_paragraph_sits_inside_the_cautions_box(cx, tmp_path, cautions):
    """Intent 2: first in the cautions area. Kills M10."""
    pr.load_content(cx, _folder(tmp_path, {"cautions_lead": {"text": LEAD, "approved": True}},
                                cautions=cautions))
    _remedy(cx, "Blood sugar", cs.FORMULATIONS, "Glucose Tolerance")
    page = _page(cx)
    assert page.count(BOX) == 1
    start = page.index(BOX)
    assert page.index(BOX + "<p class=plan-caution-lead>" + LEAD) == start


def test_the_paragraph_carries_its_own_class(cx, tmp_path):
    """Kills M11. Low value today: no CSS rule targets plan-caution-lead."""
    pr.load_content(cx, _folder(tmp_path, {"cautions_lead": {"text": LEAD, "approved": True}}))
    _remedy(cx, "Blood sugar", cs.FORMULATIONS, "Glucose Tolerance")
    page = _page(cx)
    assert f"<p class=plan-caution-lead>{LEAD}</p>" in page
    assert f"<p class=plan-caution-intro>{LEAD}" not in page
