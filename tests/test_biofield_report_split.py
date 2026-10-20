"""The five reports, their section buttons, and the invoice that follows the primary
report (Glen, 2026-10-19). Brief: 00 System/pillar-inboxes/platform/
2026-10-19-report-split-and-section-buttons.md.

PDF rendering is always stubbed and publishing is recorded, never run.
"""
import os
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
    html, work = c.written[-1]
    folder = os.path.dirname(work)
    assert os.path.exists(os.path.join(folder, "Josie Gagnon - Diet.pdf"))
    assert "Bone broth" in html and "Castor oil pack" not in html
    # No scan for this client: Remedy Sources with Infoceuticals on is refused.
    grouped = f"/test/{c.tid}/reports.pdf?mode=grouped&r=remedy_sources&r=healing_tools&r=diet"
    r = c.get(grouped)
    assert r.status_code == 409 and b"Remedy Sources was not made" in r.data
    _toggle(c, "remedy_sources", cs.INFOCEUTICALS, False)
    r = c.get(grouped)
    assert r.headers["Content-Disposition"].startswith('inline; filename="Josie Gagnon - Reports.pdf"')
    html, _ = c.written[-1]
    assert os.path.exists(os.path.join(folder, "Josie Gagnon - Reports.pdf"))
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


# ---- Review fixes, 2026-10-19 --------------------------------------------------

def _frame(cx, **lines):
    pr.ensure_schema(cx)
    cx.executemany("INSERT OR REPLACE INTO biofield_plan_wording (kind,key,text) "
                   "VALUES ('frame',?,?)", list(lines.items()))
    cx.commit()


@pytest.mark.parametrize("route", ["invoice", "handoff"])
def test_glen_sees_what_the_invoice_left_off(app_client, route):
    c = app_client
    _toggle(c, "biofield", cs.INFOCEUTICALS, False)
    j = c.post(f"/author/{c.tid}/{route}", json={}).get_json()
    warning = j["warning"] if route == "invoice" else j["invoice"]["warning"]
    assert INFO in warning and "switched off in the Biofield Analysis" in warning
    assert "fee only" not in warning
    _toggle(c, "biofield", cs.FORMULATIONS, False)
    j = c.post(f"/author/{c.tid}/{route}", json={}).get_json()
    assert c.calls["lines"] == TODAY[:1]
    warning = j["warning"] if route == "invoice" else j["invoice"]["warning"]
    assert warning.startswith("This invoice holds the Biofield Analysis fee only.")
    assert "Liver Support" in warning and INFO in warning


def test_the_left_off_warning_reaches_both_pages():
    from dashboard import biofield_report_html as rh
    import inspect
    src = inspect.getsource(rh)
    assert "if(iv.warning){m+=' '+iv.warning;}" in src      # the hand-off message
    assert "if(j.warning){parts.push(j.warning);}" in src   # the Invoice button


def test_the_left_off_warning_is_escaped_for_the_invoice_page(app_client):
    c = app_client
    with sqlite3.connect(c.db) as con:
        con.execute("UPDATE biofield_auth_chain SET remedy='<b>X</b> Infoceutical' "
                    "WHERE remedy=?", (INFO,))
    _toggle(c, "biofield", cs.INFOCEUTICALS, False)
    j = c.post(f"/author/{c.tid}/invoice", json={}).get_json()
    assert "<b>" not in j["warning"] and "&lt;b&gt;" in j["warning"]


