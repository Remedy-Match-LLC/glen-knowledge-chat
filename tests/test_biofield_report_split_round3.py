"""Report split, round 3 review (2026-10-19). Each test kills one or more mutations
that survived the original tests (named in its docstring). PDF printing, portal upload
and publishing are stubbed by the borrowed fixtures."""
import hashlib
import json
import pathlib
import smtplib
import sqlite3

import pytest

from dashboard import biofield_clinical_sections as cs
from dashboard import biofield_report_split as rs
from tests.test_biofield_report_split import (  # noqa: F401  (fixtures)
    CATALOG, ED7, INFO, TODAY, _ctx, _frame, _item, _no_gate, _seed, _tick,
    _tick_new_section, _toggle, app_client, cx, rich)


@pytest.fixture(autouse=True)
def _block_smtp(monkeypatch):
    def refuse(*a, **k):
        raise RuntimeError("no mail in tests")
    monkeypatch.setattr(smtplib, "SMTP", refuse)
    monkeypatch.setattr(smtplib, "SMTP_SSL", refuse)


def _app(c, **kw):
    """A second app on the same db as app_client, with some stubs replaced."""
    import biofield_local_app as bla

    def fake_create(customer, lines, replace_open=False, invoice_note=None,
                    update_order_id=None, idempotency_key=""):
        c.calls["lines"] = lines
        return {"ok": True, "order_id": 7, "external_ref": "INH", "total_cents": 1}
    args = dict(invoice_fetch_catalog=lambda: CATALOG, invoice_create=fake_create,
                invoice_link=lambda oid: {"ok": False},
                invoice_paid_check=lambda email: {},
                invoice_latest=lambda email: {"ok": False},
                scan_lookup=lambda email: {}, auto_publish=lambda *a, **k: {})
    args.update(kw)
    return bla.create_app(c.db, **args).test_client()


# ---- Invoice and hand-off -------------------------------------------------------

def test_already_paid_and_everything_off_still_names_what_was_left_off(app_client):
    """Kills A06."""
    c = app_client
    _toggle(c, "biofield", cs.INFOCEUTICALS, False)
    _toggle(c, "biofield", cs.FORMULATIONS, False)
    c2 = _app(c, invoice_paid_check=lambda email: {"paid": True, "order_id": 3})
    j = c2.post(f"/author/{c.tid}/invoice", json={}).get_json()
    assert j["already_paid"] and j["added"] == 0
    assert "Liver Support" in j["warning"] and INFO in j["warning"]


def test_unmatched_catalog_still_names_what_was_left_off(app_client):
    """Kills A16: the hand-off branch that refuses a fee-only invoice when nothing
    matched the catalog still says what the switches left off."""
    c = app_client
    with sqlite3.connect(c.db) as con:
        con.execute("UPDATE biofield_auth_chain SET remedy='Nowhere Tonic' "
                    "WHERE remedy='Liver Support'")
    _toggle(c, "biofield", cs.INFOCEUTICALS, False)
    j = c.post(f"/author/{c.tid}/handoff", json={}).get_json()
    assert j["invoice"]["ok"] is False and "could not be matched" in j["invoice"]["error"]
    assert INFO in j["invoice"]["warning"]


def test_handoff_bills_every_pure_powder_when_a_layer_section_is_off(app_client):
    """Kills A11: the powders slice must count the layer remedies left off."""
    c = app_client
    with sqlite3.connect(c.db) as con:
        _tick_new_section(con, c.tid, "Gut", "pure_powders", "Soothe Pure Powder")
    _toggle(c, "biofield", "pure_powders", True)
    _toggle(c, "biofield", cs.INFOCEUTICALS, False)
    c.post(f"/author/{c.tid}/handoff", json={})
    assert c.calls["lines"] == TODAY[:2] + [
        {"slug": "soothe-pure", "qty": 1, "source": "biofield"}]


