"""A row moved under other rows as a detail, for one client only (Glen, 2026-10-19).

"Dry eye, both eyes - Current folds into Sjogren's syndrome for this client as detail."
He then approved the plan: a row with ticks is refused, a row's own details go with it,
and taking it back out returns the row as it was. A detail may sit under two topics.
"""
import json
import shutil
import sqlite3
import subprocess

import pytest

from biofield_local_app import create_app
from dashboard import biofield_clinical_groups as cg
from dashboard.biofield_clinical_proposals import decide, decisions, save_selection
from dashboard.biofield_stress import add_stress, init_stress_tables

T = "a7"


@pytest.fixture()
def cx():
    c = sqlite3.connect(":memory:")
    init_stress_tables(c)
    cg.ensure_schema(c)
    return c


def _topic(cx, name, *factors):
    for f in factors:
        add_stress(cx, T, f, source="comm")
    assert cg.approve(cx, T, cg.factors_for(cx, T), name, list(factors))


def _factors(cx):
    return cg.item_factors(cx, T)


def test_a_row_goes_under_a_topic_and_leaves_the_list(cx):
    _topic(cx, "Connective tissue autoimmunity", "sjogrens")
    assert cg.place_row(cx, T, "Dry eye, both eyes - Current",
                        ["Connective tissue autoimmunity"]) == ["Connective tissue autoimmunity"]
    assert _factors(cx)["connective tissue autoimmunity"] == [
        "sjogrens", "Dry eye, both eyes - Current"]
    assert decisions(cx, T)["dry eye both eyes current"]["status"] == "dismissed"


def test_a_row_may_sit_under_two_topics(cx):
    _topic(cx, "Sugar Regulation", "borderline diabetes")
    _topic(cx, "Blood and circulation", "anemia")
    cg.place_row(cx, T, "Caffeine, coffee and green tea",
                 ["Sugar Regulation", "Blood and circulation"])
    f = _factors(cx)
    assert "Caffeine, coffee and green tea" in f["sugar regulation"]
    assert "Caffeine, coffee and green tea" in f["blood and circulation"]


def test_a_new_topic_becomes_an_item(cx):
    cg.place_row(cx, T, "Epinephrine - Allergy listed on my eye chart", ["Adrenals"])
    assert decisions(cx, T)["adrenals"]["status"] == "accepted"
    assert _factors(cx)["adrenals"] == ["Epinephrine - Allergy listed on my eye chart"]


def test_a_rows_own_details_go_with_it(cx):
    _topic(cx, "Hypothyroid", "synthroid")
    cg.place_row(cx, T, "Hypothyroid", ["Hashimoto's thyroid disease"])
    f = _factors(cx)
    assert f["hashimoto s thyroid disease"] == ["Hypothyroid", "synthroid"]
    assert "hypothyroid" not in f


def test_a_row_with_ticks_is_refused_and_nothing_changes(cx):
    _topic(cx, "Hypothyroid", "synthroid")
    save_selection(cx, T, "Hypothyroid", ["Thyroid Support"])
    before = (_factors(cx), decisions(cx, T))
    with pytest.raises(cg.Refused) as exc:
        cg.place_row(cx, T, "Hypothyroid", ["Hashimoto's thyroid disease"])
    assert "remedy: Thyroid Support" in str(exc.value)
    assert (_factors(cx), decisions(cx, T)) == before


def test_a_section_tick_is_refused_too(cx):
    from dashboard import biofield_clinical_sections as cs
    cs.save_pick(cx, T, "Hypothyroid", "nutrients", "Selenium", checked=True)
    with pytest.raises(cg.Refused) as exc:
        cg.place_row(cx, T, "Hypothyroid", ["Hashimoto's thyroid disease"])
    assert "nutrients: selenium" in str(exc.value)


def test_a_row_cannot_go_under_itself_or_nowhere(cx):
    with pytest.raises(cg.Refused):
        cg.place_row(cx, T, "Vertigo", ["Vertigo", " vertigo "])
    with pytest.raises(cg.Refused):
        cg.place_row(cx, T, "Vertigo", [])


def test_taking_it_out_of_its_only_topic_returns_the_row_as_it_was(cx):
    _topic(cx, "Hypothyroid", "synthroid")
    decide(cx, T, "Hypothyroid", "accepted", "by hand")
    cg.place_row(cx, T, "Hypothyroid", ["Hashimoto's thyroid disease"])
    assert cg.remove_factor(cx, T, "Hashimoto's thyroid disease", "Hypothyroid")
    f = _factors(cx)
    assert f["hypothyroid"] == ["synthroid"] and f.get("hashimoto s thyroid disease") == []
    assert decisions(cx, T)["hypothyroid"] == {"label": "Hypothyroid", "status": "accepted",
                                               "evidence": "by hand"}


def test_a_profile_row_with_no_decision_returns_with_none(cx):
    cg.place_row(cx, T, "Vertigo attacks", ["Vertigo"])
    cg.remove_factor(cx, T, "Vertigo", "Vertigo attacks")
    assert "vertigo attacks" not in decisions(cx, T)


def test_it_stays_hidden_while_under_another_topic(cx):
    cg.place_row(cx, T, "Caffeine", ["Sugar Regulation", "Crystalline Lens"])
    cg.remove_factor(cx, T, "Sugar Regulation", "Caffeine")
    assert decisions(cx, T)["caffeine"]["status"] == "dismissed"
    cg.remove_factor(cx, T, "Crystalline Lens", "Caffeine")
    assert "caffeine" not in decisions(cx, T)