def test_plan_wide_lines_print_only_in_reports_they_concern(cx):
    _seed(cx)
    _tick(cx, "a49", "Vertigo", cs.NUTRIENTS, _item("Ginkgo", 6))
    _frame(cx, dose_totals="Count every source.", thyroid_spacing="Space thyroid medicine.",
           vertigo_safety="Vertigo needs care.")
    ctx, on = _ctx(cx), rs.settings(cx, "a49")
    diet = rs.render_report(ctx, "diet", on["diet"])
    tools = rs.render_report(ctx, "healing_tools", on["healing_tools"])
    for page in (diet, tools):
        assert "Count every source." not in page and "Space thyroid medicine." not in page
        # The Vertigo item is a nutrient, switched off here.
        assert "Vertigo needs care." not in page
    ingredients = rs.render_report(ctx, "ingredients", on["ingredients"])
    assert "Count every source." in ingredients and "Space thyroid medicine." in ingredients
    assert "Vertigo needs care." in ingredients
    sources = rs.render_report(ctx, "remedy_sources", on["remedy_sources"])
    assert "Count every source." in sources          # Fullscript Fish Oil
    assert "Vertigo needs care." not in sources
    # Grouped: once per report it concerns, never in the others.
    page = rs.grouped_html(ctx, ["diet", "healing_tools", "ingredients"], on)
    assert page.count("Count every source.") == 1 and page.count("Vertigo needs care.") == 1
    # The intro prints once in a Grouped file, and in each report printed singly.
    cx.execute("INSERT INTO biofield_plan_wording VALUES ('intro','0','Chosen for you.')")
    cx.commit()
    ctx = _ctx(cx)
    assert ctx["intro"] == ["Chosen for you."] or tuple(ctx["intro"]) == ("Chosen for you.",)
    assert rs.grouped_html(ctx, ["diet", "healing_tools"], on).count("Chosen for you.") == 1
    assert "Chosen for you." in rs.render_report(ctx, "healing_tools", on["healing_tools"])


def test_a_caution_follows_only_printed_sections(cx):
    """The caution block is worked out from the printed sections alone."""
    _seed(cx)
    ctx = _ctx(cx)
    ctx["cautions"] = [("Zinc", "Watch zinc.", {(cs.NUTRIENTS, "zinc glycinate")})]
    on = rs.settings(cx, "a49")
    assert "Watch zinc." in rs.render_report(ctx, "ingredients", on["ingredients"])
    assert "Watch zinc." not in rs.render_report(ctx, "diet", on["diet"])


def test_remedy_sources_infoceuticals_keep_the_e4l_header(cx):
    _frame(cx, infoceutical_header="These matches are E4L's own.")
    marks = {"recommended": {"ET16"}, "purple": set()}
    names = {"ET16": "ET16 Glial Cell Terrain Infoceutical"}
    page = rs.render_report(_ctx(cx, marks=marks, names=names), "remedy_sources",
                            rs.settings(cx, "a49")["remedy_sources"])
    head = page.index("<h2>Infoceuticals</h2>")
    assert page.index("These matches are E4L&#x27;s own.") > head
    assert page.index("These matches are E4L&#x27;s own.") < page.index("ET16 Glial")


def test_one_test_has_one_key_and_filemaker_never_reads_an_authored_test(cx):
    assert rs.test_key("a48") == rs.test_key("a048") == rs.test_key(48, authored=True) \
        == rs.test_key("48", authored=True) == "a48"
    assert rs.test_key("48") == "fmp:48" != rs.test_key("a48")
    with pytest.raises(ValueError):
        rs.test_key("abc", authored=True)
    rs.set_section(cx, "48", "diet", cs.NUTRIENTS, True)       # FileMaker test 48
    assert cs.NUTRIENTS not in rs.settings(cx, "a48")["diet"]
    rs.set_section(cx, "a48", "diet", cs.HEALING_TOOLS, True)
    assert cs.HEALING_TOOLS in rs.settings(cx, "48", authored=True)["diet"]
    assert cs.HEALING_TOOLS not in rs.settings(cx, "48")["diet"]
    # FileMaker test 48 never reads authored test a48's layers.
    cx.execute("CREATE TABLE IF NOT EXISTS biofield_auth_chain (test_id INT, remedy TEXT)")
    cx.execute("INSERT INTO biofield_auth_chain VALUES (48, 'Microbiome')")
    assert rs.load(cx, "48")["chain"] == []
    assert rs.load(cx, "a48")["chain"] == ["Microbiome"]


def test_the_author_invoice_reads_the_authored_tests_switches(app_client):
    """/author/1 is authored test a1, so a switch saved on /test/a1 reaches it, and
    one saved for FileMaker test 1 does not."""
    c = app_client
    num = c.tid[1:]
    r = c.post(f"/test/{num}/report-sections",
               json={"report": "biofield", "section": cs.INFOCEUTICALS, "on": False})
    assert r.status_code == 200
    c.post(f"/author/{num}/invoice", json={})
    assert c.calls["lines"] == TODAY
    _toggle(c, "biofield", cs.INFOCEUTICALS, False)
    c.post(f"/author/{num}/invoice", json={})
    assert c.calls["lines"] == TODAY[:2]