def test_a_pure_powder_already_on_a_layer_is_billed_once(app_client):
    """Kills A12 (hand-off) and C07 (invoice_remedies)."""
    c = app_client
    with sqlite3.connect(c.db) as con:
        con.execute("UPDATE biofield_auth_chain SET remedy='Soothe Pure Powder' "
                    "WHERE remedy='Liver Support'")
        _tick_new_section(con, c.tid, "Gut", "pure_powders", "Soothe Pure Powder")
    _toggle(c, "biofield", "pure_powders", True)
    for route in ("handoff", "invoice"):
        c.post(f"/author/{c.tid}/{route}", json={})
        slugs = [l["slug"] for l in c.calls["lines"]]
        assert slugs.count("soothe-pure") == 1, (route, slugs)
    assert rs.invoice_remedies([{"name": "Soothe Pure Powder", "qty": 2}],
                               rs.DEFAULTS["biofield"] | {"pure_powders"},
                               ["Soothe Pure Powder", "soothe pure powder"]) == [
        {"name": "Soothe Pure Powder", "qty": 2}]


def test_handoff_by_bare_number_reads_the_authored_switches(app_client):
    """Kills A14: /author/1/handoff is authored test a1, as /author/1/invoice is."""
    c = app_client
    num = c.tid[1:]
    _toggle(c, "biofield", cs.INFOCEUTICALS, False)
    c.post(f"/author/{num}/handoff", json={})
    assert c.calls["lines"] == TODAY[:2]


def test_a_powder_sharing_a_layer_remedys_product_is_billed_once(app_client):
    """Kills A12: two names for one product, one on a layer and one a Pure Powder."""
    c = app_client
    with sqlite3.connect(c.db) as con:
        con.execute("UPDATE biofield_auth_chain SET remedy='Soothe Pure Powder' "
                    "WHERE remedy='Liver Support'")
        _tick_new_section(con, c.tid, "Gut", "pure_powders", "Soothe Powder")
    _toggle(c, "biofield", "pure_powders", True)
    c2 = _app(c, invoice_fetch_catalog=lambda: CATALOG + [
        {"slug": "soothe-pure", "name": "Soothe Powder"}])
    c2.post(f"/author/{c.tid}/handoff", json={})
    slugs = [l["slug"] for l in c.calls["lines"]]
    assert slugs.count("soothe-pure") == 1, slugs


def test_handoff_by_bare_number_hands_off_the_authored_switches(rich):
    """Kills A14: the portal seed pushed by /author/<n>/handoff follows test a<n>."""
    c = rich
    _toggle(c, "biofield", cs.INFOCEUTICALS, False)
    c.rec["pushed"].clear()
    assert c.post(f"/author/{c.tid[1:]}/handoff", json={}).get_json()["ok"]
    seed = c.rec["pushed"][-1]
    assert INFO not in [L["remedy"] for L in seed["layers"]]
    assert "ed12" not in [i["slug"] for i in seed["reorder_items"]]


# ---- Publish ---------------------------------------------------------------------

def test_publish_uses_the_switches_the_print_read(rich, monkeypatch):
    """Kills B07: a click after the print renders must not reach the portal."""
    import biofield_local_app as bla
    c = rich
    _toggle(c, "biofield", cs.INFOCEUTICALS, False)
    real = bla.save_report_pdf

    def save_then_click(html, out):
        res = real(html, out)
        _toggle(c, "biofield", cs.INFOCEUTICALS, True)      # Glen clicks after the render
        return res
    monkeypatch.setattr(bla, "save_report_pdf", save_then_click)
    assert c.get(f"/test/{c.tid}/report.pdf").status_code == 200
    slugs = [i["slug"] for i in c.rec["portal"][-1]["content"]["reorder_items"]]
    assert "ed12" not in slugs and "ed7" not in slugs


