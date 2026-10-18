"""Purple infoceuticals tick themselves (Glen, 2026-10-18).

"yes, tick purple infoceuticals automatically on every new scan", and then
"only scans dated from today".
"""
import sqlite3

import pytest

from dashboard import biofield_clinical_sections as cs
from dashboard.biofield_clinical_proposals import selections

ED5 = "ED5 Circulation Energetic Driver Infoceutical"
EI10 = "EI10 Circulation/Pericardium Meridian Energetic Integrator Infoceutical"
ET11 = "ET11 Thymus Energetic Tissue Infoceutical"
PURPLE = {"scan_id": "7", "recommended": {"EI10"}, "purple": {"ED5", "EI10"},
          "colours": {"ED5": ["purple"], "EI10": ["purple"], "ET11": ["green"]},
          "purple_error": ""}


@pytest.fixture
def cx(tmp_path):
    con = sqlite3.connect(str(tmp_path / "chat_log.db"))
    for name in (ED5, EI10, ET11):
        cs.upsert_item(con, "Blood", cs.INFOCEUTICALS, {
            "name": name, "overall": 8, "research": 1, "uniqueness": 6, "reason": "why",
            "source": "src", "botanical": False, "lowers_bp": None})
    con.commit()
    yield con
    con.close()


def _info(sections):
    return {e["code"]: e for s in sections if s["key"] == cs.INFOCEUTICALS
            for e in s["items"]}


def _run(cx, chosen=(), marks=PURPLE, date="2026-10-18", scan_id="7"):
    secs = cs.sections_for(cx, "a49", "Blood", [], list(chosen), marks=marks)
    added = cs.auto_tick_purple(cx, "a49", "Blood", secs, list(chosen), scan_id, date)
    cx.commit()
    return added


def test_purple_ones_are_ticked_and_marked(cx):
    assert sorted(_run(cx, chosen=["Fibrosolve"])) == sorted([ED5, EI10])
    chosen = selections(cx, "a49")["blood"]
    assert chosen[0] == "Fibrosolve" and set(chosen[1:]) == {ED5, EI10}
    info = _info(cs.sections_for(cx, "a49", "Blood", [], chosen, marks=PURPLE))
    assert (info["ED5"]["checked"], info["ED5"]["auto_ticked"]) == (True, True)
    assert (info["ET11"]["checked"], info["ET11"]["auto_ticked"]) == (False, False)


def test_a_scan_before_today_ticks_nothing(cx):
    assert _run(cx, date="2026-10-17") == []
    assert _run(cx, date="") == []
    assert "blood" not in selections(cx, "a49")


def test_unread_colours_tick_nothing(cx):
    unread = dict(PURPLE, purple=None, colours=None, purple_error="no PDF")
    assert _run(cx, marks=unread) == []
    assert _run(cx, marks=None) == []
    assert "blood" not in selections(cx, "a49")


def test_an_untick_by_hand_stays_unticked(cx):
    _run(cx)
    cs_sel = selections(cx, "a49")["blood"]
    kept = [n for n in cs_sel if n != ED5]
    from dashboard.biofield_clinical_proposals import save_selection
    save_selection(cx, "a49", "Blood", kept)
    cx.commit()
    assert _run(cx, chosen=kept) == []
    assert ED5 not in selections(cx, "a49")["blood"]


def test_a_manual_tick_is_never_taken_off_or_called_auto(cx):
    assert _run(cx, chosen=[ED5, ET11]) == [EI10]
    chosen = selections(cx, "a49")["blood"]
    assert chosen == [ED5, ET11, EI10]
    info = _info(cs.sections_for(cx, "a49", "Blood", [], chosen, marks=PURPLE))
    assert info["ED5"]["auto_ticked"] is False and info["EI10"]["auto_ticked"] is True


def test_a_new_scan_ticks_again(cx):
    _run(cx)
    from dashboard.biofield_clinical_proposals import save_selection
    save_selection(cx, "a49", "Blood", [])
    cx.commit()
    assert _run(cx, chosen=[], scan_id="7") == []
    assert sorted(_run(cx, chosen=[], scan_id="8")) == sorted([ED5, EI10])


