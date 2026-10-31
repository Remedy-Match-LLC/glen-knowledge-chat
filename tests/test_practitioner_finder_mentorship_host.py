"""The practitioner finder names no product line on MentorshipU (Glen "retitle the finder",
teaching tab, 2026-10-31). The cataract education page links to it there, and an education
page does not lead to a page that names his product line. Other hosts keep the branding.
"""
import os
import re

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BRANDS = ("Functional Formulations", "Remedy&nbsp;Match", "Remedy Match", "Healing&nbsp;Oasis</span>")


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("MENTORSHIP_BASE_URL", "https://courses.mentorshipu.com")
    import importlib
    import app as a
    importlib.reload(a)
    a.app.config["TESTING"] = True
    return a.app.test_client()


def _page(client, host):
    r = client.get("/practitioner-finder", base_url=f"https://{host}")
    assert r.status_code == 200
    return r.get_data(as_text=True)


def test_mentorshipu_shows_a_plain_finder(client):
    html = _page(client, "courses.mentorshipu.com")
    assert "<title>Find a Practitioner</title>" in html
    assert ("<p>Practitioners across functional eye care, biological dentistry and integrative "
            "health.</p>") in html
    assert not [b for b in BRANDS if b in html]
    # Review round 2: every fixed link is checked, not one URL pattern. Practitioner cards
    # link each practitioner's own site, phone and email from the listing data.
    hrefs = set(re.findall(r'href=\\?"([^"\\]*)', html))
    allowed = re.compile(r"^(?:https://fonts\.(?:googleapis|gstatic)\.com|"
                         r"https://api\.mapbox\.com/|/static/favicon\.png$|/privacy$|/terms$|"
                         r"mailto:|tel:|\$\{escapeHtml\()")
    assert hrefs and not [h for h in hrefs if not allowed.match(h)], hrefs


@pytest.mark.parametrize("host", ["illtowell.com", "myhealingoasis.com"])
def test_other_hosts_keep_the_branding(client, host):
    html = _page(client, host)
    assert "<title>Find a Practitioner · Functional Formulations™</title>" in html
    assert "Remedy&nbsp;Match" in html


def test_the_template_still_holds_every_string_replaced():
    # A silent miss would leave the brand on MentorshipU, so a template edit fails here.
    import app as a
    html = open(os.path.join(ROOT, "static", "practitioner-finder.html"), encoding="utf-8").read()
    for old, _new in a._FINDER_BRANDING:
        assert html.count(old) == 1, old
