"""The five reports, their section buttons, and the invoice that follows the primary
report (Glen, 2026-10-19). Brief: 00 System/pillar-inboxes/platform/
2026-10-19-report-split-and-section-buttons.md.

PDF rendering is always stubbed and publishing is recorded, never run.
"""
import sqlite3

import pytest

from dashboard import biofield_clinical_sections as cs
from dashboard import biofield_plan_report as pr
from dashboard import biofield_report_split as rs

INFO = "ED12 Kidney Energetic Driver Infoceutical"
CATALOG = [{"slug": "liver-support", "name": "Liver Support"},
           {"slug": "ed12", "name": INFO},
           {"slug": "soothe-pure", "name": "Soothe Pure Powder"},
           {"slug": "zinc-glycinate", "name": "Zinc glycinate"}]


@pytest.fixture(autouse=True)
def _no_gate(monkeypatch):
    monkeypatch.delenv("CONSOLE_SECRET", raising=False)
    import dashboard as _d
    monkeypatch.setattr(_d, "CONSOLE_SECRET", "", raising=False)


def _item(name, overall, explanation="", approved=False, **extra):
    return dict({"name": name, "overall": overall, "research": 5, "uniqueness": 5,
                 "reason": "INTERNAL", "source": "src", "botanical": False,
                 "lowers_bp": None, "explanation": explanation,
                 "explanation_approved": approved}, **extra)


def _tick(cx, tid, label, section, item):
    cs.upsert_item(cx, label, section, item, origin_test=tid)
    cs.save_pick(cx, tid, label, section, item["name"], checked=True)


def _tick_new_section(cx, tid, label, section, name, overall=7):
    """A tick under a section the condition files do not hold yet (Pure Powders,
    Amazon), written as the loader will write it once clinical adds the key."""
    cs.ensure_schema(cx)
    from dashboard.biofield_clinical_checklist import _norm
    cx.execute("INSERT INTO biofield_clinical_section_item (item_key,section,name_key,name,"
               "overall,reviewed,origin_test,explanation,explanation_approved) "
               "VALUES (?,?,?,?,?,1,'','',0)", (_norm(label), section, _norm(name), name, overall))
    cx.execute("INSERT INTO biofield_clinical_section_pick (test_id,item_key,section,name_key,"
               "checked) VALUES (?,?,?,?,1)", (str(tid), _norm(label), section, _norm(name)))
    cx.commit()


@pytest.fixture
def cx(tmp_path):
    con = sqlite3.connect(str(tmp_path / "chat_log.db"))
    yield con
    con.close()


# ---- Defaults and saved settings ----------------------------------------------

def test_defaults_per_report(cx):
    on = rs.settings(cx, "a1")
    assert on["biofield"] == {cs.FORMULATIONS, cs.INFOCEUTICALS}
    assert on["diet"] == {"foods_emphasize", "foods_avoid"}
    assert on["healing_tools"] == {cs.HEALING_TOOLS}
    assert on["ingredients"] == {cs.NUTRIENTS, cs.BOTANICALS, cs.ESSENTIAL_OILS}
    assert on["remedy_sources"] == {"prl", cs.FULLSCRIPT, "pure_powders", "amazon",
                                    cs.FORMULATIONS, cs.INFOCEUTICALS}


def test_a_switch_is_saved_per_client_and_reloads(tmp_path):
    db = str(tmp_path / "c.db")
    with sqlite3.connect(db) as con:
        rs.set_section(con, "a1", "diet", cs.NUTRIENTS, True)
        rs.set_section(con, "a1", "diet", "foods_avoid", False)
    with sqlite3.connect(db) as con:      # a fresh connection: read from disk
        assert rs.settings(con, "a1")["diet"] == {"foods_emphasize", cs.NUTRIENTS}
        # Another client starts from the defaults.
        assert rs.settings(con, "a2")["diet"] == {"foods_emphasize", "foods_avoid"}
        rs.set_section(con, "a1", "diet", "foods_avoid", True)
        assert rs.settings(con, "a1")["diet"] == {"foods_emphasize", "foods_avoid",
                                                  cs.NUTRIENTS}


def test_an_unknown_report_or_section_is_refused(cx):
    with pytest.raises(ValueError):
        rs.set_section(cx, "a1", "plan", cs.NUTRIENTS, True)
    with pytest.raises(ValueError):
        rs.set_section(cx, "a1", "diet", "lifestyle", True)


# ---- Each report prints only its switched-on sections -------------------------