def test_a_switched_off_unsold_remedy_does_not_block_the_publish(rich):
    """Kills B12: the pre-check reads the same filtered content as the publish."""
    c = rich
    with sqlite3.connect(c.db) as con:
        con.execute("UPDATE biofield_auth_chain SET remedy=? WHERE remedy=?",
                    ("ED99 Unsold Energetic Driver Infoceutical", ED7))
    _toggle(c, "biofield", cs.INFOCEUTICALS, False)
    c.rec["portal"].clear()
    assert c.get(f"/test/{c.tid}/report.pdf").status_code == 200
    assert c.rec["portal"], "the publish was blocked by a switched-off remedy"


def test_fulfill_requests_publishes_with_the_filemaker_tests_switches(cx, monkeypatch):
    """Kills B24."""
    import importlib.util
    from dashboard import biofield_portal_publish as bpp
    monkeypatch.setattr(bpp, "authored_report", bpp.authored_report)   # restored after
    root = pathlib.Path(rs.__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location("fulfill_requests_r3",
                                                  root / "scripts" / "fulfill_requests.py")
    fr = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fr)
    seen = {}
    monkeypatch.setattr(fr, "DRY", True)
    monkeypatch.setattr(fr, "latest_test", lambda cx, email: "48")
    monkeypatch.setattr(fr, "client_name", lambda cx, email: "J")
    monkeypatch.setattr(fr.bpp, "build_portal_content",
                        lambda cx, tid, **kw: seen.update(kw) or {"content": {"layers": []}})
    rs.set_section(cx, "48", rs.PRIMARY, cs.INFOCEUTICALS, False)
    fr.fulfill_one(cx, {"email": "j@x.com"})
    assert set(seen["primary_on"]) == {cs.FORMULATIONS}


# ---- The primary report ------------------------------------------------------------

def test_the_lead_sits_directly_under_the_masthead():
    """Kills B16: Glen, 2026-10-19, the introduction is at the top of each report."""
    from tests.test_biofield_report_present import REPORT
    from dashboard.biofield_report_present import render_present
    rep = dict(REPORT, phase="P3", location="Liver")
    html = render_present(rep, narrative="x", intro=("Your lead.",))
    assert html.index("Your lead.") < html.index("Remedy Schedule")
    assert html.index("Kauilani Perdomo") < html.index("Your lead.")


def test_an_empty_introduction_adds_no_markup():
    """Kills B18: empty, the report is byte for byte what it was."""
    from tests.test_biofield_report_present import REPORT
    from dashboard.biofield_report_present import render_present
    assert "report-intro" not in render_present(REPORT, narrative="x", intro=("", "  "))


def test_a_blanked_layer_is_never_scheduled_in_a_filemaker_report():
    """Kills C05: a FileMaker report schedules unnamed rows, but never a row this
    change blanked."""
    from dashboard.biofield_schedule import build_schedule
    layers = [{"layer": 1, "head": "Gut", "remedy": "Microbiome", "rid": 1},
              {"layer": 2, "head": "Renal", "remedy": INFO, "rid": 2},
              {"layer": 3, "head": "Lung", "remedy": "", "rid": 3}]
    sched = build_schedule([{"name": l["remedy"], "source_rids": [l["rid"]]} for l in layers])
    assert any(not e["name"] for e in sched["entries"])          # FileMaker style
    rep = rs.primary_report({"layers": layers, "schedule": sched}, {cs.FORMULATIONS})
    rids = [r for e in rep["schedule"]["entries"] for r in e.get("source_rids") or []]
    assert 2 not in rids


def test_primary_additions_print_only_their_own_lines(cx):
    """Kills E08, E09 and I03: cautions, plan-wide lines and the Amazon line in the
    sections added to the Biofield Analysis come from what it prints."""
    _seed(cx)
    _tick_new_section(cx, "a49", "Gut", "amazon", "Amazon Castor Oil")
    _frame(cx, dose_totals="Count every source.")
    ctx = _ctx(cx)
    ctx["cautions"] = [("Zinc", "Watch zinc.", {(cs.NUTRIENTS, "zinc glycinate")})]
    foods = rs.primary_added(ctx, rs.DEFAULTS["biofield"] | {"foods_emphasize"})
    assert "Bone broth" in foods
    assert "Watch zinc." not in foods and "Count every source." not in foods
    amazon = rs.primary_added(ctx, rs.DEFAULTS["biofield"] | {"amazon"})
    assert rs.AMAZON_DISCLOSURE in amazon