def test_glen_sees_a_note_when_the_scan_is_missing_or_unreadable(app_client):
    c = app_client
    page = c.get(f"/test/{c.tid}/reports").get_data(as_text=True)
    assert "No scan was found for this client" in page and "plan-glen" in page
    import biofield_local_app as bla

    def broken(email):
        raise RuntimeError("disk gone")
    c2 = bla.create_app(c.db, scan_lookup=broken).test_client()
    page = c2.get(f"/test/{c.tid}/reports").get_data(as_text=True)
    assert "The scan could not be read" in page
    r = c2.get(f"/test/{c.tid}/reports.pdf?mode=singly&r=remedy_sources")
    assert r.status_code == 409 and b"could not be read" in r.data
    # Reports that do not use the scan still print.
    assert c.get(f"/test/{c.tid}/reports.pdf?mode=singly&r=diet").status_code == 200


def test_a_scan_code_with_no_name_is_left_out_and_named_to_glen(cx):
    marks = {"recommended": {"ET16", "ZZ9"}, "purple": set()}
    names = {"ET16": "ET16 Glial Cell Terrain Infoceutical"}
    ctx = _ctx(cx, marks=marks, names=names)
    assert [l["name"] for l in ctx["remedy_source_infoceuticals"]] == [
        "ET16 Glial Cell Terrain Infoceutical"]
    assert ctx["unnamed_codes"] == ["ZZ9"]
    page = rs.render_report(ctx, "remedy_sources", rs.settings(cx, "a49")["remedy_sources"])
    assert "ZZ9" not in page


def test_each_pdf_request_renders_to_its_own_file(app_client):
    c = app_client
    for _ in range(2):
        assert c.get(f"/test/{c.tid}/reports.pdf?mode=singly&r=diet").status_code == 200
    (_, first), (_, second) = c.written[-2:]
    folder = os.path.dirname(first)
    final = os.path.join(folder, "Josie Gagnon - Diet.pdf")
    assert first != second and final not in (first, second)
    assert os.path.exists(final)
    assert not [f for f in os.listdir(folder) if f.startswith(".rendering-")]


def test_print_choices_survive_the_reload(app_client):
    c = app_client
    page = c.get(f"/test/{c.tid}/reports").get_data(as_text=True)
    assert page.count("checked>") == 4 and "data-m=singly class=on" in page
    page = c.get(f"/test/{c.tid}/reports?mode=grouped&p=diet&p=ingredients") \
        .get_data(as_text=True)
    assert "value='diet' data-title='Diet' checked>" in page
    assert "value='ingredients' data-title='Ingredients' checked>" in page
    assert "value='healing_tools' data-title='Healing Tools'>" in page
    assert "data-m=grouped class=on" in page and "data-mode='grouped'" in page
    page = c.get(f"/test/{c.tid}/reports?p=").get_data(as_text=True)
    assert "checked>" not in page
    assert "history.replaceState" in rs.PAGE_JS and "function show(){keep();" in rs.PAGE_JS


def test_headings_print_once_and_prl_fullscript_are_labelled(cx):
    _seed(cx)
    _tick_new_section(cx, "a49", "Gut", "prl", "PRL Calm", 6)
    ctx, on = _ctx(cx), rs.settings(cx, "a49")
    diet = rs.render_report(ctx, "diet", on["diet"])
    assert diet.count(">Diet<") == 1
    tools = rs.render_report(ctx, "healing_tools", on["healing_tools"])
    assert tools.count(">Healing Tools<") == 1
    sources = rs.render_report(ctx, "remedy_sources", on["remedy_sources"])
    assert "<h2>PRL and Fullscript supplements</h2>" in sources


@pytest.mark.parametrize("name,want", [
    ("A/B: C", "A B C"), ('Jo "Jo" Lee', "Jo Jo Lee"), ("Ann\x00\x07\tBee", "Ann Bee"),
    ("../etc", "etc"), ("", "Client"), ("\x01", "Client"), ("x" * 200, "x" * 80)])
def test_file_names_are_safe(name, want):
    assert rs._safe(name) == want


