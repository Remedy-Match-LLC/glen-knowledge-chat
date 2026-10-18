"""The Foods, Remedies and Healing Tools Plan (Glen, 2026-10-18).

Ticked items only; identical names merge across conditions; similar names merge only
through clinical's approved map; lines group under "Score N" headings, highest first;
each condition's approved explanation sits under the line; nothing unapproved prints.
"""
import json
import sqlite3

import pytest

from dashboard import biofield_clinical_sections as cs
from dashboard import biofield_plan_report as pr


@pytest.fixture
def cx(tmp_path):
    con = sqlite3.connect(str(tmp_path / "chat_log.db"))
    yield con
    con.close()


def _item(name, overall, explanation="", approved=False, **extra):
    return dict({"name": name, "overall": overall, "research": 5, "uniqueness": 5,
                 "reason": "INTERNAL REASON", "source": "src", "botanical": False,
                 "lowers_bp": None, "explanation": explanation,
                 "explanation_approved": approved}, **extra)


def _tick(cx, label, section, item):
    cs.upsert_item(cx, label, section, item, origin_test="a49")
    cs.save_pick(cx, "a49", label, section, item["name"], checked=True)


def test_only_ticked_items_and_identical_names_merge_across_conditions(cx):
    _tick(cx, "Gut", cs.NUTRIENTS, _item("Zinc glycinate", 7, "Zinc for the gut.", True,
                                         dose="15 mg"))
    _tick(cx, "Hypothyroid", cs.NUTRIENTS, _item("Zinc glycinate", 8, "Zinc for thyroid.",
                                                 True, dose="15 mg"))
    cs.upsert_item(cx, "Gut", cs.NUTRIENTS, _item("Selenium", 9), origin_test="a49")
    lines = pr.plan(cx, "a49")[cs.NUTRIENTS]
    assert [line["name"] for line in lines] == ["Zinc glycinate"]
    assert lines[0]["overall"] == 8
    assert [(en["condition"], en["overall"]) for en in lines[0]["entries"]] == [
        ("Hypothyroid", 8), ("Gut", 7)]


def test_explanations_sit_under_their_line_and_unapproved_ones_never_print(cx):
    _tick(cx, "Gut", "foods_emphasize", _item("Bone broth", 8, "Broth soothes.", True))
    _tick(cx, "Gut", "foods_avoid", _item("Gluten", 6, "Draft text.", False))
    page = pr.render_plan_html(pr.plan(cx, "a49"), "Josie", "18 October 2026")
    assert "<p class=plan-why>Broth soothes.</p>" in page
    assert "Draft text." not in page and "INTERNAL REASON" not in page
    assert page.index("Bone broth") < page.index("Broth soothes.") < page.index("Gluten")
    assert ("Gut", "foods_avoid") == (pr.missing(pr.plan(cx, "a49"))[0][2],
                                      pr.missing(pr.plan(cx, "a49"))[0][0])


def test_lines_group_under_score_headings_highest_first(cx):
    cs.upsert_item(cx, "Gut", cs.FORMULATIONS, _item("GI Repair", 9), origin_test="a49")
    _tick(cx, "Gut", cs.NUTRIENTS, _item("L-glutamine", 10))
    _tick(cx, "Gut", cs.NUTRIENTS, _item("Zinc", 6))
    from dashboard.biofield_clinical_proposals import save_selection
    save_selection(cx, "a49", "Gut", ["GI Repair"])
    page = pr.render_plan_html(pr.plan(cx, "a49"))
    remedies = page[page.index("<h2>Remedies</h2>"):]
    assert remedies.index("Score X") < remedies.index("Score 9") < remedies.index("Score 6")
    assert remedies.index("L-glutamine") < remedies.index("GI Repair") < remedies.index("Zinc")
    assert "plan-kind>Functional Formulation<" in remedies