def test_the_primary_buttons_count_the_layer_remedies(cx):
    """Kills J03."""
    ctx = rs.load(cx, "a49", chain=["Liver Support", INFO, ED7])
    n = rs.counts(ctx, rs.PRIMARY)
    assert n[cs.FORMULATIONS] == 1 and n[cs.INFOCEUTICALS] == 2


# ---- Remedy Sources and the scan --------------------------------------------------

def test_a_scan_read_error_refuses_remedy_sources(app_client, monkeypatch):
    """Kills F10: a partial scan read is a scan problem."""
    from dashboard import e4l_scan_marks
    monkeypatch.setattr(e4l_scan_marks, "infoceutical_marks", lambda cx, db, sid: {
        "recommended": set(), "purple": set(), "recommended_error": "table missing"})
    c = _app(app_client, scan_lookup=lambda email: {"found": True, "scan_id": 5})
    r = c.get(f"/test/{app_client.tid}/reports.pdf?mode=singly&r=remedy_sources")
    assert r.status_code == 409 and b"table missing" in r.data


def test_no_email_is_named_to_glen(app_client):
    """Kills F13."""
    c = app_client
    with sqlite3.connect(c.db) as con:
        con.execute("UPDATE biofield_auth_tests SET email=''")
    page = c.get(f"/test/{c.tid}/reports").get_data(as_text=True)
    assert "no email" in page


def test_a_filemaker_test_marks_its_own_layers_in_program(app_client, monkeypatch):
    """Kills F11: a FileMaker report passes its own layers as the chain."""
    import biofield_local_app as bla
    from dashboard.biofield_clinical_proposals import save_selection
    monkeypatch.setattr(bla, "causal_chain_report", lambda cx, t: {
        "client": {"name": "F", "email": ""}, "date": "",
        "layers": [{"head": "Gut", "remedy": "Microbiome"}]})
    with sqlite3.connect(app_client.db) as con:
        save_selection(con, "77", "Gut", ["Microbiome"])
    _toggle_fmp = app_client.post("/test/77/report-sections", json={
        "report": "remedy_sources", "section": cs.INFOCEUTICALS, "on": False})
    assert _toggle_fmp.status_code == 200
    page = app_client.get("/test/77/reports").get_data(as_text=True)
    assert rs.IN_PROGRAM_NOTE in page


# ---- Introductions, links and fixed wording ---------------------------------------

def test_stored_lead_rows_override_the_file_and_keep_their_number_order(cx):
    """Kills H02: a stored row wins over the file."""
    from dashboard import biofield_plan_report as pr
    pr.ensure_schema(cx)
    cx.executemany("INSERT INTO biofield_plan_wording (kind,key,text) VALUES ('report_lead',?,?)",
                   [(f"diet:{i}", f"L{i}") for i in range(1, 11)])
    leads = rs.stored_leads(cx)
    assert leads["diet"][0] == "L1"


def test_stored_lead_rows_keep_their_number_order(cx):
    from dashboard import biofield_plan_report as pr
    pr.ensure_schema(cx)
    cx.executemany("INSERT INTO biofield_plan_wording (kind,key,text) VALUES ('report_lead',?,?)",
                   [(f"diet:{i}", f"L{i}") for i in range(1, 11)])
    assert rs.stored_leads(cx)["diet"] == [f"L{i}" for i in range(1, 11)]


def test_the_lead_prints_before_the_shared_intro(cx, monkeypatch):
    """Kills H04."""
    monkeypatch.setattr(rs, "approved_leads", lambda: ({"diet": ["Food first."]}, {}))
    from dashboard import biofield_plan_report as pr
    _seed(cx)
    pr.ensure_schema(cx)
    cx.execute("INSERT INTO biofield_plan_wording VALUES ('intro','0','Chosen for you.')")
    cx.commit()
    page = rs.render_report(_ctx(cx), "diet", rs.DEFAULTS["diet"])
    assert page.index("Food first.") < page.index("Chosen for you.")