def _seed(cx, tid="a49"):
    _tick(cx, tid, "Gut", "foods_emphasize", _item("Bone broth", 8, "Broth soothes.", True))
    _tick(cx, tid, "Gut", "foods_avoid", _item("Gluten", 6))
    _tick(cx, tid, "Gut", cs.NUTRIENTS, _item("Zinc glycinate", 7, "Zinc helps.", True,
                                              dose="15 mg"))
    _tick(cx, tid, "Gut", cs.HEALING_TOOLS, _item("Castor oil pack", 6))
    _tick(cx, tid, "Gut", cs.FULLSCRIPT, _item("Fullscript Fish Oil", 5))


def _ctx(cx, tid="a49", **kw):
    return rs.load(cx, tid, "Josie Gagnon", "19 October 2026", **kw)


def test_each_report_prints_only_its_sections(cx):
    _seed(cx)
    ctx, on = _ctx(cx), rs.settings(cx, "a49")
    diet = rs.render_report(ctx, "diet", on["diet"])
    assert "<h1>Diet</h1>" in diet and "Bone broth" in diet and "Gluten" in diet
    assert "Zinc glycinate" not in diet and "Castor oil pack" not in diet
    tools = rs.render_report(ctx, "healing_tools", on["healing_tools"])
    assert "Castor oil pack" in tools and "Bone broth" not in tools
    ingredients = rs.render_report(ctx, "ingredients", on["ingredients"])
    assert "Zinc glycinate" in ingredients and "Fullscript Fish Oil" not in ingredients
    sources = rs.render_report(ctx, "remedy_sources", on["remedy_sources"])
    assert "Fullscript Fish Oil" in sources and "Zinc glycinate" not in sources
    assert "Foods, Remedies and Healing Tools Plan" not in sources


def test_any_section_can_be_switched_on_in_any_report(cx):
    _seed(cx)
    rs.set_section(cx, "a49", "diet", cs.NUTRIENTS, True)
    rs.set_section(cx, "a49", "diet", "foods_avoid", False)
    on = rs.settings(cx, "a49")
    diet = rs.render_report(_ctx(cx), "diet", on["diet"])
    assert "Zinc glycinate" in diet and "Zinc helps." in diet and "Gluten" not in diet


def test_the_default_plan_render_is_unchanged_by_the_new_parameters(cx):
    """The single Plan's output must not move: the stand-alone reports reuse it."""
    _seed(cx)
    groups = pr.plan(cx, "a49")
    assert pr.render_plan_section(groups, "J", "d") == pr.render_plan_section(
        groups, "J", "d", headings=pr.HEADINGS, title=pr.PLAN_TITLE, extra="")


def test_a_new_section_key_is_picked_up_when_its_data_lands(cx):
    _tick_new_section(cx, "a49", "Gut", "pure_powders", "Soothe Pure Powder")
    ctx = _ctx(cx)
    assert rs.counts(ctx, "remedy_sources")["pure_powders"] == 1
    assert rs.counts(ctx, "remedy_sources")["amazon"] is None   # "no data yet"
    assert "Soothe Pure Powder" in rs.render_report(
        ctx, "remedy_sources", rs.settings(cx, "a49")["remedy_sources"])


def test_the_buttons_say_no_data_yet_for_pure_powders_and_amazon(cx):
    _seed(cx)
    ctx = _ctx(cx)
    row = rs.button_row("a49", "diet", rs.settings(cx, "a49")["diet"], rs.counts(ctx, "diet"))
    assert row.count("no data yet") == 2
    assert "data-section='foods_emphasize' aria-pressed=true" in row
    assert "data-section='nutrients' aria-pressed=false" in row


# ---- Remedy Sources: the in-program note --------------------------------------

def test_in_program_matches_by_code_and_by_name_with_terrain_restore():
    chain = ["Digestzymes in Terrain Restore", "Microbiome",
             "EI10 Circulation/Pericardium Meridian Energetic Integrator Infoceutical"]
    assert rs.in_program("Digestzymes", chain)
    assert rs.in_program("microbiome", chain)
    assert rs.in_program("EI10 something else Infoceutical", chain)
    assert not rs.in_program("Fibrosolve", chain)
    assert not rs.in_program("ED12 Kidney Energetic Driver Infoceutical", chain)
    # A name that merely contains another is not the same remedy.
    assert not rs.in_program("Microbiome Plus", chain)


