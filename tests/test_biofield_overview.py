"""Glen's intervention overview chart (2026-10-18): ticked items against clinical
patterns, grouped Remedies, Healing Tools, Foods, Ingredients; a cell is the overall
rating; a yellow minus flags a caution and never changes the total."""
import json
import re
import sqlite3

import pytest

from dashboard import biofield_clinical_sections as cs
from dashboard import biofield_overview as ov
from dashboard.biofield_clinical_proposals import save_selection


@pytest.fixture
def cx(tmp_path):
    con = sqlite3.connect(str(tmp_path / "chat_log.db"))
    yield con
    con.close()


def _item(name, overall=7, **extra):
    return dict({"name": name, "overall": overall, "research": 5, "uniqueness": 5,
                 "reason": "r", "source": "s", "botanical": False, "lowers_bp": None},
                **extra)


def _tick(cx, label, section, name, overall=7, **extra):
    cs.upsert_item(cx, label, section, _item(name, overall, **extra), origin_test="a49")
    cs.save_pick(cx, "a49", label, section, name, checked=True)


def _rows(data):
    return {r["name"]: r for r in data["rows"]}


def test_rows_are_ticked_items_grouped_and_cells_are_ratings_per_pattern(cx):
    _tick(cx, "Gut", cs.NUTRIENTS, "Zinc", 7)
    _tick(cx, "Hypothyroid", cs.NUTRIENTS, "Zinc", 9)
    _tick(cx, "Gut", cs.BOTANICALS, "Turmeric", 6)
    _tick(cx, "Gut", cs.HEALING_TOOLS, "Kloud PEMF Mat", 5)
    _tick(cx, "Gut", "foods_avoid", "Gluten", 8)
    cs.upsert_item(cx, "Gut", cs.NUTRIENTS, _item("Selenium", 9), origin_test="a49")
    cs.upsert_item(cx, "Gut", cs.FORMULATIONS, _item("GI Repair", 8), origin_test="a49")
    save_selection(cx, "a49", "Gut", ["GI Repair"])
    rows = _rows(ov.overview(cx, "a49"))
    assert set(rows) == {"Zinc", "Turmeric", "Kloud PEMF Mat", "Gluten", "GI Repair"}
    assert rows["Zinc"]["cells"] == {"gut": 7, "hypothyroid": 9}
    assert {n: r["group"] for n, r in rows.items()} == {
        "Zinc": "Ingredients", "Turmeric": "Ingredients", "Kloud PEMF Mat": "Healing Tools",
        "Gluten": "Foods", "GI Repair": "Remedies"}
    assert ov.GROUPS == ("Remedies", "Healing Tools", "Foods", "Ingredients")


def test_merged_items_share_a_row_with_the_higher_rating(cx, monkeypatch):
    _tick(cx, "Gut", cs.NUTRIENTS, "Magnesium glycinate", 6)
    _tick(cx, "Gut", cs.NUTRIENTS, "Magnesium citrate", 8)
    from dashboard import biofield_plan_report as pr
    real = pr.stored_wording
    monkeypatch.setattr(pr, "stored_wording", lambda c: (
        {("nutrients_botanicals", "magnesium glycinate"): "Magnesium",
         ("nutrients_botanicals", "magnesium citrate"): "Magnesium"}, *real(c)[1:]))
    rows = _rows(ov.overview(cx, "a49"))
    assert list(rows) == ["Magnesium"] and rows["Magnesium"]["cells"] == {"gut": 8}


def test_biofield_columns_follow_the_layers_and_all_adds_the_rest(cx):
    _tick(cx, "Gut", cs.NUTRIENTS, "Zinc")
    _tick(cx, "Vertigo", cs.NUTRIENTS, "B12")
    _tick(cx, "Blood sugar", cs.NUTRIENTS, "Chromium")
    layers = [{"head": "Gut"}, {"head": "Gut"}, {"head": "Cataract and vision"}, {"head": ""}]
    d = ov.overview(cx, "a49", layers)
    assert [c["label"] for c in d["ba"]] == ["Gut", "Cataract and vision"]
    assert [c["label"] for c in d["all"]] == ["Gut", "Cataract and vision", "Blood sugar",
                                              "Vertigo"]


