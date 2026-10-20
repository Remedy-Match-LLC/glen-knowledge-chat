"""Review round 3 of the "As guided" change (2026-10-20). Each test names the
mutations that survived the first tests and that it now catches."""
import json
import pathlib
import re
import shutil
import smtplib
import subprocess

import pytest

from dashboard import biofield_clinical_sections as cs
from dashboard.biofield_schedule import build_schedule, guided_dose
from tests.test_biofield_report_split import INFO, _no_gate, _toggle, app_client  # noqa: F401

WT = pathlib.Path(__file__).resolve().parents[1]
PORTAL = WT / "static" / "client-portal.html"

LASER = {"name": "Harmony Laser", "dosage": "30 minutes", "frequency": "every other day",
         "timing": "as the laser sets itself. You can change the time and use less at first as you need."}
# Slot-less, and _placement still notes a food relationship for it.
BEFORE = {"name": "Lymph Laser", "dosage": "30 minutes", "frequency": "every other day",
          "timing": "before meals"}
UNSAFE = {"name": "Odd <i>", "dosage": "1 dropper", "frequency": "as desired",
          "timing": "<b>& later"}

# Each branch of the semicolon rule on its own, plus a capitalised "Contains:".
ONLY_LIST = {"dosage": "Aspen, Walnut", "frequency": "every other day",
             "contains": ["Aspen", "Walnut"]}
ONLY_DOSE = {"dosage": "contains: Aspen, Walnut", "frequency": "every other day"}
CAPITAL = {"dosage": "Contains: Aspen, Walnut", "frequency": "every other day"}


@pytest.fixture(autouse=True)
def _block_smtp(monkeypatch):
    def refuse(*a, **k):
        raise RuntimeError("no mail in tests")
    monkeypatch.setattr(smtplib, "SMTP", refuse)
    monkeypatch.setattr(smtplib, "SMTP_SSL", refuse)


def _node(prog):
    if not shutil.which("node"):
        pytest.skip("node not installed")
    out = subprocess.run(["node", "-e", prog], capture_output=True, text=True, check=True)
    return out.stdout


def _portal_js():
    """The portal's esc and scheduleHtml, as shipped."""
    src = PORTAL.read_text()
    esc = re.search(r"^const esc = .*?;$", src, re.M).group(0)
    fn = re.search(r"^function scheduleHtml\(sched\)\{.*?^\}", src, re.M | re.S).group(0)
    return esc + "\n" + fn


# --- the rule itself -------------------------------------------------------------

def test_a_contains_list_alone_gets_the_semicolon():
    """Kills G7: the contains-list branch of the semicolon rule."""
    assert guided_dose(ONLY_LIST) == "Aspen, Walnut; every other day"


def test_a_contains_dose_alone_gets_the_semicolon():
    """Kills G8: the "contains:" dose branch of the semicolon rule."""
    assert guided_dose(ONLY_DOSE) == "contains: Aspen, Walnut; every other day"


def test_a_capitalised_contains_still_gets_the_semicolon():
    """Kills G9: the "contains:" test ignores case."""
    assert guided_dose(CAPITAL) == "Contains: Aspen, Walnut; every other day"


def test_the_portal_rule_matches_on_each_branch_alone():
    """Kills J3, J4, J12: the node comparison only had entries where both branches held."""
    src = PORTAL.read_text()
    js = re.search(r"const guided = e=>\{.*?\n    \};", src, re.S).group(0)
    cases = [ONLY_LIST, ONLY_DOSE, CAPITAL]
    got = json.loads(_node(js + "\nconsole.log(JSON.stringify(" + json.dumps(cases)
                           + ".map(guided)));"))
    assert got == [guided_dose(c) for c in cases]


# --- the three surfaces, rendered --------------------------------------------------

def _rep(*remedies):
    return {"client": {"name": "K", "email": ""},
            "layers": [dict(r, layer=i + 1, head="Structure", remedy=r["name"])
                       for i, r in enumerate(remedies)],
            "schedule": build_schedule(list(remedies))}


def test_the_printed_report_escapes_the_guided_row():
    """Kills P2: authored text is escaped in the guided row of the printed report."""
    from dashboard.biofield_report_present import render_present
    html = render_present(_rep(UNSAFE), narrative="x")
    assert "(1 dropper, as desired, &lt;b&gt;&amp; later)" in html
    assert "<b>& later" not in html


def test_the_authoring_page_prints_the_whole_dose_once():
    """Kills H1, H2, H3, H4 by rendering, where the old test read the source. A slot-less
    remedy with a food note must not print "before meals" twice."""
    from dashboard.biofield_report_html import render_report_html
    html = render_report_html(_rep(LASER, BEFORE, UNSAFE))
    row = html[html.index("<td class=slot>As guided</td>"):]
    row = row[:row.index("</tr>")]
    assert "(30 minutes, every other day, as the laser sets itself." in row
    assert "(30 minutes, every other day, before meals)</span>" in row
    assert "(1 dropper, as desired, &lt;b&gt;&amp; later)" in row
    assert "<b>& later" not in html


def test_the_portal_row_prints_the_rule_escaped():
    """Kills J9, J10: runs the portal's scheduleHtml itself, not only its helper."""
    sched = build_schedule([LASER, UNSAFE])
    out = _node(_portal_js() + "\nprocess.stdout.write(scheduleHtml("
                + json.dumps(sched) + "));")
    row = out[out.index('sched-when">As guided<'):]
    assert "(30 minutes, every other day, as the laser sets itself." in row
    assert "Odd &lt;i&gt;" in row
    assert "(1 dropper, as desired, &lt;b&gt;&amp; later)" in row
    assert "<b>& later" not in out


# --- the walkthrough on a FileMaker test --------------------------------------------

def test_the_walkthrough_reads_a_filemaker_tests_own_switches(app_client, monkeypatch):
    """Kills V3: FileMaker test N reads "fmp:N", never authored test aN's switches."""
    import biofield_local_app as bla
    c = app_client
    n = c.tid.lstrip("a")                  # same number as the authored test, on purpose
    fake = {"client": {"name": "F", "email": "f@x.com"},
            "layers": [{"layer": 1, "head": "", "remedy": "Liver Support"},
                       {"layer": 2, "head": "", "remedy": INFO}]}
    real = bla.causal_chain_report
    monkeypatch.setattr(bla, "causal_chain_report",
                        lambda cx, tid: dict(fake) if str(tid) == n else real(cx, tid))
    seen = []

    def fake_script(rep, notes, complete, scan=None, problems_out=None):
        seen.append([l.get("remedy") for l in rep.get("layers") or []])
        return "Aloha"
    monkeypatch.setattr(bla, "generate_video_script", fake_script)
    r = c.post(f"/test/{n}/report-sections",
               json={"report": "biofield", "section": cs.INFOCEUTICALS, "on": False})
    assert r.status_code == 200 and r.get_json()["ok"], r.data
    assert c.post(f"/test/{n}/video-generate", json={}).get_json()["script"] == "Aloha"
    assert INFO not in seen[-1] and "Liver Support" in seen[-1]
