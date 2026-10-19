"""The Essential Oils section (Glen, 2026-10-18): printed under Remedies after
Botanicals, grouped with Ingredients on the overview chart. Each oil carries its
route, "inhale and skin" or "inhale only", which Glen sees on the author page."""
import json
import sqlite3

import pytest

from dashboard import biofield_clinical_sections as cs
from dashboard import biofield_overview as ov
from dashboard import biofield_plan_report as pr


@pytest.fixture
def cx(tmp_path):
    con = sqlite3.connect(str(tmp_path / "chat_log.db"))
    yield con
    con.close()


def _oil(name="Lavender", overall=7, **extra):
    return dict({"name": name, "overall": overall, "research": 5, "uniqueness": 5,
                 "reason": "r", "source": "s", "botanical": True, "lowers_bp": None,
                 "dose": "2 drops, breathed in", "route": "inhale only",
                 "route_reason": "Can irritate skin <in some>",
                 "explanation": "Lavender calms.", "explanation_approved": True}, **extra)


def _section(cx, label="Vertigo", key=cs.ESSENTIAL_OILS):
    return next(s for s in cs.sections_for(cx, "a49", label) if s["key"] == key)


def test_the_section_follows_botanicals_and_carries_a_dose():
    keys = [key for key, _ in cs.SECTIONS]
    assert keys.index(cs.ESSENTIAL_OILS) == keys.index(cs.BOTANICALS) + 1
    assert dict(cs.SECTIONS)[cs.ESSENTIAL_OILS] == "Essential Oils"
    assert cs.ESSENTIAL_OILS in cs.DOSED_SECTIONS


def test_an_oil_keeps_its_route_and_reason_and_an_update_changes_them(cx):
    cs.upsert_item(cx, "Vertigo", cs.ESSENTIAL_OILS, _oil(), origin_test="a49")
    item = _section(cx)["items"][0]
    assert (item["name"], item["dose"]) == ("Lavender", "2 drops, breathed in")
    assert (item["route"], item["route_reason"]) == ("inhale only", "Can irritate skin <in some>")
    cs.upsert_item(cx, "Vertigo", cs.ESSENTIAL_OILS,
                   _oil(route="inhale and skin", route_reason=""), origin_test="a49")
    item = _section(cx)["items"][0]
    assert (item["route"], item["route_reason"]) == ("inhale and skin", "")


def _files(tmp_path, sections):
    folder = tmp_path / "content"
    folder.mkdir(exist_ok=True)
    (folder / "vertigo.json").write_text(json.dumps(
        {"condition_key": "vertigo", "label": "Vertigo", "sections": sections}))
    return str(folder)


def test_the_loader_loads_essential_oils(tmp_path, cx):
    from scripts.load_clinical_sections import load
    report = load(cx, _files(tmp_path, {"essential_oils": [_oil()]}), client_test="a49")
    assert report["added"] == {"essential_oils": 1}
    assert report["unknown_sections"] == []
    assert _section(cx)["items"][0]["route"] == "inhale only"


def test_an_oil_moved_out_of_botanicals_leaves_one_row(tmp_path, cx):
    from scripts.load_clinical_sections import load
    load(cx, _files(tmp_path, {"botanicals": [_oil(route="", route_reason="")]}), "a49")
    report = load(cx, _files(tmp_path, {"essential_oils": [_oil()]}), "a49")
    assert [i["name"] for i in _section(cx, key=cs.BOTANICALS)["items"]] == []
    assert [i["name"] for i in _section(cx)["items"]] == ["Lavender"]
    assert report["removed"]


def test_an_unknown_section_is_reported_and_the_cli_refuses_to_commit(tmp_path, monkeypatch):
    import scripts.load_clinical_sections as loader
    db = tmp_path / "chat_log.db"
    folder = _files(tmp_path, {"aromatherapy": [_oil()], "fullscript_wanted": []})
    con = sqlite3.connect(str(db))
    assert loader.load(con, folder, "a49")["unknown_sections"] == ["Vertigo: aromatherapy"]
    con.rollback()
    con.close()
    monkeypatch.setattr("sys.argv", ["load", folder, "--db", str(db),
                                     "--client-test", "a49", "--commit"])
    with pytest.raises(SystemExit) as stop:
        loader.main()
    assert "aromatherapy" in str(stop.value)


def test_a_ticked_oil_prints_under_remedies_as_an_essential_oil(cx):
    cs.upsert_item(cx, "Vertigo", cs.ESSENTIAL_OILS, _oil(), origin_test="a49")
    cs.save_pick(cx, "a49", "Vertigo", cs.ESSENTIAL_OILS, "Lavender", checked=True)
    page = pr.build(cx, "a49", "Pat", "")[0]
    remedies = page[page.index(">Remedies<"):]
    assert "Lavender" in remedies and "Essential oil" in remedies
    assert "Lavender calms." in remedies and "2 drops, breathed in" in remedies
    # The route is for Glen; the client reads it in the dose.
    assert "inhale only" not in page and "irritate" not in page


def test_an_oil_is_an_ingredient_on_the_overview(cx):
    cs.upsert_item(cx, "Vertigo", cs.ESSENTIAL_OILS, _oil(), origin_test="a49")
    cs.save_pick(cx, "a49", "Vertigo", cs.ESSENTIAL_OILS, "Lavender", checked=True)
    row = next(r for r in ov.overview(cx, "a49")["rows"] if r["name"] == "Lavender")
    assert (row["group"], row["kind"]) == ("Ingredients", "Essential oil")


def test_the_author_page_shows_the_route_with_its_reason_escaped(cx):
    from dashboard.biofield_report_html import _clinical_sections_html
    cs.upsert_item(cx, "Vertigo", cs.ESSENTIAL_OILS, _oil(), origin_test="a49")
    page = _clinical_sections_html(cs.sections_for(cx, "a49", "Vertigo"), lambda n: "")
    assert ">inhale only</span>" in page
    assert 'title="Can irritate skin &lt;in some&gt;"' in page
    assert "placeholder='Dose'" in page