def test_grouped_reports_each_start_a_page_and_sections_flow(cx):
    """Glen, 2026-10-19: each report starts a new page; sections inside flow on, and
    no heading sits alone at a page foot."""
    import re
    _seed(cx)
    ctx = _ctx(cx)
    on = {r: set(rs.DEFAULTS[r]) for r in rs.STAND_ALONE}
    page = rs.grouped_html(ctx, ["diet", "healing_tools", "ingredients"], on)
    starts = re.findall(r"<div class=rs-report style='([^']*)'>", page)
    assert starts == ["", "break-before:page", "break-before:page"]
    assert page.count("break-before:page") == 2       # no forced break inside a report
    assert "h1,h2,h3,h4{break-after:avoid" in page


def test_each_report_has_a_lead_then_a_personal_slot(cx):
    """Glen, 2026-10-19: the standard lead first, the same for every client; a
    personal passage after it (phase 2) that prints nothing while empty."""
    _seed(cx)
    on = {r: set(rs.DEFAULTS[r]) for r in rs.STAND_ALONE}
    ctx = _ctx(cx)
    assert rs.report_intro(ctx, "diet") == () and ctx["report_personal"] == {}
    cx.execute("INSERT INTO biofield_plan_wording VALUES ('report_lead','diet:1','Food is the base.')")
    cx.commit()
    ctx = _ctx(cx)
    assert rs.report_intro(ctx, "diet") == ("Food is the base.",)
    assert rs.report_intro(ctx, "ingredients") == ()
    ctx["report_personal"] = {"diet": ["For your energy and sleep."], "ingredients": ["  "]}
    diet = rs.render_report(ctx, "diet", on["diet"])
    assert diet.index("Food is the base.") < diet.index("For your energy and sleep.")
    assert "For your energy and sleep." not in rs.render_report(ctx, "ingredients", on["ingredients"])
    page = rs.grouped_html(ctx, ["healing_tools", "diet"], on)
    assert page.count("Food is the base.") == 1       # printed even when not first


def test_the_primary_report_reads_its_own_lead(cx):
    assert rs.primary_intro(cx) == ()
    cx.execute("INSERT INTO biofield_plan_wording VALUES ('report_lead','biofield:1','Your layers.')")
    cx.execute("INSERT INTO biofield_plan_wording VALUES ('report_lead','diet:1','Food first.')")
    cx.commit()
    assert rs.primary_intro(cx) == ("Your layers.",)
    assert rs.primary_intro(cx, ["For your sleep."]) == ("Your layers.", "For your sleep.")


# ---- A switched-off layer section leaves the report and the portal too ------------
# Glen, 2026-10-19: "Yes, I think so, at least for now. That way it is presented as the
# complete report for that individual at that time." The invoice, the printed report
# and the portal all use rs.layer_remedy_on, so they cannot disagree.

ED7 = "ED7 Stomach Energetic Driver Infoceutical"
PYRITE = "Pyrite Gem Elixir in Terrain Restore"
QUINTON = "Quinton Hypertonic Electrolytes in Terrain Restore"
RICH_CATALOG = CATALOG + [
    {"slug": "ed7", "name": ED7},
    {"slug": "microbiome", "name": "Microbiome"},
    {"slug": "terrain-restore", "name": "Terrain Restore"},
    {"slug": "pyrite-gem-elixir-in-terrain-restore", "name": PYRITE},
    {"slug": "quinton-in-terrain-restore", "name": QUINTON}]
NARRATIVE = ("1. Lens: Liver Support clears the lens.\n\n"
             "2. " + INFO + " restores what lies beneath.\n\n"
             "3. Blood: Pyrite Gem Elixir and Quinton rebuild the blood.\n\n"
             "4. Gut: Microbiome with " + ED7 + " settles the gut.")


