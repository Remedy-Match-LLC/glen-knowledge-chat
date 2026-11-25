"""Home shows an unpaid invoice and an unopened report first (Glen, 2026-11-24 and 25).

Linda Maher's FileMaker invoice 17513 reached her portal as order 324. Glen then asked
that an open invoice be the first thing a member sees on Home, that it also show under
My Remedies, and that a report the member has not opened shows right under it as a
link that opens the report itself.
"""
import json
import re
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PORTAL = (ROOT / "static" / "client-portal.html").read_text(encoding="utf-8")
node = pytest.mark.skipif(not shutil.which("node"), reason="node not available")


def _run(js):
    out = subprocess.run(["node", "-e", textwrap.dedent(js)], cwd=str(ROOT),
                         capture_output=True, text=True)
    assert out.returncode == 0, out.stderr + out.stdout
    return out.stdout


JOURNEY = {"phases": [{"key": "heal", "title": "Accelerate healing",
                       "steps": [{"key": "light", "label": "Light", "done": False, "href": "#"}]}]}


@node
def test_invoice_and_new_report_come_before_the_journey():
    view = {"journey": JOURNEY,
            "unpaid_invoice": {"amount_dollars": "1546.00", "link": "https://x/invoice/t"},
            "new_report": {"when": "23 November 2026", "href": "#report-new"}}
    html = json.loads(_run(f"""
      const shell = require('./static/js/portal-shell.js');
      console.log(JSON.stringify(shell.renderHome({json.dumps(view)})));
    """))
    inv = html.index('data-home-alert="invoice"')
    rep = html.index('data-home-alert="report"')
    assert inv < rep < html.index('class="hub-banner"')
    assert '<a href="https://x/invoice/t">Unpaid invoice, $1546.00.</a>' in html
    assert '<a href="#report-new">Your new report, 23 November 2026.</a>' in html
    assert html.count("Unpaid invoice") == 1


@node
def test_alerts_show_even_with_no_journey():
    view = {"unpaid_invoice": {"amount_dollars": "10.00", "link": "https://x/i"},
            "new_report": {"when": "1 May 2026", "href": "#report-new"}}
    html = json.loads(_run(f"""
      const shell = require('./static/js/portal-shell.js');
      console.log(JSON.stringify(shell.renderHome({json.dumps(view)})));
    """))
    assert 'data-home-alert="invoice"' in html and 'data-home-alert="report"' in html


@node
def test_no_alerts_without_data_and_text_is_escaped():
    html = json.loads(_run(f"""
      const shell = require('./static/js/portal-shell.js');
      console.log(JSON.stringify(shell.renderHome({{journey: {json.dumps(JOURNEY)},
        new_report: {{when: '<b>x</b>', href: '#report-new'}}}})));
    """))
    assert "home-alert\" data-home-alert=\"invoice\"" not in html
    assert "<b>x</b>" not in html and "&lt;b&gt;x&lt;/b&gt;" in html
    empty = json.loads(_run(f"""
      const shell = require('./static/js/portal-shell.js');
      console.log(JSON.stringify(shell.renderHome({{journey: {json.dumps(JOURNEY)}}})));
    """))
    assert "home-alert" not in empty


def _fn(name):
    m = re.search(r"^function " + name + r"\(.*?^}", PORTAL, re.S | re.M)
    assert m, name
    return m.group(0)


@node
@pytest.mark.parametrize("case,expect", [
    ({"d": {"opens": {}, "scan_date": "2026-11-23", "layers": [1], "biofield_status": "confirmed"}},
     "#report-new"),
    ({"d": {"opens": {"2026-11-23": "t"}, "scan_date": "2026-11-23", "layers": [1]}}, None),
    ({"d": {"scan_date": "2026-11-23", "layers": [1]}}, None),            # read receipts off
    ({"d": {"opens": {}, "scan_date": "2026-11-23", "layers": [1]}, "search": "?member=a@b.c"}, None),
    ({"d": {"opens": {}, "scan_date": "2026-11-23", "layers": [1], "biofield_status": "ai_draft"}}, None),
    ({"d": {"opens": {}, "scan_date": "2026-11-23", "layers": [1], "biofield_status": "pending"}}, None),
    ({"d": {"opens": {}, "scan_date": "2026-11-23", "layers": []}}, None),  # no report shown
    ({"d": {"opens": {}, "scan_date": "2026-11-23"},
      "v": {"biofield": {"visible": True, "status": "confirmed"}}}, "#report-new"),
    ({"d": {"opens": {}, "scan_date": "2026-11-23"},
      "v": {"biofield": {"visible": True, "status": "interested"}}}, None),
])
def test_home_new_report_only_for_an_unopened_confirmed_own_report(case, expect):
    js = f"""
      globalThis.location = {{search: {json.dumps(case.get("search", ""))}}};
      {_fn("homeDayFmt")}
      {_fn("homeNewReport")}
      const r = homeNewReport({json.dumps(case["d"])}, {json.dumps(case.get("v"))});
      console.log(JSON.stringify(r));
    """
    r = json.loads(_run(js))
    if expect is None:
        assert r is None
    else:
        assert r["href"] == expect
        assert "2026" in r["when"] and "23" in r["when"]   # UTC: never shifts to the 22nd


def test_report_new_route_expands_the_report_and_keeps_its_own_hash():
    assert ('"report-new": {panel:"current", target:"biofield-section", latest:true, '
            'expandReport:true}') in PORTAL
    assert "latestUrl.hash = key;" in PORTAL
    assert 'latestUrl.hash = "biofield-latest";' not in PORTAL
    block = PORTAL[PORTAL.index("if(followed && route.expandReport){"):][:200]
    assert 'document.querySelector(".rr-header")' in block and "rr.click()" in block
    assert 'rr.dataset.opened !== "1"' in block


def test_remedies_panel_shows_the_unpaid_invoice_cards_first():
    fn = _fn("buildRemediesHtml")
    assert fn.startswith("function buildRemediesHtml(v, d){")
    assert "unpaidInvoiceCardsHtml(d && d.invoices)" in fn
    assert "return invoiceCards + `<div class=\"card\" data-fold-id=\"remedies-top-recommended\">" in fn
    calls = re.findall(r"buildRemediesHtml\([^)]*\)", PORTAL.replace(fn, ""))
    assert calls and set(calls) == {"buildRemediesHtml(v, d)"}, calls
    assert "unpaidInvoiceCardsHtml" not in _fn("buildOrdersHtml")


@node
def test_invoice_cards_render_and_escape():
    js = f"""
      function esc(s){{return String(s==null?'':s).replace(/[&<>"']/g,c=>({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[c]));}}
      function foldSlug(s){{return String(s||'').toLowerCase().replace(/[^a-z0-9]+/g,'-');}}
      function invoiceTrackingHtml(){{return '';}}
      {_fn("unpaidInvoiceCardsHtml")}
      console.log(JSON.stringify([unpaidInvoiceCardsHtml([{{amount_dollars:'1546.00', link:'https://x/i?a=1&b=2'}}]),
                                  unpaidInvoiceCardsHtml(undefined)]));
    """
    one, none = json.loads(_run(js))
    assert "Amount due: <strong>$1546.00</strong>" in one
    assert 'href="https://x/i?a=1&amp;b=2"' in one and 'data-fold-id="remedies-invoice-' in one
    assert none == ""
