"""Plan reports say "let us know" and show where (Glen, "approve", clinical tab,
2026-10-20; clinical/biofield-sections/plan-report/frame-lines.json).

- cautions_routes prints once, straight after cautions_lead, inside the cautions area,
  in every Plan report, with "portal chat" and "Intake form" linked.
- symptom_changes prints once near the top, before the first plan item and outside
  the cautions area, with "portal chat" linked. cautions_lead links "portal chat" too.
- "let us know" in every approved sentence (frame lines, cautions, item explanations)
  links to the portal chat. Never in a name, a condition, a heading or an intro.
- The links come from each line's `links` map in clinical's file, through the loader:
  one source. Text is escaped before any link goes in. No per-client token.
"""
import json
import re
import sqlite3

import pytest

from dashboard import biofield_clinical_sections as cs
from dashboard import biofield_plan_report as pr
from tests.test_biofield_plan_frame import _folder, _item, _remedy, _tick

CHAT = "https://illtowell.com/portal/me#chat"
INTAKE = "https://illtowell.com/portal/me#intake"
CHAT_A = f'<a href="{CHAT}">'
LEAD = ("If you experience intense symptoms like vertigo, please let us know right away "
        "by portal chat, email, and even follow with a phone call if there is urgency.")
ROUTES = ("Let us know about anything below in your portal chat or directly in your Intake "
          "form. Use the portal chat for anything you need to tell us right away.")
ROUTES_LINKS = {"portal chat": CHAT, "Intake form": INTAKE,
                "let us know (every caution and item sentence)": CHAT}
SYMPTOMS = ("Please report any change in your symptoms in your portal chat. That includes "
            "an improvement or the return of an old symptom.")
SYMPTOMS_P = "<p class=plan-symptoms>"
BOX = "<div class=plan-cautions>"
ROUTES_P = "<p class=plan-caution-routes>"


@pytest.fixture
def cx(tmp_path):
    con = sqlite3.connect(str(tmp_path / "chat_log.db"))
    yield con
    con.close()


def _lines(lead=True, routes=True, symptoms=True, **extra):
    out = {}
    if symptoms:
        out["symptom_changes"] = {"text": SYMPTOMS, "approved": True,
                                  "links": {"portal chat": CHAT}}
    if lead:
        out["cautions_lead"] = {"text": LEAD, "approved": True,
                                "links": {"portal chat": CHAT}}
    if routes:
        out["cautions_routes"] = {"text": ROUTES, "approved": True, "links": ROUTES_LINKS}
    out.update({k: {"text": v, "approved": True} for k, v in extra.items()})
    return out


def _page(cx, test_id="a49"):
    return pr.build(cx, test_id, "Josie <Doe>", "20 October 2026")[0]


def _explained(cx, label, section, name, explanation):
    cs.upsert_item(cx, label, section, _item(name, explanation=explanation,
                                             explanation_approved=True), origin_test="a49")
    cs.save_pick(cx, "a49", label, section, name, checked=True)


# cautions_routes names its own two links; its "Let us know" stays plain.
ROUTES_HTML = ("Let us know about anything below in your "
               f'<a href="{CHAT}">portal chat</a> or directly in your '
               f'<a href="{INTAKE}">Intake form</a>. Use the '
               f'<a href="{CHAT}">portal chat</a> for anything you need to tell us right away.')


# ── cautions_routes: placement ──────────────────────────────────────────────

def test_the_loader_carries_the_new_lines_and_their_links(cx, tmp_path):
    report = pr.load_content(cx, _folder(tmp_path, _lines()))
    assert report["frame_lines_unknown"] == []
    assert report["frame_lines_applied"] == 3
    assert report["frame_links_refused"] == [] and report["frame_links_unplaced"] == []
    frame = pr.stored_frame(cx)[0]
    assert frame["cautions_routes"] == ROUTES and frame["symptom_changes"] == SYMPTOMS
    assert frame[pr.LINKS_KEY] == {
        "lines": {"cautions_routes": {"portal chat": CHAT, "Intake form": INTAKE},
                  "cautions_lead": {"portal chat": CHAT},
                  "symptom_changes": {"portal chat": CHAT}},
        "sentences": {"let us know": CHAT}}