@pytest.fixture
def rich(tmp_path, monkeypatch):
    """Josie-like chain: a formulation layer, an infoceutical-only layer, a Terrain
    Restore layer of two essences, and a layer holding one of each. Every outside
    call is stubbed: PDF, portal upload and publish, the hand-off push."""
    import biofield_local_app as bla
    from dashboard import biofield_invoice as bi
    from dashboard import biofield_portal_publish as bpp
    from dashboard.biofield_authoring import add_chain_row, create_test, init_auth_tables
    from dashboard.biofield_narrative import save_narrative
    rec = {"pdf_html": [], "portal": [], "pushed": [], "lines": None, "written": []}
    monkeypatch.setattr(bla, "_fetch_life_stress_curation", lambda email: None)

    def fake_pdf(html, out):
        rec["written"].append(html)
        with open(out, "wb") as f:
            f.write(b"%PDF fake")
        return out
    monkeypatch.setattr(bla, "save_report_pdf", fake_pdf)
    monkeypatch.setattr(bla, "report_pdf_bytes",
                        lambda html: rec["pdf_html"].append(html) or b"%PDF fake")
    monkeypatch.setattr(bpp, "load_catalog",
                        lambda: {c["slug"]: {"name": c["name"]} for c in RICH_CATALOG})
    # A fixed address: the real asset name is random on every upload.
    monkeypatch.setattr(bpp, "upload_asset", lambda data, name, **kw: "https://x/asset")
    monkeypatch.setattr(bpp, "publish_to_portal",
                        lambda payload, **kw: rec["portal"].append(payload) or {"ok": True})
    monkeypatch.setattr(bi, "default_handoff_push",
                        lambda email, name, content, **kw: rec["pushed"].append(content)
                        or {"ok": True})
    monkeypatch.setenv("PORTAL_PUBLISH_BASE_URL", "https://example.invalid")
    monkeypatch.setenv("BIOFIELD_REPORTS_DIR", str(tmp_path))
    db = str(tmp_path / "chat_log.db")
    with sqlite3.connect(db) as con:
        init_auth_tables(con)
        n = create_test(con, name="Josie Gagnon", email="j@x.com", date="2026-10-19")
        add_chain_row(con, n, 1, "Lens", "Lens", "Liver Support", "1 capsule", "daily",
                      "with breakfast")
        # Its paragraph names only the remedy, never the head "Renal", so the
        # narrative can be cut into cards only by the remedy as Glen wrote it.
        add_chain_row(con, n, 2, "Renal", "Renal", INFO, "1 sachet", "daily", "")
        add_chain_row(con, n, 3, "Blood", "Blood", PYRITE, "10 drops", "daily", "")
        add_chain_row(con, n, 3, "Blood", "Blood", QUINTON, "10 drops", "daily", "")
        add_chain_row(con, n, 4, "Gut", "Gut", "Microbiome", "1 capsule", "twice daily", "")
        add_chain_row(con, n, 4, "Gut", "Gut", ED7, "1 sachet", "daily", "")
        con.execute("UPDATE biofield_auth_chain SET bottles='3' WHERE remedy=?", (QUINTON,))
        save_narrative(con, n, NARRATIVE)
        # A finding on the Gut layer, placed there by ED7's row.
        from dashboard import biofield_stress as bs
        bs.add_stress(con, n, "Stomach acid")
        rid = con.execute("SELECT id FROM biofield_auth_chain WHERE remedy=?", (ED7,)).fetchone()[0]
        bs.cover_stress(con, n, bs.stress_id_for(con, n, "Stomach acid"), [rid])

    def fake_create(customer, lines, replace_open=False, invoice_note=None,
                    update_order_id=None, idempotency_key=""):
        rec["lines"] = lines
        return {"ok": True, "order_id": 7, "external_ref": "INH", "total_cents": 1}
    c = bla.create_app(db, invoice_fetch_catalog=lambda: RICH_CATALOG,
                       invoice_create=fake_create, invoice_link=lambda oid: {"ok": False},
                       invoice_paid_check=lambda email: {},
                       invoice_latest=lambda email: {"ok": False},
                       scan_lookup=lambda email: {}).test_client()
    c.db, c.rec = db, rec
    c.tid = n if str(n).startswith("a") else f"a{n}"
    return c