def test_a_link_address_is_escaped():
    """Kills H07."""
    from dashboard.biofield_plan_report import _linked
    out = _linked("Ask the chat.", {"the chat": 'https://x.test/"><script>'})
    assert "<script>" not in out and "&quot;" in out


def test_only_remedy_sources_carries_a_link(cx):
    """Kills H10 when another report's lead holds the phrase."""
    phrase = "the chat interface of Dr. Glen's knowledge-base"
    leads = {r: [f"Ask {phrase}."] for r in rs.STAND_ALONE}
    links = {"remedy_sources": {phrase: "https://myhealingoasis.com/portal/login"}}
    import unittest.mock as um
    with um.patch.object(rs, "approved_leads", lambda: (leads, links)):
        _seed(cx)
        ctx = _ctx(cx)
        for r in ("diet", "healing_tools", "ingredients"):
            assert "<a href" not in rs.render_report(ctx, r, rs.DEFAULTS[r]), r
        assert "<a href" in rs.render_report(ctx, "remedy_sources", rs.DEFAULTS["remedy_sources"])


# The approved leads, frozen. Source: marketing/04 Copy/report-introductions-2026-10-19/
# intros-APPROVED.md (Glen, "approved with edits", 2026-10-19). Round 3 checked the file
# matches it paragraph for paragraph. Any change to the wording must change this digest
# on purpose.


def _digest(leads):
    return hashlib.sha256(json.dumps(leads, sort_keys=True, ensure_ascii=False)
                          .encode()).hexdigest()


def test_the_leads_are_the_approved_wording():
    """Kills H12: the word-for-word test compares the page to the file, so a reworded
    file passed. This pins the file to the approved text."""
    leads, _ = rs.approved_leads()
    assert _digest(leads) == APPROVED_DIGEST
    # Glen, 2026-10-19: "Use each remedy as guided." not 'take'.
    assert leads["biofield"][7] == "Use each remedy as guided."


def test_the_amazon_line_is_the_approved_wording():
    """Kills I02: ruling 7's words, written out, not read from the constant."""
    assert rs.AMAZON_DISCLOSURE == ("Some recommendations include affiliate links. "
                                    "As an Amazon Associate, Healing Oasis earns from "
                                    "qualifying purchases.")


APPROVED_DIGEST = "6740036ce596393cfbbf58727649e891080a07af66bc037e9391d787ba314ec3"


def test_a_list_item_never_splits_across_pages(cx):
    """Kills G08. Note for Glen: the same rule keeps a whole section together where
    it fits, which can leave a gap at a page foot."""
    _seed(cx)
    page = rs.grouped_html(_ctx(cx), ["diet"], {"diet": rs.DEFAULTS["diet"]})
    assert ".plan-list li{break-inside:avoid-page}" in page


def test_the_walkthrough_script_names_only_switched_on_remedies(app_client, monkeypatch):
    """Glen, 2026-10-19, "yes": the spoken walkthrough follows the section switches,
    as the printed report and the invoice do."""
    import biofield_local_app as bla
    seen = []

    def fake_script(rep, notes, complete, scan=None, problems_out=None):
        seen.append([l.get("remedy") for l in rep.get("layers") or []])
        return "Aloha"
    monkeypatch.setattr(bla, "generate_video_script", fake_script)
    c = app_client
    assert c.post(f"/test/{c.tid}/video-generate", json={}).get_json()["script"] == "Aloha"
    assert INFO in seen[-1] and "Liver Support" in seen[-1]
    _toggle(c, "biofield", cs.INFOCEUTICALS, False)
    c.post(f"/test/{c.tid}/video-generate", json={})
    assert INFO not in seen[-1] and "Liver Support" in seen[-1]