def test_a_caution_and_a_blood_pressure_item_carry_a_minus(cx, tmp_path):
    from dashboard import biofield_plan_report as pr
    folder = tmp_path / "plan-report"
    folder.mkdir()
    (folder / "merge-map.json").write_text(json.dumps({"merges": []}))
    (folder / "score-lines.json").write_text(json.dumps({"lines": {}}))
    (folder / "frame-lines.json").write_text(json.dumps({"lines": {}, "ingredient_cautions": {
        "Berberine": {"text": "t", "approved": True,
                      "items": [{"section": cs.BOTANICALS, "name": "Berberine HCl"}]}}}))
    pr.load_content(cx, str(folder))
    _tick(cx, "Gut", cs.BOTANICALS, "Berberine HCl")
    _tick(cx, "Gut", "foods_emphasize", "Beets", lowers_bp=True)
    _tick(cx, "Gut", cs.NUTRIENTS, "Zinc")
    rows = _rows(ov.overview(cx, "a49"))
    assert rows["Berberine HCl"]["cautions"] == ["Caution: Berberine"]
    assert rows["Beets"]["cautions"] == ["Lowers blood pressure"]
    assert rows["Zinc"]["cautions"] == []


def test_a_food_one_pattern_avoids_gets_a_minus_in_that_column_only(cx):
    _tick(cx, "Cataract", "foods_emphasize", "Sweet pepper", food_keys=["pepper"])
    _tick(cx, "Autoimmunity", "foods_avoid", "Nightshades", food_keys=["pepper", "tomato"])
    _tick(cx, "Gut", "foods_emphasize", "Carrots")
    rows = _rows(ov.overview(cx, "a49"))
    assert rows["Sweet pepper"]["conflicts"] == {"autoimmunity": "Autoimmunity avoids Nightshades"}
    assert rows["Carrots"]["conflicts"] == {} and rows["Nightshades"]["conflicts"] == {}


def test_a_pattern_never_conflicts_with_itself(cx):
    _tick(cx, "Gut", "foods_emphasize", "Salt", food_keys=["salt"])
    _tick(cx, "Gut", "foods_avoid", "Salty snacks", food_keys=["salt"])
    assert _rows(ov.overview(cx, "a49"))["Salt"]["conflicts"] == {}


def _run(data, clicks=0):
    """Run the page's script in node against a stub page; returns (table, button, off)."""
    import shutil
    import subprocess
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    stub = ("const el={};function g(id){return el[id]||(el[id]={innerHTML:'',textContent:'',"
            "hidden:false,onclick:null})}const document={getElementById:g};")
    js = (stub + "const D=" + json.dumps(data) + ";" + ov.SCRIPT
          + "for(let i=0;i<" + str(clicks) + ";i++)g('b').onclick();"
          + "console.log(JSON.stringify([g('t').innerHTML,g('b').textContent,g('off').textContent]))")
    out = subprocess.run([node, "-e", js], capture_output=True, text=True, timeout=20)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def _data(rows, ba=("gut",), extra=("liver",)):
    col = lambda k: {"key": k, "label": k.capitalize()}
    return {"ba": [col(k) for k in ba], "all": [col(k) for k in (*ba, *extra)],
            "groups": list(ov.GROUPS), "rows": rows}


def _row(name, group, cells, cautions=(), conflicts=None, kind=""):
    return {"name": name, "group": group, "kind": kind, "cells": cells,
            "cautions": list(cautions), "conflicts": conflicts or {}}


def _names(table):
    return re.findall(r"<td class=n>([^<]+)", table)


def test_rows_rank_by_total_then_top_rating_then_name_within_their_group():
    rows = [_row("Low", "Remedies", {"gut": 3, "liver": 9}),
            _row("High", "Remedies", {"gut": 8}),
            _row("Tie b", "Remedies", {"gut": 5, "liver": 1}),
            _row("Tie a", "Remedies", {"gut": 5, "liver": 1}),
            _row("Kloud", "Healing Tools", {"gut": 9}),
            _row("Zinc", "Ingredients", {"gut": 10})]
    table, button, _ = _run(_data(rows))
    assert _names(table) == ["High", "Tie a", "Tie b", "Low", "Kloud", "Zinc"]
    assert table.index("Remedies (4)") < table.index("Healing Tools (1)") \
        < table.index("Ingredients (1)")
    assert "Foods" not in table and ">X<" in table
    assert button.startswith("Showing the Biofield Analysis patterns (1)")
    table, button, _ = _run(_data(rows), clicks=1)
    assert _names(table) == ["Low", "High", "Tie a", "Tie b", "Kloud", "Zinc"]
    assert button.startswith("Showing all ticked patterns (2)")