def _everything(c):
    """What each output carries now: the printed report (HTML and PDF), the portal
    content and its PDF (published on print), the hand-off seed, and both invoices."""
    rec = c.rec
    for k in ("pdf_html", "portal", "pushed", "written"):
        rec[k].clear()
    html = c.get(f"/test/{c.tid}/report").get_data(as_text=True)
    assert c.get(f"/test/{c.tid}/report.pdf").status_code == 200
    pdf_html = rec["written"][-1]
    portal, portal_pdf = rec["portal"][-1]["content"], rec["pdf_html"][-1]
    assert c.post(f"/author/{c.tid}/handoff", json={}).get_json()["ok"]
    seed, handoff_lines = rec["pushed"][-1], rec["lines"]
    assert c.post(f"/author/{c.tid}/invoice", json={}).get_json()["ok"]
    return {"html": html, "pdf_html": pdf_html, "portal": portal, "portal_pdf": portal_pdf,
            "seed": seed, "handoff_lines": handoff_lines, "invoice_lines": rec["lines"]}


def _outside_narrative(html):
    """The report with Glen's narrative cut out: what the code lists."""
    import re
    return re.sub(r'<div class="narrative">.*?</div>', "", html, flags=re.S)


def _narrative_div(html):
    import re
    return re.search(r'<div class="narrative">.*?</div>', html, flags=re.S).group(0)


def _slugs(items):
    return [i["slug"] for i in items]


def test_the_defaults_print_publish_and_bill_exactly_as_before(rich):
    """Pressing a switch that is already on writes a row; the outputs must still be
    byte for byte what the code without this change made."""
    from dashboard import biofield_portal_publish as bpp
    from dashboard.biofield_authoring import authored_report
    from dashboard.biofield_handoff import build_portal_seed
    from dashboard.biofield_narrative import get_narrative
    from dashboard.biofield_report_present import render_present
    c = rich
    before = _everything(c)
    with sqlite3.connect(c.db) as con:
        rep, narr = authored_report(con, c.tid), get_narrative(con, c.tid)
        # The old call, with no switch at all, is what the report printed before.
        assert before["html"] == render_present(rep, narr)
        assert before["pdf_html"] == before["html"]
        assert before["portal_pdf"] == render_present(rep, narr)
        old_portal = bpp.build_portal_content(con, c.tid, special_price_cents=0)
        assert bpp.build_portal_content(con, c.tid, special_price_cents=0,
                                        primary_on=rs.DEFAULTS["biofield"]) == old_portal
        resolve = lambda nm: __import__("dashboard.biofield_invoice", fromlist=["x"]) \
            .resolve_line_slug(nm, RICH_CATALOG)
        assert build_portal_seed(con, c.tid, resolve, name="Josie",
                                 primary_on=rs.DEFAULTS["biofield"]) \
            == build_portal_seed(con, c.tid, resolve, name="Josie")
    assert rs.primary_report(rep, rs.DEFAULTS["biofield"]) is rep
    # Other sections on in the primary report never touch the layer remedies.
    assert render_present(rep, narr, primary_on=rs.DEFAULTS["biofield"] | {
        "pure_powders", "foods_emphasize"}) == render_present(rep, narr)
    # Switches pressed off and back on: still exactly the same everywhere.
    for s in (cs.INFOCEUTICALS, cs.FORMULATIONS):
        _toggle(c, "biofield", s, False)
        _toggle(c, "biofield", s, True)
    assert _everything(c) == before
    assert _slugs(before["portal"]["reorder_items"]) == [
        "liver-support", "ed12", "terrain-restore", "microbiome", "ed7"]
    assert before["invoice_lines"][1:] == [
        {"slug": "liver-support", "qty": 1, "source": "biofield"},
        {"slug": "ed12", "qty": 1, "source": "biofield"},
        {"slug": "terrain-restore", "qty": 3, "source": "biofield",
         "note": "With Pyrite Gem Elixir and Quinton Hypertonic Electrolytes"},
        {"slug": "microbiome", "qty": 1, "source": "biofield"},
        {"slug": "ed7", "qty": 1, "source": "biofield"}]


