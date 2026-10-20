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


ROUTES_HTML = (f"{CHAT_A}Let us know</a> about anything below in your "
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
    assert page.index("Count every source.") < at
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
    # Every chat link is one of the sentences above: 2 item, 1 caution, 2 in the lead,
    # 1 oils header, 3 in the routes line and 1 in symptom_changes.
    assert page.count(CHAT_A) == 10


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
    assert f'{CHAT_A}&lt;b&gt;</a>{CHAT_A}portal chat</a>&lt;/b&gt;' in page


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
    # 1 symptoms, 2 lead, 3 routes, 1 item.
    assert html.count(CHAT_A) == 7 and html.count(f'<a href="{INTAKE}">') == 1
    with_plan = pr.append_to_report("<html><body>R</body></html>", _page(cx))
    assert with_plan.count(CHAT_A) == 7


def test_the_rendered_pdf_has_clickable_links(cx, tmp_path):
    """Chromium writes each anchor as a link annotation. Runs where Playwright is."""
    pytest.importorskip("playwright.sync_api")
    from dashboard.biofield_report_pdf import report_pdf_bytes
    try:
        data = report_pdf_bytes(_pdf_page(cx, tmp_path))
    except Exception as exc:  # no browser installed for Playwright here
        pytest.skip(f"no Chromium: {exc!r}")
    assert b"/URI (https://illtowell.com/portal/me#chat)" in data
    assert b"/URI (https://illtowell.com/portal/me#intake)" in data
