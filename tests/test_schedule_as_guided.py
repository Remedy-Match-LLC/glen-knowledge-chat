"""The Remedy Schedule's row for remedies with no daily slot reads "As guided", and
shows each one's whole dose.

Glen, 2026-10-19: "I typically like "As guided" vs "As directed"." Clinical found
Harmony Laser (30 minutes, every other day, timed by the laser) printed only
"(30 minutes)" under a heading that contradicted it.
"""
import pathlib

from dashboard.biofield_schedule import AS_GUIDED, build_schedule, guided_dose

LASER = {"name": "Harmony Laser", "dosage": "30 minutes", "frequency": "every other day",
         "timing": "as the laser sets itself. You can change the time and use less at first as you need."}


def _entry():
    sched = build_schedule([LASER])
    [e] = [e for e in sched["entries"] if e["name"] == "Harmony Laser"]
    assert e["as_directed"]          # no daily slot: it lands in the guided row
    return e


def test_the_guided_dose_keeps_dose_frequency_and_timing():
    assert guided_dose(_entry()) == (
        "30 minutes, every other day, as the laser sets itself. You can change the time "
        "and use less at first as you need.")


def test_terrain_restore_keeps_its_contents_apart_from_its_timing():
    """Review round 1: "contains: Aspen, Walnut, every other day" read as three items."""
    e = {"dosage": "contains: Aspen, Walnut", "frequency": "every other day",
         "timing": "under the tongue", "contains": ["Aspen", "Walnut"]}
    assert guided_dose(e) == "contains: Aspen, Walnut; every other day, under the tongue"


def test_a_full_stop_never_meets_a_comma():
    e = {"dosage": "30 minutes.", "frequency": "every other day.", "timing": "as guided."}
    assert guided_dose(e) == "30 minutes, every other day, as guided."


def test_the_portal_copy_of_the_rule_matches(tmp_path):
    """Review round 1 (L3): the rule lives in Python and in the portal's script. Run the
    portal's copy under node on the same entries and compare."""
    import re
    import shutil
    import subprocess
    if not shutil.which("node"):
        import pytest
        pytest.skip("node not installed")
    src = (pathlib.Path(__file__).resolve().parents[1] / "static" / "client-portal.html").read_text()
    js = re.search(r"const guided = e=>\{.*?\n    \};", src, re.S).group(0)
    cases = [LASER, {"dosage": "contains: Aspen, Walnut", "frequency": "every other day",
                     "timing": "", "contains": ["Aspen", "Walnut"]},
             {"dosage": "30 minutes.", "frequency": "", "timing": "at night"}, {}]
    import json
    prog = js + "\nconsole.log(JSON.stringify(" + json.dumps(cases) + ".map(guided)));"
    got = json.loads(subprocess.run(["node", "-e", prog], capture_output=True, text=True,
                                    check=True).stdout)
    assert got == [guided_dose(c) for c in cases]


def test_a_remedy_with_nothing_written_reads_as_guided():
    assert guided_dose({"dosage": " ", "frequency": "", "timing": None}) == "as guided"
    assert AS_GUIDED == "As guided"


def test_the_printed_report_shows_the_whole_dose():
    from dashboard.biofield_report_present import render_present
    rep = {"client": {"name": "K", "email": ""},
           "layers": [dict(LASER, layer=1, head="Structure", remedy="Harmony Laser")],
           "schedule": build_schedule([LASER])}
    html = render_present(rep, narrative="x")
    assert "As guided" in html and "As directed" not in html
    # The schedule row itself, not the layer table that also prints the frequency.
    assert "(30 minutes, every other day, as the laser sets itself." in html


def test_the_authoring_page_shows_the_whole_dose():
    from dashboard import biofield_report_html as rh
    src = pathlib.Path(rh.__file__).read_text()
    assert "<td class=slot>As directed" not in src
    assert "guided_dose(e)" in src


def test_the_portal_uses_the_same_wording():
    src = (pathlib.Path(__file__).resolve().parents[1] / "static" / "client-portal.html").read_text()
    assert 'sched-when">As guided<' in src and 'sched-when">As directed<' not in src
    assert "[e.dosage,e.frequency,e.timing]" in src
