"""The Kloud mat's program follows the scan's Phase (Glen, 2026-10-18).

"yes, the Phase always sets the Kloud program". Phases 1 to 5 map to programs 3, 4,
5, 1 and 2 (Energize, Synchronize, Transform, Relax, Balance). The Phase comes from
this test, else the client's most recent earlier Biofield with one, else the Intake
form's terrain answer. With none, the line names no program and Glen's note says so.
"""
import sqlite3

import pytest

from dashboard import biofield_clinical_sections as cs
from dashboard import biofield_plan_report as pr
from dashboard import kloud_program as kp

KLOUD = "Kloud PEMF Mat (Maxi)"


@pytest.fixture
def cx(tmp_path):
    con = sqlite3.connect(str(tmp_path / "chat_log.db"))
    con.execute("""CREATE TABLE biofield_auth_tests (id INTEGER PRIMARY KEY, name TEXT,
        email TEXT, date_test TEXT, phase INTEGER, client_id TEXT, e4l_client_id TEXT)""")
    yield con
    con.close()


def _test(cx, tid, email="pat@example.com", date="2026-10-16", phase=None, client_id=None,
          e4l=None):
    cx.execute("INSERT INTO biofield_auth_tests (id,email,date_test,phase,client_id,"
               "e4l_client_id) VALUES (?,?,?,?,?,?)", (tid, email, date, phase, client_id, e4l))


@pytest.mark.parametrize("phase,label", [(1, "Program 3 (Energize)"), (2, "Program 4 (Synchronize)"),
                                         (3, "Program 5 (Transform)"), (4, "Program 1 (Relax)"),
                                         (5, "Program 2 (Balance)"), ("4", "Program 1 (Relax)"),
                                         (None, ""), (0, ""), (6, ""), ("phase 3", "")])
def test_each_phase_names_its_program(phase, label):
    assert kp.program_label(phase) == label


def test_this_tests_phase_wins_and_the_intake_is_never_fetched(cx):
    _test(cx, 40, date="2026-10-01", phase=5)
    _test(cx, 49, phase=2)
    asked = []
    assert kp.resolve(cx, "a49", None, lambda: asked.append(1) or {"terrain": "1"}) == \
        (2, "this Biofield")
    assert kp.resolve(cx, "a49", 3) == (3, "this Biofield")
    assert asked == []


def test_the_most_recent_earlier_biofield_with_a_phase_is_next(cx):
    _test(cx, 30, date="2026-09-01", phase=1)
    _test(cx, 35, date="2026-09-20", phase=4)
    _test(cx, 38, date="2026-10-01")            # no Phase: skipped
    _test(cx, 49, date="2026-10-16")
    _test(cx, 55, date="2026-10-20", phase=5)   # later: never used
    _test(cx, 41, email="other@example.com", date="2026-10-10", phase=3)  # another client
    assert kp.resolve(cx, "a49", None, lambda: {"terrain": "2"}) == \
        (4, "the Biofield of 2026-09-20")


def test_the_same_date_counts_only_a_lower_id(cx):
    _test(cx, 48, date="2026-10-16", phase=1)
    _test(cx, 49, date="2026-10-16")
    _test(cx, 50, date="2026-10-16", phase=5)
    assert kp.resolve(cx, "a49")[0] == 1
    assert kp.resolve(cx, "a50")[0] == 5


def test_the_same_client_by_email_case_or_client_id(cx):
    _test(cx, 30, email=" Pat@Example.com ", date="2026-09-01", phase=3)
    _test(cx, 31, email="new@example.com", date="2026-09-02", phase=4, client_id="c7")
    _test(cx, 49, client_id="c7")
    assert kp.earlier_phase(cx, "a49") == (4, "2026-09-02")
    cx.execute("UPDATE biofield_auth_tests SET client_id=NULL WHERE id=31")
    assert kp.earlier_phase(cx, "a49") == (3, "2026-09-01")


def test_no_identity_matches_nobody(cx):
    _test(cx, 30, email="", date="2026-09-01", phase=3)
    _test(cx, 49, email="")
    assert kp.earlier_phase(cx, "a49") == (None, "")


def test_the_intake_terrain_is_last_and_then_nothing(cx):
    _test(cx, 49)
    assert kp.resolve(cx, "a49", None, lambda: {"terrain": "3"}) == (3, "the Intake form")
    assert kp.resolve(cx, "a49", None, lambda: {"terrain": ""}) == (None, "")
    assert kp.resolve(cx, "a49", None, lambda: None) == (None, "")
    assert kp.resolve(cx, "a49") == (None, "")


def test_a_test_with_no_auth_row_uses_its_own_phase_then_the_intake(cx):
    assert kp.resolve(cx, "1234", 5) == (5, "this Biofield")
    assert kp.resolve(cx, "1234", None, lambda: {"terrain": "1"}) == (1, "the Intake form")