def test_remedy_sources_lists_recommended_then_purple_with_the_note(cx):
    from dashboard.biofield_clinical_proposals import save_selection
    save_selection(cx, "a49", "Gut", ["Microbiome", "Fibrosolve", INFO])
    marks = {"recommended": {"ED12", "ET16"}, "purple": {"ED12", "MB8", "Iron"}}
    names = {"ED12": INFO, "ET16": "ET16 Glial Cell Terrain Infoceutical",
             "MB8": "MB8 Love Infoceutical"}
    groups = pr.plan(cx, "a49")
    chain = ["Microbiome", INFO]
    info = rs.remedy_source_infoceuticals(groups, marks, chain, names, ["ET16", "ED12"])
    assert [l["name"] for l in info] == ["ET16 Glial Cell Terrain Infoceutical", INFO,
                                         "MB8 Love Infoceutical"]   # nutrient "Iron" left out
    assert [l["note"] for l in info] == ["", rs.IN_PROGRAM_NOTE, ""]
    assert info[1]["source"] == "One of the scan's 5 recommended and purple on the scan"
    assert info[1]["entries"]           # ticked: its condition prints under it
    ff = rs.remedy_source_formulations(groups, chain)
    assert {l["name"]: l["note"] for l in ff} == {"Microbiome": rs.IN_PROGRAM_NOTE,
                                                  "Fibrosolve": ""}
    cx.execute("CREATE TABLE IF NOT EXISTS biofield_auth_chain (test_id INT, remedy TEXT)")
    cx.executemany("INSERT INTO biofield_auth_chain VALUES (49, ?)", [(r,) for r in chain])
    page = rs.render_report(_ctx(cx, marks=marks, names=names), "remedy_sources",
                            rs.settings(cx, "a49")["remedy_sources"])
    assert page.count(rs.IN_PROGRAM_NOTE) == 2
    assert page.index("<h2>Functional Formulations</h2>") < page.index("<h2>Infoceuticals</h2>")
    rs.set_section(cx, "a49", "remedy_sources", cs.INFOCEUTICALS, False)
    page = rs.render_report(_ctx(cx, marks=marks, names=names), "remedy_sources",
                            rs.settings(cx, "a49")["remedy_sources"])
    assert "<h2>Infoceuticals</h2>" not in page and "MB8" not in page


# ---- Amazon disclosure -------------------------------------------------------

def test_the_amazon_disclosure_prints_only_with_an_amazon_section(cx):
    _seed(cx)
    on = rs.settings(cx, "a49")
    assert rs.AMAZON_DISCLOSURE not in rs.render_report(_ctx(cx), "remedy_sources",
                                                        on["remedy_sources"])  # no lines yet
    _tick_new_section(cx, "a49", "Gut", "amazon", "Amazon Castor Oil")
    ctx = _ctx(cx)
    assert rs.AMAZON_DISCLOSURE in rs.render_report(ctx, "remedy_sources", on["remedy_sources"])
    assert rs.AMAZON_DISCLOSURE not in rs.render_report(ctx, "diet", on["diet"])
    rs.set_section(cx, "a49", "remedy_sources", "amazon", False)
    assert rs.AMAZON_DISCLOSURE not in rs.render_report(
        ctx, "remedy_sources", rs.settings(cx, "a49")["remedy_sources"])


# ---- The invoice ---------------------------------------------------------------

def test_invoice_remedies_follow_the_primary_switches():
    remedies = [{"name": "Liver Support", "qty": 2}, {"name": INFO, "qty": 1}]
    assert rs.invoice_remedies(remedies, rs.DEFAULTS["biofield"]) == remedies
    assert rs.invoice_remedies(remedies, {cs.FORMULATIONS}) == remedies[:1]
    assert rs.invoice_remedies(remedies, {cs.INFOCEUTICALS}) == remedies[1:]
    assert rs.left_off(remedies, {cs.FORMULATIONS}) == [INFO]
    # Pure Powders only when on in the primary report.
    assert rs.invoice_remedies(remedies, rs.DEFAULTS["biofield"], ["Soothe Pure Powder"]) \
        == remedies
    assert rs.invoice_remedies(remedies, rs.DEFAULTS["biofield"] | {"pure_powders"},
                               ["Soothe Pure Powder"]) \
        == remedies + [{"name": "Soothe Pure Powder", "qty": 1}]