def test_a_bad_link_is_refused_and_a_missing_phrase_reported(cx, tmp_path):
    lines = _lines()
    lines["cautions_routes"]["links"] = {"Intake form": "http://x.example/",
                                         "portal chat": "javascript:alert(1)",
                                         "not in the text": CHAT}
    report = pr.load_content(cx, _folder(tmp_path, lines))
    assert report["frame_links_refused"] == [
        "cautions_routes: Intake form: http://x.example/",
        "cautions_routes: portal chat: javascript:alert(1)"]
    assert report["frame_links_unplaced"] == ["cautions_routes: not in the text"]
    _remedy(cx, "Blood sugar", cs.FORMULATIONS, "Glucose Tolerance")
    page = _page(cx)
    assert "x.example" not in page and "javascript:" not in page


def test_with_no_links_map_nothing_is_linked(cx, tmp_path):
    """The file is the one source: no map, no links, and the text prints plain."""
    lines = _lines()
    for entry in lines.values():
        entry.pop("links", None)
    pr.load_content(cx, _folder(tmp_path, lines))
    _explained(cx, "Gut", cs.BOTANICALS, "Slippery Elm", "Let us know first.")
    page = _page(cx)
    assert "<a " not in page
    assert ROUTES in page and "Let us know first." in page


# ── symptom_changes: placement ──────────────────────────────────────────────

def test_symptoms_print_once_before_the_first_item_outside_the_cautions(cx, tmp_path):
    pr.load_content(cx, _folder(tmp_path, _lines(dose_totals="Count every source.")))
    _remedy(cx, "Blood sugar", cs.FORMULATIONS, "Glucose Tolerance")
    page = _page(cx)
    assert page.count(SYMPTOMS_P) == 1
    at = page.index(SYMPTOMS_P)
    assert at < page.index("<section class=plan-heading>")
    assert at < page.index("<span class=plan-name>")
    box = page.index(BOX)
    box_end = page.index("</div>", box)
    assert not (box < at < box_end), "symptom_changes must sit outside the cautions area"
    # It heads the block: before the other top lines too.
    assert at < page.index("Count every source.")
    assert (SYMPTOMS_P + f"Please report any change in your symptoms in your "
            f"{CHAT_A}portal chat</a>. That includes") in page


def test_symptoms_print_with_no_plan_item_too(cx, tmp_path):
    pr.load_content(cx, _folder(tmp_path, _lines()))
    assert _page(cx).count(SYMPTOMS_P) == 1


def test_unapproved_symptoms_never_print(cx, tmp_path):
    lines = _lines()
    lines["symptom_changes"]["approved"] = False
    pr.load_content(cx, _folder(tmp_path, lines))
    assert SYMPTOMS_P not in _page(cx) and "Please report" not in _page(cx)


@pytest.mark.parametrize("cautions", [None, {"Berberine": {
    "text": "It lowers blood sugar, so let us know first if you take diabetes medicine.",
    "approved": True, "items": [{"section": cs.FORMULATIONS, "name": "Glucose Tolerance"}]}}])
def test_routes_print_once_right_after_the_lead_inside_the_box(cx, tmp_path, cautions):
    pr.load_content(cx, _folder(tmp_path, _lines(ingredient_caution_intro=
                                                 "Each caution is listed once below."),
                                cautions=cautions))
    _remedy(cx, "Blood sugar", cs.FORMULATIONS, "Glucose Tolerance")
    page = _page(cx)
    assert page.count(ROUTES_P) == 1
    assert page.count(BOX) == 1
    lead_at = page.index("<p class=plan-caution-lead>")
    lead_end = page.index("</p>", lead_at) + len("</p>")
    # Straight after the lead, before anything else, inside the box.
    assert page.index(BOX) < lead_at
    assert page[lead_end:].startswith(ROUTES_P + ROUTES_HTML + "</p>")
    if cautions:
        assert lead_end < page.index("<p class=plan-caution-intro>")


def test_routes_print_with_no_ingredient_caution(cx, tmp_path):
    pr.load_content(cx, _folder(tmp_path, _lines()))
    _remedy(cx, "Blood sugar", cs.FORMULATIONS, "Glucose Tolerance")
    page = _page(cx)
    assert page.count(ROUTES_P) == 1
    assert BOX + "<p class=plan-caution-lead>" in page


def test_unapproved_routes_never_print(cx, tmp_path):
    lines = _lines()
    lines["cautions_routes"]["approved"] = False
    pr.load_content(cx, _folder(tmp_path, lines))
    _remedy(cx, "Blood sugar", cs.FORMULATIONS, "Glucose Tolerance")
    assert ROUTES_P not in _page(cx)