def test_page_ticks_a_dated_scan_and_shows_the_mark(tmp_path, monkeypatch):
    monkeypatch.delenv("CONSOLE_SECRET", raising=False)
    import dashboard
    monkeypatch.setattr(dashboard, "CONSOLE_SECRET", "", raising=False)
    from dashboard import e4l_scan_marks
    from dashboard.biofield_authoring import create_test, init_auth_tables
    from biofield_local_app import create_app
    db = str(tmp_path / "chat_log.db")
    with sqlite3.connect(db) as con:
        init_auth_tables(con)
        tid = create_test(con, "Pam", "pam@example.com", "2026-10-18")
        cs.upsert_item(con, "Fatigue", cs.INFOCEUTICALS, {
            "name": ED5, "overall": 8, "research": 1, "uniqueness": 6, "reason": "why",
            "source": "src", "botanical": False, "lowers_bp": None})
        cs.upsert_item(con, "Fatigue", cs.INFOCEUTICALS, {
            "name": ET11, "overall": 8, "research": 1, "uniqueness": 6, "reason": "why",
            "source": "src", "botanical": False, "lowers_bp": None})
        con.commit()
    monkeypatch.setattr(e4l_scan_marks, "infoceutical_marks",
                        lambda c, e, sid: dict(PURPLE, scan_id=str(sid)))
    scan = {"found": True, "scan_id": 7, "scan_date": "2026-10-18"}
    app = create_app(db, fetch_profile=lambda email: {"conditions": ["Fatigue"]},
                     scan_lookup=lambda email: scan)
    page = app.test_client().get(f"/author/{tid}").get_data(as_text=True)
    with sqlite3.connect(db) as con:
        assert selections(con, tid)["fatigue"] == [ED5]
    assert page.count("auto: purple") == 1

    scan["scan_date"] = "2026-10-17"
    tid2 = None
    with sqlite3.connect(db) as con:
        tid2 = create_test(con, "Ann", "ann@example.com", "2026-10-17")
    app.test_client().get(f"/author/{tid2}")
    with sqlite3.connect(db) as con:
        assert "fatigue" not in selections(con, tid2)


def test_an_untick_on_a_new_scan_also_stays_and_loses_its_mark(cx):
    from dashboard.biofield_clinical_proposals import save_selection
    _run(cx)
    save_selection(cx, "a49", "Blood", [])
    cx.commit()
    _run(cx, scan_id="8")
    save_selection(cx, "a49", "Blood", [EI10])
    cx.commit()
    assert _run(cx, chosen=[EI10], scan_id="8") == []
    assert selections(cx, "a49")["blood"] == [EI10]
    marks = dict(PURPLE, scan_id="8")
    info = _info(cs.sections_for(cx, "a49", "Blood", [], [EI10], marks=marks))
    assert info["ED5"]["auto_ticked"] is False and info["EI10"]["auto_ticked"] is True


def test_a_bad_date_ticks_nothing(cx):
    for date in ("unknown", "2026-9-30", "2027-02-30", None):
        assert _run(cx, date=date) == [], date
    assert _run(cx, date="2026-10-19") != []


def test_a_saved_selection_read_now_wins_over_the_page_snapshot(cx):
    from dashboard.biofield_clinical_proposals import save_selection
    save_selection(cx, "a49", "Blood", ["Fibrosolve", ET11])
    cx.commit()
    # The page's snapshot is older and has none of them.
    _run(cx, chosen=[])
    assert selections(cx, "a49")["blood"][:2] == ["Fibrosolve", ET11]


def test_an_item_another_tab_already_claimed_is_not_ticked_again(cx):
    secs = cs.sections_for(cx, "a49", "Blood", [], [], marks=PURPLE)
    assert sorted(cs.auto_tick_purple(cx, "a49", "Blood", secs, [], "7", "2026-10-18")) \
        == sorted([ED5, EI10])
    cx.commit()
    from dashboard.biofield_clinical_proposals import save_selection
    save_selection(cx, "a49", "Blood", [])
    cx.commit()
    # The same stale sections, as a second tab would hold them.
    assert cs.auto_tick_purple(cx, "a49", "Blood", secs, [], "7", "2026-10-18") == []


def test_a_hand_re_tick_is_not_called_auto(cx, tmp_path, monkeypatch):
    monkeypatch.delenv("CONSOLE_SECRET", raising=False)
    import dashboard
    monkeypatch.setattr(dashboard, "CONSOLE_SECRET", "", raising=False)
    from biofield_local_app import create_app
    db = str(tmp_path / "chat_log.db")
    _run(cx)
    client = create_app(db).test_client()
    url = "/author/a49/clinical-items/selection"
    client.post(url, json={"label": "Blood", "remedies": [EI10]})
    client.post(url, json={"label": "Blood", "remedies": [EI10, ED5]})
    info = _info(cs.sections_for(cx, "a49", "Blood", [], [EI10, ED5], marks=PURPLE))
    assert info["ED5"]["auto_ticked"] is False and info["EI10"]["auto_ticked"] is True
    # Untick EI10 outside the routes, so its auto row still says added; then the
    # All box ticks it again. That tick is the box's, never "auto".
    from dashboard.biofield_clinical_proposals import save_selection
    save_selection(cx, "a49", "Blood", [ED5])
    cx.commit()
    client.post("/author/a49/clinical-sections/all", json={
        "label": "Blood", "section": cs.INFOCEUTICALS, "on": True,
        "names": [ED5, EI10, ET11], "remedies": [ED5]})
    chosen = selections(cx, "a49")["blood"]
    info = _info(cs.sections_for(cx, "a49", "Blood", [], chosen, marks=PURPLE))
    assert info["EI10"]["checked"] is True and info["EI10"]["auto_ticked"] is False