def _wording(tmp_path, approved):
    folder = tmp_path / "plan-report"
    folder.mkdir()
    (folder / "merge-map.json").write_text(json.dumps({"merges": [
        {"group": "foods_emphasize", "display_name": "Bone broth and soups",
         "members": ["Bone broth", "Bone broth and vegetable soups"], "approved": approved}]}))
    (folder / "score-lines.json").write_text(json.dumps({
        "lines": {"8": {"text": "These scored high.", "approved": approved}},
        "intro": {"lines": ["Your plan, by score."], "approved": approved}}))
    return str(folder)


@pytest.mark.parametrize("approved", [True, False])
def test_similar_names_merge_and_score_lines_print_only_once_approved(cx, tmp_path, approved):
    _tick(cx, "Gut", "foods_emphasize", _item("Bone broth", 8, "Broth A.", True))
    _tick(cx, "Liver", "foods_emphasize", _item("Bone broth and vegetable soups", 7,
                                                "Broth B.", True))
    report = pr.load_content(cx, _wording(tmp_path, approved))
    assert report["merges_applied"] == (1 if approved else 0)
    _, meanings, intro = pr.stored_wording(cx)
    lines = pr.plan(cx, "a49")["foods_emphasize"]
    page = pr.render_plan_html(pr.plan(cx, "a49"), score_meanings=meanings, intro=intro)
    if approved:
        assert [line["name"] for line in lines] == ["Bone broth and soups"]
        assert "These scored high." in page and "Your plan, by score." in page
        assert "Broth A." in page and "Broth B." in page
    else:
        assert len(lines) == 2
        assert "These scored high." not in page and "Your plan, by score." not in page


def test_a_withdrawn_approval_is_withdrawn_on_reload(cx, tmp_path):
    pr.load_content(cx, _wording(tmp_path, True))
    folder = tmp_path / "plan-report"
    (folder / "merge-map.json").write_text(json.dumps({"merges": []}))
    (folder / "score-lines.json").write_text(json.dumps({"lines": {}}))
    pr.load_content(cx, str(folder))
    assert pr.stored_wording(cx) == ({}, {}, [])


def test_the_loader_stores_explanation_and_its_approval(cx):
    cs.upsert_item(cx, "Gut", cs.NUTRIENTS, _item("Zinc", 6, "Text.", True))
    cs.upsert_item(cx, "Gut", cs.NUTRIENTS, _item("Iron", 6, "Text.", "yes"))
    rows = dict(cx.execute("SELECT name, explanation_approved FROM "
                           "biofield_clinical_section_item").fetchall())
    # Only a real true approves: a stray string never prints client wording.
    assert rows == {"Zinc": 1, "Iron": 0}


def test_the_plan_follows_the_report_on_a_new_page_of_the_same_document():
    joined = pr.append_to_report("<html><body><h1>Report</h1></body></html>",
                                 "<main class=plan>PLAN</main>")
    assert joined.index("Report") < joined.index("break-before:page") < joined.index("PLAN")
    assert joined.endswith("</body></html>")


def test_plan_styles_never_reach_the_report_they_join(cx):
    section, _ = pr.build(cx, "a49")
    css = section[section.index("<style>") + 7:section.index("</style>")]
    for rule in css.replace("@media print{", "").split("}"):
        selector = rule.split("{")[0].strip()
        if selector:
            assert all(part.strip().startswith(".plan") for part in selector.split(",")), selector


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.delenv("CONSOLE_SECRET", raising=False)
    import dashboard as _d
    monkeypatch.setattr(_d, "CONSOLE_SECRET", "", raising=False)
    import biofield_local_app as bla
    written, published = [], []

    def fake_pdf(html, out):
        written.append((html, out))
        with open(out, "wb") as f:
            f.write(b"%PDF fake")
        return out
    monkeypatch.setattr(bla, "save_report_pdf", fake_pdf)
    monkeypatch.setenv("BIOFIELD_REPORTS_DIR", str(tmp_path))
    db = str(tmp_path / "chat_log.db")
    # Publishing is recorded, never run: the real one can email a client.
    c = bla.create_app(db, auto_publish=lambda *a, **k: published.append(a) or {}).test_client()
    c.db, c.written, c.published = db, written, published
    return c