def test_routes_print_in_every_stand_alone_report(cx):
    """The split reports and the block added to the Biofield Analysis all render
    through render_plan_section, so each gets the line once."""
    from dashboard import biofield_report_split as rsplit
    frame = {"cautions_lead": LEAD, "cautions_routes": ROUTES, "symptom_changes": SYMPTOMS,
             pr.LINKS_KEY: {"lines": {"symptom_changes": {"portal chat": CHAT}},
                            "sentences": {}}}
    for report in rsplit.REPORT_TITLES:
        ctx = {"groups": {}, "client_name": "J", "date": "", "meanings": {}, "intro": (),
               "cautions": [], "frame": frame, "chain": [], "remedy_source_infoceuticals": []}
        body = rsplit.render_report(ctx, report, set())
        assert body.count(ROUTES_P) == 1, report
        assert body.index("<p class=plan-caution-lead>") < body.index(ROUTES_P)
        assert body.count(SYMPTOMS_P) == 1, report
        assert body.index(SYMPTOMS_P) < body.index(BOX)
        assert f"{CHAT_A}portal chat</a>. That" in body


# ── "let us know" links ─────────────────────────────────────────────────────

def test_let_us_know_links_in_caution_and_item_sentences_and_nowhere_else(cx, tmp_path):
    cautions = {"Let us know Root": {
        "text": "It can raise thyroid hormone, so let us know if you take thyroid medicine.",
        "approved": True, "items": [{"section": cs.BOTANICALS, "name": "Let Us Know Herb"}]}}
    pr.load_content(cx, _folder(tmp_path, _lines(
        essential_oils_header="Some oils can tighten the airways, so let us know first.",
        ingredient_caution_intro="Each caution is listed once below."), cautions=cautions))
    _explained(cx, "Let us know clinic", cs.BOTANICALS, "Let Us Know Herb",
               "Let us know first if you are pregnant. LET US KNOW again.")
    _explained(cx, "Vertigo", cs.ESSENTIAL_OILS, "Lavender", "Calming; let us knowing is not it.")
    page = _page(cx)
    # The item sentence: both, any case.
    assert (f'<p class=plan-why>{CHAT_A}Let us know</a> first if you are pregnant. '
            f'{CHAT_A}LET US KNOW</a> again.</p>') in page
    # The caution sentence; its name stays plain.
    assert f"so {CHAT_A}let us know</a> if you take thyroid medicine." in page
    assert "<span class=plan-caution-name>Let us know Root</span>" in page
    # The frame lines Glen approved in the same batch, the lead among them, with the
    # lead's own "portal chat" link.
    assert (f"please {CHAT_A}let us know</a> right away by {CHAT_A}portal chat</a>, "
            "email") in page
    assert f"airways, so {CHAT_A}let us know</a> first." in page
    # Not a whole phrase, not linked.
    assert "let us knowing is not it." in page
    # Names, conditions and the items under a caution stay plain.
    assert "<span class=plan-name>Let Us Know Herb</span>" in page
    assert "Let us know clinic" in page and f"{CHAT_A}Let us know</a> clinic" not in page
    assert f"{CHAT_A}Let Us Know</a> Herb" not in page
    # cautions_routes keeps its "Let us know" plain.
    assert ROUTES_P + "Let us know about" in page
    # Every chat link is one of the sentences above: 2 item, 1 caution, 2 in the lead,
    # 1 oils header, 2 in the routes line and 1 in symptom_changes.
    assert page.count(CHAT_A) == 9


def test_an_intro_line_is_not_linked():
    out = pr.render_plan_section({}, intro=["Let us know what you think."], frame={})
    assert "<a " not in out


def test_a_hostile_sentence_stays_escaped(cx, tmp_path):
    evil = ('<script>alert(1)</script> let us know <a href="https://evil.example">x</a> '
            '& "quoted" let us know')
    cautions = {"<b>Bad</b>": {"text": evil, "approved": True,
                               "items": [{"section": cs.FORMULATIONS, "name": "Glucose Tolerance"}]}}
    pr.load_content(cx, _folder(tmp_path, {
        "cautions_lead": {"text": evil, "approved": True, "links": {"portal chat": CHAT}},
        "symptom_changes": {"text": evil + " <b>portal chat</b>", "approved": True,
                            "links": {"portal chat": CHAT, "<b>": CHAT}},
        "cautions_routes": {"text": evil + " portal chat <i>Intake form</i>", "approved": True,
                            "links": ROUTES_LINKS}},
        cautions=cautions))
    _remedy(cx, "Blood sugar", cs.FORMULATIONS, "Glucose Tolerance")
    _explained(cx, "Gut", cs.BOTANICALS, "Slippery Elm", evil)
    page = _page(cx)
    assert "<script>" not in page and "<b>Bad</b>" not in page and "<i>" not in page
    assert 'href="https://evil.example"' not in page
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page
    # Every anchor in the page goes to one of the two portal routes.
    hrefs = set(re.findall(r'<a href="([^"]*)"', page))
    assert hrefs == {CHAT, INTAKE}
    assert f"&lt;i&gt;<a href=\"{INTAKE}\">Intake form</a>&lt;/i&gt;" in page
    # A phrase holding markup is matched only in its escaped form, and stays text.
    # Here "<b>" touches a word character, so it is not a whole phrase and stays plain.
    assert f'&lt;b&gt;{CHAT_A}portal chat</a>&lt;/b&gt;' in page