def _page_app(tmp_path, monkeypatch, conditions, date="2026-10-18"):
    monkeypatch.delenv("CONSOLE_SECRET", raising=False)
    import dashboard
    monkeypatch.setattr(dashboard, "CONSOLE_SECRET", "", raising=False)
    from dashboard import e4l_scan_marks
    from dashboard.biofield_authoring import create_test, init_auth_tables
    from biofield_local_app import create_app
    db = str(tmp_path / "chat_log.db")
    with sqlite3.connect(db) as con:
        init_auth_tables(con)
        tid = create_test(con, "Pam", "pam@example.com", date)
        for label in conditions:
            cs.upsert_item(con, label, cs.INFOCEUTICALS, {
                "name": ED5, "overall": 8, "research": 1, "uniqueness": 6,
                "reason": "why", "source": "src", "botanical": False, "lowers_bp": None})
        con.commit()
    monkeypatch.setattr(e4l_scan_marks, "infoceutical_marks",
                        lambda c, e, sid: dict(PURPLE, scan_id=str(sid)))
    app = create_app(db, fetch_profile=lambda email: {"conditions": list(conditions)},
                     scan_lookup=lambda email: {"found": True, "scan_id": 7,
                                                "scan_date": date})
    return db, tid, app.test_client()


def test_a_failure_on_a_later_row_saves_nothing(tmp_path, monkeypatch):
    real = cs.auto_tick_purple
    calls = []

    def flaky(cx, test_id, label, *a):
        calls.append(label)
        if len(calls) == 2:
            raise RuntimeError("second row fails")
        return real(cx, test_id, label, *a)

    db, tid, client = _page_app(tmp_path, monkeypatch, ["Fatigue", "Insomnia"])
    monkeypatch.setattr(cs, "auto_tick_purple", flaky)
    page = client.get(f"/author/{tid}").get_data(as_text=True)
    assert len(calls) == 2 and "auto: purple" not in page
    with sqlite3.connect(db) as con:
        assert selections(con, tid) == {}
        assert con.execute("SELECT COUNT(*) FROM biofield_clinical_autotick").fetchone()[0] == 0
    # Working again, both rows tick on the next open.
    monkeypatch.setattr(cs, "auto_tick_purple", real)
    client.get(f"/author/{tid}")
    with sqlite3.connect(db) as con:
        assert set(selections(con, tid)) == {"fatigue", "insomnia"}


def test_a_chain_tick_on_an_unsaved_row_is_kept(cx):
    """No saved selection: the page shows the chain remedy ticked, so the first
    auto tick must keep it (review round 1)."""
    assert cs.row_ticks({"covered_by": "Fibrosolve"}) == ["Fibrosolve"]
    assert cs.row_ticks({"covered_by": " "}) == []
    assert cs.row_ticks({"selection_saved": True, "selected_remedies": [],
                         "covered_by": "Fibrosolve"}) == []
    item = {"label": "Blood", "covered_by": "Fibrosolve"}
    secs = cs.sections_for(cx, "a49", "Blood", ["Fibrosolve"], [], marks=PURPLE)
    cs.auto_tick_purple(cx, "a49", "Blood", secs, cs.row_ticks(item), "7", "2026-10-18")
    cx.commit()
    assert selections(cx, "a49")["blood"][0] == "Fibrosolve"


def test_the_page_seeds_an_unsaved_row_from_its_chain_tick(tmp_path, monkeypatch):
    from dashboard import biofield_clinical_checklist as ck
    real_build = ck.build

    def build(*a, **k):
        rows = real_build(*a, **k)
        for r in rows:
            r["covered_by"], r["checked"] = "Fibrosolve", True
        return rows

    monkeypatch.setattr(ck, "build", build)
    db, tid, client = _page_app(tmp_path, monkeypatch, ["Fatigue"])
    client.get(f"/author/{tid}")
    with sqlite3.connect(db) as con:
        assert selections(con, tid)["fatigue"] == ["Fibrosolve", ED5]


def test_two_spellings_of_one_code_tick_once(cx):
    short = "ED5 Circulation Energetic Driver"
    secs = cs.sections_for(cx, "a49", "Blood", [short], [], marks=PURPLE)
    added = cs.auto_tick_purple(cx, "a49", "Blood", secs, [], "7", "2026-10-18")
    assert sum(1 for n in added if cs.infoceutical_code(n) == "ED5") == 1
    cx.rollback()
    secs = cs.sections_for(cx, "a49", "Blood", [short], [short], marks=PURPLE)
    added = cs.auto_tick_purple(cx, "a49", "Blood", secs, [short], "7", "2026-10-18")
    assert added == [EI10]