@pytest.fixture
def app_client(tmp_path, monkeypatch):
    import biofield_local_app as bla
    from dashboard import biofield_invoice as bi
    from dashboard.biofield_authoring import add_chain_row, create_test, init_auth_tables
    written, published, calls = [], [], {}

    def fake_pdf(html, out):
        written.append((html, out))
        with open(out, "wb") as f:
            f.write(b"%PDF fake")
        return out
    monkeypatch.setattr(bla, "save_report_pdf", fake_pdf)
    monkeypatch.setattr(bi, "default_handoff_push",
                        lambda *a, **k: calls.setdefault("pushed", True) and {"ok": True})
    monkeypatch.setenv("BIOFIELD_REPORTS_DIR", str(tmp_path))
    db = str(tmp_path / "chat_log.db")
    with sqlite3.connect(db) as con:
        init_auth_tables(con)
        n = create_test(con, name="Josie Gagnon", email="j@x.com", date="2026-10-19")
        add_chain_row(con, n, 1, "", "", "Liver Support")
        add_chain_row(con, n, 2, "", "", INFO)
        con.execute("UPDATE biofield_auth_chain SET bottles='2' WHERE remedy='Liver Support'")

    def fake_create(customer, lines, replace_open=False, invoice_note=None,
                    update_order_id=None, idempotency_key=""):
        calls["lines"] = lines
        return {"ok": True, "order_id": 7, "external_ref": "INH", "total_cents": 1}
    c = bla.create_app(db, invoice_fetch_catalog=lambda: CATALOG, invoice_create=fake_create,
                       invoice_link=lambda oid: {"ok": False},
                       invoice_paid_check=lambda email: {},
                       invoice_latest=lambda email: {"ok": False},
                       scan_lookup=lambda email: {},
                       auto_publish=lambda *a, **k: published.append(a) or {}).test_client()
    c.db, c.written, c.calls, c.published = db, written, calls, published
    c.tid = f"a{n}" if not str(n).startswith("a") else str(n)
    return c


# What the invoice raised before this change, for this chain: the fee, then each
# layer remedy at its own bottle count.
TODAY = [{"slug": "biofield-analysis", "qty": 1},
         {"slug": "liver-support", "qty": 2, "source": "biofield"},
         {"slug": "ed12", "qty": 1, "source": "biofield"}]


def _toggle(c, report, section, on):
    r = c.post(f"/test/{c.tid}/report-sections",
               json={"report": report, "section": section, "on": on})
    assert r.status_code == 200 and r.get_json()["ok"], r.data
    return r.get_json()


@pytest.mark.parametrize("route", ["invoice", "handoff"])
def test_the_invoice_is_unchanged_at_the_defaults(app_client, route):
    c = app_client
    j = c.post(f"/author/{c.tid}/{route}", json={}).get_json()
    assert j["ok"], j
    assert c.calls["lines"] == TODAY


@pytest.mark.parametrize("route", ["invoice", "handoff"])
def test_a_primary_section_switched_off_comes_off_the_invoice(app_client, route):
    c = app_client
    _toggle(c, "biofield", cs.INFOCEUTICALS, False)
    j = c.post(f"/author/{c.tid}/{route}", json={}).get_json()
    assert c.calls["lines"] == TODAY[:2]
    if route == "invoice":
        assert j["left_off"] == [INFO]
    _toggle(c, "biofield", cs.INFOCEUTICALS, True)
    _toggle(c, "biofield", cs.FORMULATIONS, False)
    c.post(f"/author/{c.tid}/{route}", json={})
    assert c.calls["lines"] == [TODAY[0], TODAY[2]]


@pytest.mark.parametrize("route", ["invoice", "handoff"])
def test_stand_alone_sections_never_add_invoice_lines(app_client, route):
    c = app_client
    with sqlite3.connect(c.db) as con:
        _tick(con, c.tid, "Gut", cs.NUTRIENTS, _item("Zinc glycinate", 7))
        _tick_new_section(con, c.tid, "Gut", "pure_powders", "Soothe Pure Powder")
    for report in rs.STAND_ALONE:
        for section in rs.SECTION_KEYS:
            _toggle(c, report, section, True)
    c.post(f"/author/{c.tid}/{route}", json={})
    assert c.calls["lines"] == TODAY


@pytest.mark.parametrize("route", ["invoice", "handoff"])
def test_pure_powders_on_in_the_primary_report_adds_their_lines(app_client, route):
    c = app_client
    with sqlite3.connect(c.db) as con:
        _tick_new_section(con, c.tid, "Gut", "pure_powders", "Soothe Pure Powder")
    c.post(f"/author/{c.tid}/{route}", json={})
    assert c.calls["lines"] == TODAY
    _toggle(c, "biofield", "pure_powders", True)
    c.post(f"/author/{c.tid}/{route}", json={})
    assert c.calls["lines"] == TODAY + [{"slug": "soothe-pure", "qty": 1, "source": "biofield"}]