def test_infoceuticals_off_leave_the_report_the_portal_and_the_invoice_together(rich):
    c = rich
    before = _everything(c)
    _toggle(c, "biofield", cs.INFOCEUTICALS, False)
    now = _everything(c)
    for key in ("html", "pdf_html", "portal_pdf"):
        listed = _outside_narrative(now[key])
        assert INFO not in listed and ED7 not in listed, key
        assert "Liver Support" in listed and "Microbiome" in listed, key
        # Glen's prose is untouched, names and all.
        assert _narrative_div(now[key]) == _narrative_div(before[key]), key
        # The Renal layer keeps its number and head, with no remedy.
        assert ("<td class=lyr rowspan=1>2</td><td rowspan=1>Renal</td>"
                "<td rowspan=1>Renal</td><td></td><td></td><td></td><td></td>") in listed, key
    # The portal: same cards and the same words on them; no infoceutical anywhere.
    p, b = now["portal"], before["portal"]
    assert [L["title"] for L in p["layers"]] == [L["title"] for L in b["layers"]]
    assert [L["meaning"] for L in p["layers"]] == [L["meaning"] for L in b["layers"]]
    assert p["layers"][1]["meaning"].startswith(INFO) and p["greeting"] == b["greeting"]
    # The layers' findings stay, even one placed there by a switched-off remedy.
    assert [L["stresses"] for L in p["layers"]] == [L["stresses"] for L in b["layers"]]
    assert [x["label"] for x in b["layers"][3]["stresses"]] == ["Stomach acid"]
    assert [L["remedy"] for L in p["layers"]] == [
        "Liver Support", "", PYRITE + " + " + QUINTON, "Microbiome"]
    assert _slugs(p["reorder_items"]) == ["liver-support", "terrain-restore", "microbiome"]
    assert p["reorder_items"][1]["note"] == \
        "With Pyrite Gem Elixir and Quinton Hypertonic Electrolytes"
    assert p["reorder_items"][1]["qty"] == 3
    assert not any(e["name"] in (INFO, ED7) for e in p["schedule"]["entries"])
    # The hand-off seed Rae publishes.
    s = now["seed"]
    assert [L["remedy"] for L in s["layers"]] == [
        "Liver Support", "", PYRITE, QUINTON, "Microbiome"]
    assert [L["title"] for L in s["layers"]] == ["Lens", "Renal", "Blood", "Blood", "Gut"]
    assert _slugs(s["reorder_items"]) == ["liver-support", "terrain-restore", "microbiome"]
    # The invoice, both routes.
    for key in ("invoice_lines", "handoff_lines"):
        assert [l["slug"] for l in now[key]] == [
            "biofield-analysis", "liver-support", "terrain-restore", "microbiome"], key


def test_formulations_off_leave_only_the_infoceuticals_and_fold_nothing(rich):
    c = rich
    before = _everything(c)
    _toggle(c, "biofield", cs.FORMULATIONS, False)
    now = _everything(c)
    for key in ("html", "pdf_html", "portal_pdf"):
        listed = _outside_narrative(now[key])
        for name in ("Liver Support", "Pyrite", "Quinton", "Microbiome", "Terrain Restore"):
            assert name not in listed, (key, name)
        assert INFO in listed and ED7 in listed, key
        assert _narrative_div(now[key]) == _narrative_div(before[key]), key
        for n, head in ((1, "Lens"), (3, "Blood")):
            assert (f"<td class=lyr rowspan=1>{n}</td><td rowspan=1>{head}</td>"
                    f"<td rowspan=1>{head}</td><td></td><td></td><td></td><td></td>") in listed
    p = now["portal"]
    assert [L["remedy"] for L in p["layers"]] == ["", INFO, "", ED7]
    assert [L["meaning"] for L in p["layers"]] == [L["meaning"] for L in before["portal"]["layers"]]
    assert _slugs(p["reorder_items"]) == ["ed12", "ed7"]
    assert _slugs(now["seed"]["reorder_items"]) == ["ed12", "ed7"]
    assert [L["remedy"] for L in now["seed"]["layers"]] == ["", INFO, "", ED7]
    for key in ("invoice_lines", "handoff_lines"):
        assert [l["slug"] for l in now[key]] == ["biofield-analysis", "ed12", "ed7"], key


def test_terrain_restore_folds_what_remains_when_one_ingredient_is_off():
    """One Terrain Restore holding an infoceutical and an essence: with
    Infoceuticals off, the bottle still folds, holding the essence alone."""
    from dashboard.biofield_invoice import fold_terrain_restore
    rows = [{"head": "Blood", "remedy": PYRITE}, {"head": "Blood", "remedy": "ED3 Infoceutical in Terrain Restore"}]
    kept = rs.primary_rows(rows, {cs.FORMULATIONS})
    assert kept == rows[:1]
    items = [{"name": r["remedy"], "slug": "pyrite-gem-elixir-in-terrain-restore"} for r in kept]
    folded = fold_terrain_restore(items, True)
    assert [i["slug"] for i in folded] == ["terrain-restore"]
    assert folded[0]["note"] == "With Pyrite Gem Elixir"