def test_the_plan_page_and_its_note_for_glen(client):
    tid = client.post("/author/new").headers["Location"].rstrip("/").rsplit("/", 1)[-1]
    with sqlite3.connect(client.db) as con:
        cs.upsert_item(con, "Gut", "foods_emphasize", _item("Bone broth", 8, "Broth.", True),
                       origin_test=tid)
        cs.save_pick(con, tid, "Gut", "foods_emphasize", "Bone broth", checked=True)
        cs.upsert_item(con, "Gut", "foods_avoid", _item("Gluten", 6), origin_test=tid)
        cs.save_pick(con, tid, "Gut", "foods_avoid", "Gluten", checked=True)
    page = client.get(f"/test/{tid}/plan").get_data(as_text=True)
    assert "Foods, Remedies and Healing Tools Plan" in page and "Broth." in page
    assert "1 ticked entry has no approved explanation" in page and "Gluten (Gut)" in page
    assert ".plan-glen{display:none}" in page   # the note never prints


def test_an_extra_explanation_covers_a_shared_list_remedy_once_approved(cx):
    """Clinical's `extra_explanations`: a ticked remedy with no item in the file."""
    from dashboard.biofield_clinical_proposals import save_selection
    save_selection(cx, "a49", "Blood sugar", ["Soothe Powder"])
    extras = {"Soothe Powder": {"section": cs.FORMULATIONS, "explanation": "Soothes.",
                                "explanation_approved": False}}
    assert cs.set_extra_explanations(cx, "Blood sugar", extras) == (0, 1)
    line = pr.plan(cx, "a49")[cs.FORMULATIONS][0]
    assert line["entries"][0]["explanation"] == ""
    extras["Soothe Powder"]["explanation_approved"] = True
    assert cs.set_extra_explanations(cx, "Blood sugar", extras) == (1, 0)
    line = pr.plan(cx, "a49")[cs.FORMULATIONS][0]
    assert line["entries"][0]["explanation"] == "Soothes."
    # Another condition's extra never reaches this one.
    save_selection(cx, "a49", "Gut", ["Soothe Powder"])
    entries = {en["condition"]: en["explanation"]
               for en in pr.plan(cx, "a49")[cs.FORMULATIONS][0]["entries"]}
    assert entries["Gut"] == ""
    # A withdrawn approval is withdrawn on reload.
    extras["Soothe Powder"]["explanation_approved"] = "yes"
    assert cs.set_extra_explanations(cx, "Blood sugar", extras) == (0, 1)
    assert pr.plan(cx, "a49")[cs.FORMULATIONS][0]["entries"][0]["explanation"] == ""


def test_the_condition_loader_reads_extra_explanations(cx, tmp_path):
    import importlib.util, os
    spec = importlib.util.spec_from_file_location(
        "lcs", os.path.join(os.path.dirname(__file__), "..", "scripts",
                            "load_clinical_sections.py"))
    lcs = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(lcs)
    folder = tmp_path / "sections"
    folder.mkdir()
    (folder / "gut.json").write_text(json.dumps({
        "condition_key": "gut", "label": "Gut", "sections": {},
        "extra_explanations": {
            "WholOmega 120 gelcaps": {"section": cs.FORMULATIONS, "explanation": "DHA.",
                                      "explanation_approved": True},
            "Soothe Powder": {"section": cs.FORMULATIONS, "explanation": "Draft.",
                              "explanation_approved": False}}}))
    report = lcs.load(cx, str(folder), "a49")
    assert (report["extra_explanations_applied"], report["extra_explanations_waiting"]) == (1, 1)
    assert cx.execute("SELECT name FROM biofield_clinical_extra_explanation").fetchall() == [
        ("WholOmega 120 gelcaps",)]