def test_the_toggle_route_refuses_bad_input(app_client):
    c = app_client
    assert c.post(f"/test/{c.tid}/report-sections",
                  json={"report": "diet", "section": "nutrients", "on": "yes"}).status_code == 400
    assert c.post(f"/test/{c.tid}/report-sections",
                  json={"report": "x", "section": "nutrients", "on": True}).status_code == 400


# ---- The primary report --------------------------------------------------------

def test_the_primary_report_is_unchanged_at_defaults_and_takes_added_sections(app_client):
    c = app_client
    with sqlite3.connect(c.db) as con:
        _tick(con, c.tid, "Gut", "foods_emphasize", _item("Bone broth", 8, "Broth.", True))
        from dashboard.biofield_clinical_proposals import save_selection
        save_selection(con, c.tid, "Gut", ["Fibrosolve"])   # ticked, not on a layer
    c.get(f"/test/{c.tid}/report.pdf")
    html, _ = c.written[-1]
    assert "plan-appended" not in html and "Bone broth" not in html
    assert "Fibrosolve" not in html
    _toggle(c, "biofield", "foods_emphasize", True)
    c.get(f"/test/{c.tid}/report.pdf")
    html, _ = c.written[-1]
    assert "plan-appended" in html and "Bone broth" in html and "Broth." in html
    assert "Fibrosolve" not in html   # the layer sections never print twice


# ---- Printing ------------------------------------------------------------------

def test_print_jobs_singly_and_grouped():
    picked = ["remedy_sources", "diet", "biofield", "nonsense"]
    assert rs.print_jobs(picked, "singly", "Josie Gagnon") == [
        ("Josie Gagnon - Diet.pdf", ["diet"]),
        ("Josie Gagnon - Remedy Sources.pdf", ["remedy_sources"])]
    assert rs.print_jobs(picked, "grouped", "Josie Gagnon") == [
        ("Josie Gagnon - Reports.pdf", ["diet", "remedy_sources"])]
    assert rs.print_jobs(["biofield"], "grouped", "J") == []
    assert rs.print_jobs(["diet"], "singly", "A/B: C") == [("A B C - Diet.pdf", ["diet"])]


def test_the_pdf_route_makes_the_right_files_in_order(app_client):
    c = app_client
    with sqlite3.connect(c.db) as con:
        _seed(con, c.tid)
    r = c.get(f"/test/{c.tid}/reports.pdf?mode=singly&r=diet&download=1")
    assert r.status_code == 200 and r.data == b"%PDF fake"
    assert 'attachment; filename="Josie Gagnon - Diet.pdf"' in r.headers["Content-Disposition"]
    html, out = c.written[-1]
    assert out.endswith("/Josie Gagnon - Diet.pdf") and "Bone broth" in html
    assert "Castor oil pack" not in html
    r = c.get(f"/test/{c.tid}/reports.pdf?mode=grouped&r=remedy_sources&r=healing_tools&r=diet")
    assert r.headers["Content-Disposition"].startswith('inline; filename="Josie Gagnon - Reports.pdf"')
    html, out = c.written[-1]
    assert out.endswith("/Josie Gagnon - Reports.pdf")
    assert html.index("<h1>Diet</h1>") < html.index("<h1>Healing Tools</h1>") \
        < html.index("<h1>Remedy Sources</h1>")
    assert "<h1>Ingredients</h1>" not in html and html.count("break-before:page") == 2
    # Singly means one file per request; the primary report is not part of it.
    assert c.get(f"/test/{c.tid}/reports.pdf?mode=singly&r=diet&r=ingredients").status_code == 400
    assert c.get(f"/test/{c.tid}/reports.pdf?mode=grouped&r=biofield").status_code == 400
    assert c.get(f"/test/{c.tid}/reports.pdf?mode=both&r=diet").status_code == 400
    assert c.published == []          # stand-alone reports never publish


def test_the_reports_page_shows_buttons_ticks_and_the_switch(app_client):
    c = app_client
    page = c.get(f"/test/{c.tid}/reports").get_data(as_text=True)
    for title in ("Biofield Analysis", "Diet", "Healing Tools", "Ingredients", "Remedy Sources"):
        assert f"<h2 class=rs-rt>{title}</h2>" in page
    assert page.count("class=rs-btns") == 5
    assert page.count("class=rs-pt ") == 4           # no tick for the primary report
    assert "data-m=singly" in page and "data-m=grouped" in page
    assert f"/test/{c.tid}/report.pdf" in page        # the primary's own Print