def test_a_minus_flags_and_never_changes_the_total_or_the_count():
    rows = [_row("A", "Foods", {"gut": 6}, cautions=["Lowers blood pressure"],
                 conflicts={"gut": "Gut avoids X", "liver": "Liver avoids Y"}),
            _row("B", "Foods", {"gut": 6})]
    table, _, _ = _run(_data(rows), clicks=1)
    a = table[table.index(">A<"):table.index(">B<")]
    assert a.count("class=m") == 3
    assert re.findall(r"<td class=t>(\d+)</td><td>(\d+)</td>", a) == [("6", "1")]
    assert 'title="Lowers blood pressure"' in a and 'title="Liver avoids Y"' in a


def test_a_row_with_nothing_in_the_shown_patterns_is_counted_not_hidden_silently():
    rows = [_row("Gut item", "Remedies", {"gut": 6}), _row("Liver item", "Remedies", {"liver": 7})]
    table, _, off = _run(_data(rows))
    assert _names(table) == ["Gut item"]
    assert off == "1 ticked item has no rating under the patterns shown, so it is left off this view."
    assert _run(_data(rows), clicks=1)[2] == ""


def test_with_no_biofield_patterns_the_chart_shows_all_and_hides_the_button():
    table, button, _ = _run(_data([_row("A", "Remedies", {"liver": 4})], ba=()))
    assert _names(table) == ["A"]


def test_every_string_is_escaped_by_the_script():
    bad = "<img src=x onerror=1>"
    rows = [_row(bad, "Remedies", {"gut": 6}, cautions=[bad], conflicts={"gut": bad}, kind=bad)]
    data = _data(rows)
    data["ba"][0]["label"] = bad
    table, _, _ = _run(data)
    assert "<img" not in table and table.count("&lt;img") >= 4


def test_the_page_cannot_close_its_own_script(cx):
    _tick(cx, "Gut</script><b>", cs.NUTRIENTS, "Zinc</script><img src=x>")
    page = ov.render_page(ov.overview(cx, "a49"), "Pat <b>", "")
    assert page[page.index("<script>"):].count("</script>") == 1
    assert "<b>" not in page[:page.index("<script>")]


def test_the_route_serves_the_chart_and_the_page_links_it(tmp_path, monkeypatch):
    monkeypatch.delenv("CONSOLE_SECRET", raising=False)
    import dashboard
    monkeypatch.setattr(dashboard, "CONSOLE_SECRET", "", raising=False)
    import biofield_local_app as bla
    from dashboard.biofield_authoring import create_test, init_auth_tables
    db = str(tmp_path / "chat_log.db")
    with sqlite3.connect(db) as con:
        init_auth_tables(con)
        tid = create_test(con, "Pat", "pat@example.com", "2026-10-18")
        _tick(con, "Gut", cs.NUTRIENTS, "Zinc", 7)
        con.execute("UPDATE biofield_clinical_section_pick SET test_id=?", (tid,))
        con.execute("UPDATE biofield_clinical_section_item SET origin_test=?", (tid,))
        con.commit()
    published = []
    client = bla.create_app(db, fetch_profile=lambda e: {"conditions": ["Gut"]},
                            scan_lookup=lambda e: {"found": False},
                            auto_publish=lambda *a, **k: published.append(a)).test_client()
    page = client.get(f"/test/{tid}/overview").get_data(as_text=True)
    data = json.loads(re.search(r"const D=(\{.*?\});", page).group(1).replace("<\\/", "</"))
    assert [r["name"] for r in data["rows"]] == ["Zinc"]
    assert "Not printed." in page and published == []
    assert f"/test/{tid}/overview" in client.get(f"/author/{tid}").get_data(as_text=True)


def test_on_equal_totals_the_higher_single_rating_ranks_first():
    rows = [_row("Alpha", "Remedies", {"gut": 4, "liver": 4}), _row("Zed", "Remedies", {"gut": 8})]
    assert _names(_run(_data(rows), clicks=1)[0]) == ["Zed", "Alpha"]


@pytest.mark.parametrize("avoid_first", [True, False])
def test_a_conflict_names_the_avoiding_pattern_by_its_label(cx, avoid_first):
    ticks = [("Cataract and Vision", "foods_emphasize", "Sweet pepper"),
             ("Connective Tissue Autoimmunity", "foods_avoid", "Nightshades")]
    for label, section, name in (ticks[::-1] if avoid_first else ticks):
        _tick(cx, label, section, name, food_keys=["pepper"])
    assert _rows(ov.overview(cx, "a49"))["Sweet pepper"]["conflicts"] == {
        "connective tissue autoimmunity": "Connective tissue autoimmunity avoids Nightshades"}