def test_filemaker_dates_compare_as_dates_and_unreadable_ones_never_count(cx):
    _test(cx, 1, date="6/24/26", phase=2)
    _test(cx, 20, date="2026-05-01", phase=4)
    _test(cx, 30, date="2026-07-01", phase=5)     # after 6/24/26
    _test(cx, 31, date="someday", phase=1)
    assert kp.earlier_phase(cx, "a1") == (4, "2026-05-01")
    _test(cx, 49, date="2026-07-02")
    assert kp.earlier_phase(cx, "a49") == (5, "2026-07-01")


def test_an_undated_test_looks_at_none_and_an_undated_one_is_never_used(cx):
    _test(cx, 30, date="", phase=3)
    _test(cx, 49, date="2026-10-16")
    assert kp.earlier_phase(cx, "a49") == (None, "")
    _test(cx, 50, date="", email="b@example.com")
    _test(cx, 20, date="2026-01-01", phase=1, email="b@example.com")
    assert kp.earlier_phase(cx, "a50") == (None, "")


def test_the_same_client_by_e4l_id(cx):
    _test(cx, 30, email="old@example.com", date="2026-09-01", phase=5, e4l="E9")
    _test(cx, 49, email="new@example.com", e4l="E9")
    assert kp.earlier_phase(cx, "a49") == (5, "2026-09-01")


def test_a_test_with_no_authored_row_finds_its_clients_earlier_biofield(cx):
    _test(cx, 30, date="2026-09-01", phase=4)
    _test(cx, 60, date="2026-11-01", phase=1)
    assert kp.resolve(cx, "1234", None, lambda: {"terrain": "1"},
                      "Pat@example.com", "2026-10-16") == (4, "the Biofield of 2026-09-01")


def _item(name):
    return {"name": name, "overall": 7, "research": 5, "uniqueness": 5, "reason": "r",
            "source": "s", "botanical": False, "lowers_bp": None,
            "dose": "15 minutes on the mat, twice daily"}


def test_only_the_kloud_line_names_the_program(cx):
    for name in (KLOUD, "Harmony Laser"):
        cs.upsert_item(cx, "Gut", cs.HEALING_TOOLS, _item(name), origin_test="a49")
        cs.save_pick(cx, "a49", "Gut", cs.HEALING_TOOLS, name, checked=True)
    page = pr.build(cx, "a49", "Pat", "", "Program 3 (Energize)")[0]
    assert page.count("<span class=plan-program>Program 3 (Energize)</span>") == 1
    k = page.index(KLOUD)
    assert k < page.index("Program 3 (Energize)") < page.index("15 minutes", k)
    assert "<span class=plan-program>" not in pr.build(cx, "a49", "Pat", "", "")[0]
    assert "&lt;b&gt;" in pr.build(cx, "a49", "Pat", "", "<b>")[0]


def _app(tmp_path, monkeypatch, phase=None, intake=None):
    """The local app on a test with one Kloud item under Gut, ticked."""
    monkeypatch.delenv("CONSOLE_SECRET", raising=False)
    import dashboard
    monkeypatch.setattr(dashboard, "CONSOLE_SECRET", "", raising=False)
    import biofield_local_app as bla
    from dashboard.biofield_authoring import create_test, init_auth_tables
    monkeypatch.setattr(bla, "_fetch_intake_answers", lambda email: intake)
    db = str(tmp_path / "chat_log.db")
    with sqlite3.connect(db) as con:
        init_auth_tables(con)
        tid = create_test(con, "Pat", "pat@example.com", "2026-10-18")
        con.execute("UPDATE biofield_auth_tests SET phase=? WHERE id=?", (phase, int(tid[1:])))
        cs.upsert_item(con, "Gut", cs.HEALING_TOOLS, _item(KLOUD), origin_test=tid)
        cs.save_pick(con, tid, "Gut", cs.HEALING_TOOLS, KLOUD, checked=True)
        con.commit()
    published = []
    app = bla.create_app(db, fetch_profile=lambda email: {"conditions": ["Gut"]},
                         scan_lookup=lambda email: {"found": False},
                         auto_publish=lambda *a, **k: published.append(a) or {})
    return tid, app.test_client(), published


def test_the_plan_page_names_the_program_from_the_saved_phase(tmp_path, monkeypatch):
    tid, client, _ = _app(tmp_path, monkeypatch, phase=4)
    page = client.get(f"/test/{tid}/plan").get_data(as_text=True)
    assert "<span class=plan-program>Program 1 (Relax)</span>" in page
    assert "names no program" not in page


def test_with_no_phase_anywhere_glen_is_told_and_nothing_is_guessed(tmp_path, monkeypatch):
    tid, client, _ = _app(tmp_path, monkeypatch)
    page = client.get(f"/test/{tid}/plan").get_data(as_text=True)
    assert "<span class=plan-program>" not in page
    assert "The Kloud line names no program." in page
    assert ".plan-glen{display:none}" in page