def test_the_schedule_keeps_every_remaining_entry_as_it_was(rich):
    from dashboard.biofield_authoring import authored_report
    with sqlite3.connect(rich.db) as con:
        rep = authored_report(con, rich.tid)
    off = rs.primary_report(rep, {cs.FORMULATIONS})
    assert off is not rep and rep["layers"][1]["remedy"] == INFO   # the original is untouched
    by_name = {e["name"]: e for e in rep["schedule"]["entries"]}
    assert [e["name"] for e in off["schedule"]["entries"]] == [
        "Liver Support", "Microbiome", "Terrain Restore"]
    for e in off["schedule"]["entries"]:
        assert e == by_name[e["name"]]


def test_one_function_classifies_for_all_three(monkeypatch):
    """The invoice, the printed report and the portal all ask layer_remedy_on."""
    seen = []
    real = rs.layer_remedy_on

    def spy(name, on):
        seen.append(name)
        return real(name, on)
    monkeypatch.setattr(rs, "layer_remedy_on", spy)
    rs.invoice_remedies([{"name": "Liver Support"}], {cs.FORMULATIONS})
    rs.primary_rows([{"head": "", "remedy": INFO}], {cs.FORMULATIONS})
    assert set(seen) == {"Liver Support", INFO}


def test_life_stress_essences_follow_the_switch(monkeypatch):
    from dashboard import life_stress as ls
    from dashboard import life_stress_curation as lsc
    from dashboard.biofield_report_present import render_present
    monkeypatch.setenv("LIFE_STRESS_ENABLED", "1")
    monkeypatch.setattr(ls, "_load_json", lambda path: {"products": {}})
    monkeypatch.setattr(lsc, "apply_data", lambda cur, block, products: {
        "curated": True, "items": [{"name": "Oak Flower Essence", "description": "Calm."}]})
    rep = {"client": {"name": "J"}, "layers": [], "schedule": {"slots": [], "entries": []},
           "life_stress_curation": {"slugs": ["oak"]}}
    assert "Oak Flower Essence" in render_present(rep, "", primary_on=rs.DEFAULTS["biofield"])
    assert render_present(rep, "", primary_on=rs.DEFAULTS["biofield"]) == render_present(rep, "")
    assert "Life Stress" not in render_present(rep, "", primary_on={cs.INFOCEUTICALS})


def test_a_switched_off_remedy_is_not_called_in_the_program(cx):
    """With Infoceuticals off in the Biofield Analysis, an infoceutical on the layers
    is not in the program, so Remedy Sources does not say it is (2026-10-19)."""
    from dashboard.biofield_clinical_proposals import save_selection
    save_selection(cx, "a49", "Gut", ["Microbiome", INFO])
    marks = {"recommended": {"ED12"}, "purple": set()}
    names = {"ED12": INFO}
    cx.execute("CREATE TABLE IF NOT EXISTS biofield_auth_chain (test_id INT, remedy TEXT)")
    cx.executemany("INSERT INTO biofield_auth_chain VALUES (49, ?)", [("Microbiome",), (INFO,)])
    on = rs.settings(cx, "a49")["remedy_sources"]
    assert rs.render_report(_ctx(cx, marks=marks, names=names), "remedy_sources", on
                            ).count(rs.IN_PROGRAM_NOTE) == 2
    rs.set_section(cx, "a49", rs.PRIMARY, cs.INFOCEUTICALS, False)
    page = rs.render_report(_ctx(cx, marks=marks, names=names), "remedy_sources", on)
    assert page.count(rs.IN_PROGRAM_NOTE) == 1          # Microbiome only
    rs.set_section(cx, "a49", rs.PRIMARY, cs.FORMULATIONS, False)
    page = rs.render_report(_ctx(cx, marks=marks, names=names), "remedy_sources", on)
    assert rs.IN_PROGRAM_NOTE not in page