def _new_test(client):
    return client.post("/author/new").headers["Location"].rstrip("/").rsplit("/", 1)[-1]


def test_the_pdf_routes_print_the_plan_without_glens_note(client, tmp_path):
    tid = _new_test(client)
    with sqlite3.connect(client.db) as con:
        cs.upsert_item(con, "Gut", "foods_avoid", _item("Gluten", 6), origin_test=tid)
        cs.save_pick(con, tid, "Gut", "foods_avoid", "Gluten", checked=True)
    r = client.get(f"/test/{tid}/plan.pdf")
    assert r.status_code == 200 and r.data == b"%PDF fake"
    html, out = client.written[-1]
    assert "Gluten" in html and "plan-glen" not in html.replace(".plan-glen{", "")
    assert out.endswith(".pdf") and "/plan_" in out
    assert client.published == []   # the Plan alone never publishes
    r = client.get(f"/test/{tid}/report.pdf?plan=1")
    assert r.status_code == 200
    html, out = client.written[-1]
    assert out.endswith("_with_plan.pdf") and "break-before:page" in html and "Gluten" in html
    assert "no approved explanation" not in html
    r = client.get(f"/test/{tid}/report.pdf")
    html, out = client.written[-1]
    assert not out.endswith("_with_plan.pdf") and "plan-appended" not in html


def test_a_dismissed_or_folded_row_does_not_print_and_glen_is_told(client):
    from dashboard.biofield_clinical_checklist import alias_condition
    from dashboard.biofield_clinical_proposals import decide, save_selection
    tid = _new_test(client)
    with sqlite3.connect(client.db) as con:
        con.row_factory = None
        save_selection(con, tid, "Vertigo", ["GI Repair"])
        save_selection(con, tid, "Low estrogen", ["Estro Balance"])
        save_selection(con, tid, "Gut", ["Fibrosolve"])
        decide(con, tid, "Vertigo", "dismissed")
        alias_condition(con, "Low estrogen", "Adrenal fatigue")
        con.commit()
        names = [line["name"] for line in pr.plan(con, tid).get(cs.FORMULATIONS, [])]
        assert names == ["Fibrosolve"]
        whys = {name: why for _, _, name, why in pr.lost_ticks(con, tid)}
    assert whys == {"GI Repair": "its row was dismissed",
                    "Estro Balance": "its row was folded into Adrenal fatigue"}
    page = client.get(f"/test/{tid}/plan").get_data(as_text=True)
    assert "2 ticked entries are left out of the Plan" in page


def test_a_lost_pick_is_named_in_the_note(cx):
    _tick(cx, "Gut", "foods_avoid", _item("Gluten", 6))
    cx.execute("DELETE FROM biofield_clinical_section_item")
    assert pr.plan(cx, "a49") == {}
    assert pr.lost_ticks(cx, "a49") == [("foods_avoid", "Gut", "gluten",
                                         "the item left the catalog")]


def test_the_rows_display_name_prints_not_its_key(cx):
    from dashboard.biofield_clinical_checklist import set_display_label
    _tick(cx, "adrenal fatigue current 2023", "foods_avoid", _item("Gluten", 6))
    set_display_label(cx, "adrenal fatigue current 2023", "Adrenal fatigue (2023)")
    entry = pr.plan(cx, "a49")["foods_avoid"][0]["entries"][0]
    assert entry["condition"] == "Adrenal fatigue (2023)"