def test_no_link_carries_a_client_token(cx, tmp_path):
    pr.load_content(cx, _folder(tmp_path, _lines()))
    _explained(cx, "Gut", cs.BOTANICALS, "Slippery Elm", "Let us know first.")
    hrefs = re.findall(r'<a href="([^"]*)"', _page(cx))
    assert hrefs and set(hrefs) <= {CHAT, INTAKE}


# ── The PDF ─────────────────────────────────────────────────────────────────

def _pdf_page(cx, tmp_path):
    pr.load_content(cx, _folder(tmp_path, _lines()))
    _explained(cx, "Gut", cs.BOTANICALS, "Slippery Elm", "Let us know first.")
    return pr.plan_page(_page(cx))


def test_the_pdf_html_keeps_the_anchors(cx, tmp_path, monkeypatch):
    """/test/<id>/plan.pdf hands plan_page(section) to save_report_pdf: the anchors
    must be in that html, absolute, so a PDF reader can open them."""
    html = _pdf_page(cx, tmp_path)
    # 1 symptoms, 2 lead, 2 routes, 1 item.
    assert html.count(CHAT_A) == 6 and html.count(f'<a href="{INTAKE}">') == 1
    with_plan = pr.append_to_report("<html><body>R</body></html>", _page(cx))
    assert with_plan.count(CHAT_A) == 6