def test_another_client_is_untouched(cx):
    cg.place_row(cx, T, "Dry eyes", ["Sjogren's syndrome"])
    assert decisions(cx, "a8") == {} and cg.item_factors(cx, "a8") == {}
    # No alias is written: that would fold the row for every client.
    assert cx.execute("SELECT COUNT(*) FROM biofield_clinical_alias").fetchone() == (0,)


# --- the route and the page ------------------------------------------------------------

@pytest.fixture(autouse=True)
def _no_gate(monkeypatch):
    monkeypatch.delenv("CONSOLE_SECRET", raising=False)
    import dashboard as _d
    monkeypatch.setattr(_d, "CONSOLE_SECRET", "", raising=False)


def _app(tmp_path, conditions):
    db = str(tmp_path / "chat.db")
    e4l = tmp_path / "e4l.db"
    e = sqlite3.connect(str(e4l))
    e.execute("CREATE TABLE e4l_clients(client_id INTEGER, name TEXT, email TEXT)")
    e.commit()
    e.close()
    app = create_app(db, scan_lookup=lambda e: {"found": False},
                     fetch_profile=lambda e: {"conditions": list(conditions)},
                     fetch_recent_comms=lambda e: {}, e4l_db=str(e4l),
                     client_gmail=lambda cx, e: {"text": "", "held": 0, "error": None},
                     records_reader=None)
    c = app.test_client()
    tid = c.post("/author/new").headers["Location"].rstrip("/").split("/")[-1]
    c.post(f"/author/{tid}/header", json={"name": "Pat Testerson",
                                          "email": "pat.test@example.com",
                                          "date": "2026-10-19"})
    return c, tid, db


def _rows(page):
    import re
    return re.findall(r"class=\"clinical-item[^\"]*\" data-label=\"([^\"]+)\"", page) or \
        re.findall(r"data-label=\"([^\"]+)\"", page)


def test_the_route_moves_the_row_and_the_page_shows_it_as_a_detail(tmp_path):
    c, tid, db = _app(tmp_path, ["Sjogren's syndrome", "Dry eye, both eyes - Current"])
    page = c.get(f"/author/{tid}").get_data(as_text=True)
    assert 'data-row="Dry eye, both eyes - Current"' in page
    assert "Make this a detail under another row&hellip; (this client only)" in page
    j = c.post(f"/author/{tid}/clinical-groups/place-row",
               json={"row": "Dry eye, both eyes - Current",
                     "topics": ["Sjogren's syndrome"]}).get_json()
    assert j == {"ok": True, "topics": ["Sjogren's syndrome"]}
    page = c.get(f"/author/{tid}").get_data(as_text=True)
    assert 'data-row="Dry eye, both eyes - Current"' not in page
    row = page[page.index("data-label=\"Sjogren&#x27;s syndrome\""):]
    assert "<span class=clinical-factor>Dry eye, both eyes - Current" in row[:4000]


def test_the_route_refuses_bad_input_and_ticks(tmp_path):
    c, tid, db = _app(tmp_path, ["Hypothyroid", "Hashimoto's"])
    url = f"/author/{tid}/clinical-groups/place-row"
    assert c.post(url, json={"row": "Hypothyroid", "topics": "Hashimoto's"}).status_code == 400
    assert c.post(url, json={"row": "Hypothyroid", "topics": [3]}).status_code == 400
    with sqlite3.connect(db) as x:
        save_selection(x, tid, "Hypothyroid", ["Thyroid Support"])
    r = c.post(url, json={"row": "Hypothyroid", "topics": ["Hashimoto's"]})
    assert r.status_code == 409 and "Thyroid Support" in r.get_json()["error"]


def _handler():
    from dashboard.biofield_report_html import _GROUPS_HTML
    js = _GROUPS_HTML.split("<script>")[1].split("</script>")[0]
    start = js.index("window.placeClinicalRow=")
    return js[start:js.index("window.addGroupFactor=", start)]


def test_the_picker_sends_the_ticked_rows_and_a_typed_topic():
    if not shutil.which("node"):
        pytest.skip("needs node")
    harness = """
var sent=[],asked=[];var window={};var location={reload:function(){sent.push('reload')}};
function alert(t){asked.push(t)} function confirm(t){asked.push(t);return true}
async function send(p,b){sent.push([p,b]);return {ok:true}}
function box(){return {getAttribute:function(){return 'Dry eye'},
 querySelectorAll:function(){return [{value:'Sugar Regulation'},{value:'Crystalline Lens'}]},
 querySelector:function(){return {value:' Adrenals '}}}}
var b=box();var btn={closest:function(){return b}};
""" + _handler() + """
window.placeClinicalRow(btn).then(function(){console.log(JSON.stringify({sent:sent,asked:asked}))});
"""
    out = subprocess.run(["node", "-e", harness], capture_output=True, text=True, timeout=20)
    assert out.returncode == 0, out.stderr
    got = json.loads(out.stdout)
    assert got["sent"][0] == ["/place-row", {"row": "Dry eye", "topics": [
        "Sugar Regulation", "Crystalline Lens", "Adrenals"]}]
    assert "for this client only" in got["asked"][0] and got["sent"][1] == "reload"