def test_the_clients_own_score_and_dose_win_for_a_remedy(cx):
    from dashboard.biofield_clinical_proposals import save_selection
    cs.upsert_item(cx, "Gut", cs.FORMULATIONS, _item("GI Repair", 9, dose="1 scoop"))
    save_selection(cx, "a49", "Gut", ["GI Repair"])
    cs.save_pick(cx, "a49", "Gut", cs.FORMULATIONS, "GI Repair",
                 rating="574", dose="2 scoops twice daily")
    line = pr.plan(cx, "a49")[cs.FORMULATIONS][0]
    assert (line["overall"], line["entries"][0]["dose"]) == (5, "2 scoops twice daily")


def test_an_infoceutical_without_a_code_keeps_its_section_and_explanation(cx):
    from dashboard.biofield_clinical_proposals import save_selection
    cs.upsert_item(cx, "Gut", cs.INFOCEUTICALS, _item("Gut Calm", 7, "Calms.", True))
    save_selection(cx, "a49", "Gut", ["Gut Calm"])
    line = pr.plan(cx, "a49")[cs.INFOCEUTICALS][0]
    assert line["entries"][0]["explanation"] == "Calms."


def test_a_merged_group_never_absorbs_an_item_that_only_shares_its_name(cx):
    _tick(cx, "Gut", cs.NUTRIENTS, _item("Magnesium glycinate", 7))
    _tick(cx, "Gut", cs.NUTRIENTS, _item("Magnesium", 6))
    merge_map = {("nutrients_botanicals", "magnesium glycinate"): "Magnesium"}
    lines = pr.plan(cx, "a49", merge_map)[cs.NUTRIENTS]
    assert len(lines) == 2


def test_a_dose_heads_the_line_only_when_every_condition_gives_it(cx):
    _tick(cx, "Gut", cs.NUTRIENTS, _item("Zinc", 7, dose="15 mg"))
    _tick(cx, "Skin", cs.NUTRIENTS, _item("Zinc", 6))
    html = pr._line_html(pr.plan(cx, "a49", {})[cs.NUTRIENTS][0], False)
    assert "plan-dose" not in html and "Gut · score 7 · 15 mg" in html


def test_an_extra_never_stands_in_for_a_catalog_rows_withdrawn_approval(cx):
    from dashboard.biofield_clinical_proposals import save_selection
    cs.upsert_item(cx, "Gut", cs.FORMULATIONS, _item("Soothe Powder", 7, "Row.", False))
    cs.set_extra_explanations(cx, "Gut", {"Soothe Powder": {
        "section": cs.FORMULATIONS, "explanation": "Old extra.", "explanation_approved": True}})
    save_selection(cx, "a49", "Gut", ["Soothe Powder"])
    assert pr.plan(cx, "a49")[cs.FORMULATIONS][0]["entries"][0]["explanation"] == ""


def test_a_file_without_the_fields_keeps_stored_explanations(cx):
    cs.upsert_item(cx, "Gut", cs.NUTRIENTS, _item("Zinc", 7, "Zinc text.", True))
    older = _item("Zinc", 8)
    del older["explanation"], older["explanation_approved"]
    cs.upsert_item(cx, "Gut", cs.NUTRIENTS, older)
    assert cx.execute("SELECT overall,explanation,explanation_approved FROM "
                      "biofield_clinical_section_item").fetchone() == (8, "Zinc text.", 1)
    cs.set_extra_explanations(cx, "Gut", {"X": {"explanation": "x",
                                                 "explanation_approved": True}})
    assert cs.set_extra_explanations(cx, "Gut", None) == (0, 0)
    assert cx.execute("SELECT COUNT(*) FROM biofield_clinical_extra_explanation").fetchone() == (1,)


def test_an_unrated_prl_tick_is_named_not_dropped(cx):
    from dashboard import biofield_prl_section as prl
    prl.ensure_schema(cx)
    cx.execute("INSERT INTO biofield_prl_pick (test_id,focus_area_id,name_key,checked) "
               "VALUES ('a49',1,'detox drops',1)")
    assert pr.lost_ticks(cx, "a49") == [("prl", "PRL", "detox drops", "it has no rating row")]