def _chromium_available():
    """True only when Playwright AND its Chromium binary are installed. Checked
    explicitly, so any other failure in making the PDF fails the test."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return False
    import os
    with sync_playwright() as p:
        path = p.chromium.executable_path
    return bool(path) and os.path.exists(path)


def test_the_rendered_pdf_has_clickable_links(cx, tmp_path):
    """Chromium writes each anchor as a link annotation."""
    if not _chromium_available():
        pytest.skip("Playwright's Chromium is not installed here")
    from dashboard.biofield_report_pdf import report_pdf_bytes
    data = report_pdf_bytes(_pdf_page(cx, tmp_path))
    assert b"/URI (https://illtowell.com/portal/me#chat)" in data
    assert b"/URI (https://illtowell.com/portal/me#intake)" in data


# ── Review rounds 1 and 2 (2026-10-20) ──────────────────────────────────────

def test_a_phrase_links_only_as_a_whole_word():
    links = {"portal chat": CHAT}
    out = pr.sentence_html("portal chatroom, xportal chat, portal chat.", links,
                           {"let us know": CHAT})
    assert out == f"portal chatroom, xportal chat, {CHAT_A}portal chat</a>."
    out = pr.sentence_html("let us knowledge, let us know_x, let us know!", None,
                           {"let us know": CHAT})
    assert out == f"let us knowledge, let us know_x, {CHAT_A}let us know</a>!"


def test_two_lines_giving_one_phrase_two_addresses_is_a_conflict(cx, tmp_path):
    lines = _lines(essential_oils_header="Oils; let us know.")
    lines["essential_oils_header"]["links"] = {
        "let us know (every caution and item sentence)": "https://x.example/other",
        "portal chat": "https://x.example/chat2"}
    lines["essential_oils_header"]["text"] = "Oils; let us know in your portal chat."
    report = pr.load_content(cx, _folder(tmp_path, lines))
    assert sorted(report["frame_links_conflict"]) == sorted([
        f"let us know: cautions_routes {CHAT} / essential_oils_header https://x.example/other",
        f"portal chat: symptom_changes {CHAT} / essential_oils_header https://x.example/chat2"])
    # The first sentence-wide address stands: nothing was overwritten.
    assert pr.stored_frame(cx)[0][pr.LINKS_KEY]["sentences"] == {"let us know": CHAT}


def test_a_bracketed_key_other_than_clinicals_form_is_refused(cx, tmp_path):
    lines = _lines()
    lines["cautions_routes"]["links"] = dict(ROUTES_LINKS, **{"portal chat (anywhere)": CHAT})
    report = pr.load_content(cx, _folder(tmp_path, lines))
    assert report["frame_links_refused"] == [f"cautions_routes: portal chat (anywhere): {CHAT}"]
    assert pr.stored_frame(cx)[0][pr.LINKS_KEY]["sentences"] == {"let us know": CHAT}


ONCE_P = (SYMPTOMS_P, "<p class=plan-caution-lead>", ROUTES_P)


def _ctx(frame, groups=None):
    return {"groups": groups or {}, "client_name": "J", "date": "", "meanings": {},
            "intro": (), "cautions": [], "frame": frame, "chain": [], "program_chain": [],
            "remedy_source_infoceuticals": [], "report_leads": {}, "report_personal": {}}


FRAME = {"cautions_lead": LEAD, "cautions_routes": ROUTES, "symptom_changes": SYMPTOMS,
         pr.LINKS_KEY: {"lines": {"symptom_changes": {"portal chat": CHAT}}, "sentences": {}}}


def test_a_grouped_pdf_prints_each_once_line_once_in_the_first_report():
    from dashboard import biofield_report_split as rsplit
    reports = list(rsplit.STAND_ALONE)
    doc = rsplit.grouped_html(_ctx(FRAME), reports, {r: set() for r in reports})
    for p in ONCE_P:
        assert doc.count(p) == 1, p
    first_end = doc.index("<div class=rs-report", doc.index("<div class=rs-report") + 1)
    for p in ONCE_P:
        assert doc.index(p) < first_end, p


def test_a_single_report_prints_each_once_line_once():
    from dashboard import biofield_report_split as rsplit
    for report in rsplit.STAND_ALONE:
        for one in (rsplit.grouped_html(_ctx(FRAME), [report], {report: set()}),
                    rsplit.render_report(_ctx(FRAME), report, set())):
            for p in ONCE_P:
                assert one.count(p) == 1, (report, p)


def test_the_block_added_to_the_biofield_analysis_starts_with_symptoms(cx, tmp_path):
    from dashboard import biofield_report_split as rsplit
    pr.load_content(cx, _folder(tmp_path, _lines(dose_totals="Count every source.")))
    _tick(cx, "Gut", cs.NUTRIENTS, "Zinc")
    frame, cautions = pr.stored_frame(cx)
    ctx = _ctx(frame, pr.plan(cx, "a49"))
    ctx["cautions"] = cautions
    once = set()
    block = rsplit.primary_added(ctx, {cs.NUTRIENTS}, once)
    assert block.count(SYMPTOMS_P) == 1
    main = block.index("<main class=plan>") + len("<main class=plan>")
    assert block[main:].startswith(SYMPTOMS_P), "symptom_changes heads the added block"
    assert block.index(SYMPTOMS_P) < block.index("<span class=plan-name>")
    # A Plan appended to the same PDF does not print them again.
    plan = pr.build(cx, "a49", "J", "", printed=once)[0]
    for p in ONCE_P:
        assert p not in plan, p
    # On its own, the Plan prints each once.
    alone = pr.build(cx, "a49", "J", "")[0]
    for p in ONCE_P:
        assert alone.count(p) == 1, p


from tests.test_biofield_plan_report import client  # noqa: E402,F401  (the local app)


ZINC_CAUTION = {"Zinc": {"text": "Zinc adds up, so let us know your total.", "approved": True,
                         "items": [{"section": cs.NUTRIENTS, "name": "Zinc"}]}}
OIL_CAUTION = {"Lavender": {"text": "Lavender can calm too much.", "approved": True,
                            "items": [{"section": cs.ESSENTIAL_OILS, "name": "Lavender"}]}}
LEAD_P, BLOCK = "<p class=plan-caution-lead>", "plan-appended"


def _report_pdf(client, tmp_path, cautions, added=True, plan=True, oil=False):
    from dashboard import biofield_report_split as rsplit
    tid = client.post("/author/new").headers["Location"].rstrip("/").rsplit("/", 1)[-1]
    with sqlite3.connect(client.db) as con:
        pr.load_content(con, _folder(tmp_path, _lines(), cautions=cautions))
        cs.upsert_item(con, "Gut", cs.NUTRIENTS, _item("Zinc"), origin_test=tid)
        cs.save_pick(con, tid, "Gut", cs.NUTRIENTS, "Zinc", checked=True)
        if oil:
            cs.upsert_item(con, "Vertigo", cs.ESSENTIAL_OILS, _item("Lavender"), origin_test=tid)
            cs.save_pick(con, tid, "Vertigo", cs.ESSENTIAL_OILS, "Lavender", checked=True)
        if added:
            rsplit.set_section(con, tid, rsplit.PRIMARY, cs.NUTRIENTS, True)
        con.commit()
    r = client.get(f"/test/{tid}/report.pdf" + ("?plan=1" if plan else ""))
    assert r.status_code == 200
    html = client.written[-1][0]
    blocks = html.split(BLOCK)[1:]       # each appended block, in document order
    for p in ONCE_P:
        assert html.count(p) == 1, p
    return blocks


def _where(blocks, p):
    return [i for i, b in enumerate(blocks) if p in b]


def test_report_and_plan_the_added_block_with_cautions_carries_all_three(client, tmp_path):
    blocks = _report_pdf(client, tmp_path, ZINC_CAUTION)
    assert len(blocks) == 2
    assert _where(blocks, SYMPTOMS_P) == [0]
    assert _where(blocks, LEAD_P) == [0] and _where(blocks, ROUTES_P) == [0]
    assert "Zinc adds up" in blocks[0]


def test_report_and_plan_the_lead_follows_the_cautions_into_the_plan(client, tmp_path):
    """The added block prints Nutrients and no caution; the Plan prints the oil caution."""
    blocks = _report_pdf(client, tmp_path, OIL_CAUTION, oil=True)
    assert _where(blocks, SYMPTOMS_P) == [0], "symptom_changes heads the first plan block"
    assert _where(blocks, LEAD_P) == [1] and _where(blocks, ROUTES_P) == [1]
    plan = blocks[1]
    assert plan.index(LEAD_P) < plan.index(ROUTES_P) < plan.index("Lavender can calm")


def test_report_and_plan_with_no_caution_the_plan_carries_the_lead(client, tmp_path):
    blocks = _report_pdf(client, tmp_path, None)
    assert _where(blocks, SYMPTOMS_P) == [0]
    assert _where(blocks, LEAD_P) == [1] and _where(blocks, ROUTES_P) == [1]


def test_report_and_plan_with_no_added_block_the_plan_carries_all(client, tmp_path):
    blocks = _report_pdf(client, tmp_path, ZINC_CAUTION, added=False)
    assert len(blocks) == 1
    for p in ONCE_P:
        assert _where(blocks, p) == [0], p
    assert blocks[0].index(SYMPTOMS_P) < blocks[0].index("<span class=plan-name>")


@pytest.mark.parametrize("cautions", [None, ZINC_CAUTION])
def test_the_report_alone_its_added_block_carries_all(client, tmp_path, cautions):
    blocks = _report_pdf(client, tmp_path, cautions, plan=False)
    assert len(blocks) == 1
    for p in ONCE_P:
        assert _where(blocks, p) == [0], p


def _grouped(cx, tmp_path, cautions):
    from dashboard import biofield_report_split as rsplit
    pr.load_content(cx, _folder(tmp_path, _lines(), cautions=cautions))
    _tick(cx, "Gut", "foods_emphasize", "Bone broth")
    _tick(cx, "Gut", cs.NUTRIENTS, "Zinc")
    frame, stored = pr.stored_frame(cx)
    ctx = _ctx(frame, pr.plan(cx, "a49"))
    ctx["cautions"] = stored
    doc = rsplit.grouped_html(ctx, ["diet", "ingredients"],
                              {"diet": {"foods_emphasize"}, "ingredients": {cs.NUTRIENTS}})
    parts = doc.split("<div class=rs-report")[1:]
    for p in ONCE_P:
        assert doc.count(p) == 1, p
    return parts


def test_grouped_the_lead_goes_to_the_first_report_with_a_caution(cx, tmp_path):
    parts = _grouped(cx, tmp_path, ZINC_CAUTION)
    assert _where(parts, SYMPTOMS_P) == [0]
    assert _where(parts, LEAD_P) == [1] and _where(parts, ROUTES_P) == [1]
    assert parts[1].index(LEAD_P) < parts[1].index("Zinc adds up")


def test_grouped_with_no_caution_the_first_report_carries_all(cx, tmp_path):
    parts = _grouped(cx, tmp_path, None)
    for p in ONCE_P:
        assert _where(parts, p) == [0], p
