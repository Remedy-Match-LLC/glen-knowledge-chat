"""The E4L panel offers an Import-Reveal button only when a scan is < 7 days old;
the author page defines the importReveal() handler."""
from dashboard.biofield_report_html import render_author_html, render_e4l_panel


def _ctx(found, days):
    return {"status": "fresh" if found else "none", "found": found,
            "scan_id": 900 if found else None, "scan_date": "2026-06-22",
            "days_ago": days, "fresh": True, "window_days": 14,
            "message": "Recent E4L scan", "findings": [],
            "infoceuticals": [], "stresses": []}


def test_button_active_when_scan_under_7_days():
    html = render_e4l_panel(_ctx(True, 3))
    assert "Import Reveal" in html
    assert "onclick=importReveal()" in html
    assert "disabled" not in html.split("Import Reveal")[0][-40:]  # button not disabled


def test_stale_scan_offers_request_email_and_explicit_use_anyway():
    html = render_e4l_panel(_ctx(True, 12))
    assert "12 days old" in html
    assert "Use most recent scan anyway" in html
    assert "useStaleScan(12)" in html
    assert "Email client to request fresh scan" in html
    assert "requestFreshScan()" in html


def test_no_button_when_no_scan():
    html = render_e4l_panel(_ctx(False, None))
    assert "Import Reveal" not in html


def test_author_page_defines_import_reveal_handler():
    rep = {"test_id": "a7", "client": {"name": "Jane", "email": "jane@x.com"},
           "date": "2026-06-25", "layers": [], "schedule": []}
    html = render_author_html(rep, [], "")
    assert "function importReveal(allowStale)" in html
    assert "/author/a7/e4l/import-reveal" in html
    assert "needs_confirm" in html
    assert "allow_stale" in html
    assert "/author/a7/e4l/request-fresh" in html