def test_the_intake_fallback_reaches_the_page(tmp_path, monkeypatch):
    tid, client, _ = _app(tmp_path, monkeypatch, intake={"terrain": "5"})
    page = client.get(f"/test/{tid}/plan").get_data(as_text=True)
    assert "<span class=plan-program>Program 2 (Balance)</span>" in page


def test_the_summary_shows_the_program_and_where_it_came_from(tmp_path, monkeypatch):
    tid, client, _ = _app(tmp_path, monkeypatch, phase=1)
    page = client.get(f"/author/{tid}").get_data(as_text=True)
    assert "Program 3 (Energize) <span class=clinical-section-program-src>from this Biofield" \
        in page


def test_the_summary_says_when_there_is_no_program(tmp_path, monkeypatch):
    tid, client, _ = _app(tmp_path, monkeypatch)
    page = client.get(f"/author/{tid}").get_data(as_text=True)
    assert "No Kloud program: no Phase on this or an earlier Biofield" in page


def test_a_plan_without_a_kloud_line_looks_nothing_up(tmp_path, monkeypatch):
    tid, client, _ = _app(tmp_path, monkeypatch)
    import biofield_local_app as bla
    asked = []
    monkeypatch.setattr(bla, "_fetch_intake_answers", lambda email: asked.append(email))
    import sqlite3 as _s
    with _s.connect(str(tmp_path / "chat_log.db")) as con:
        cs.save_pick(con, tid, "Gut", cs.HEALING_TOOLS, KLOUD, checked=False)
    page = client.get(f"/test/{tid}/plan").get_data(as_text=True)
    assert asked == [] and "names no program" not in page
    client.get(f"/author/{tid}")
    assert asked == []
    assert "No Kloud program" not in client.get(f"/author/{tid}").get_data(as_text=True)
    with _s.connect(str(tmp_path / "chat_log.db")) as con:
        cs.save_pick(con, tid, "Gut", cs.HEALING_TOOLS, KLOUD, checked=True)
    client.get(f"/test/{tid}/plan")
    assert asked


@pytest.fixture
def real_cx(tmp_path):
    """The schema the app builds for itself, which has no client_id column."""
    from dashboard.biofield_authoring import init_auth_tables
    con = sqlite3.connect(str(tmp_path / "fresh.db"))
    init_auth_tables(con)
    yield con
    con.close()


def _real(cx, tid, email, date, phase=None, e4l=None):
    cx.execute("INSERT INTO biofield_auth_tests (id,name,email,date_test,phase,e4l_client_id) "
               "VALUES (?,?,?,?,?,?)", (tid, "Pat", email, date, phase, e4l))


def test_the_apps_own_schema_reads_the_saved_phase_and_matches_by_e4l_id(real_cx):
    assert "client_id" not in {r[1] for r in real_cx.execute(
        "PRAGMA table_info(biofield_auth_tests)")}
    _real(real_cx, 30, "old@example.com", "2026-09-01", phase=5, e4l="E9")
    _real(real_cx, 48, "pat@example.com", "2026-10-16", phase=1)
    _real(real_cx, 49, "pat@example.com", "2026-10-16", e4l="E9")
    _real(real_cx, 50, "x@example.com", "2026-10-17", phase=2)
    assert kp.resolve(real_cx, "a50") == (2, "this Biofield")
    assert kp.resolve(real_cx, "a49") == (1, "the Biofield of 2026-10-16")
    real_cx.execute("DELETE FROM biofield_auth_tests WHERE id=48")
    assert kp.resolve(real_cx, "a49") == (5, "the Biofield of 2026-09-01")


def test_a_test_with_no_authored_row_never_counts_one_on_its_own_date(cx):
    _test(cx, 30, date="2026-10-16", phase=4)
    assert kp.resolve(cx, "1234", None, None, "pat@example.com", "2026-10-16") == (None, "")
    assert kp.resolve(cx, "1234", None, None, "pat@example.com", "2026-10-17")[0] == 4


def test_the_report_with_the_plan_names_the_program(tmp_path, monkeypatch):
    tid, client, published = _app(tmp_path, monkeypatch, phase=3)
    import biofield_local_app as bla
    written = []

    def fake_pdf(html, out):
        written.append(html)
        open(out, "wb").write(b"%PDF fake")
        return out
    monkeypatch.setattr(bla, "save_report_pdf", fake_pdf)
    monkeypatch.setenv("BIOFIELD_REPORTS_DIR", str(tmp_path))
    client.get(f"/test/{tid}/report.pdf?plan=1")
    assert written and "<span class=plan-program>Program 5 (Transform)</span>" in written[-1]
    assert len(published) == 1   # recorded by the stub, never run
