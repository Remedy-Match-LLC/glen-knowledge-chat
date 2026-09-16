"""Glen, 2026-09-16: he was authoring Steve Fox's clinical summary and "all the data
just disappeared". #1696 had merged one minute earlier, production was restarting,
and the Intake app's profile fetch failed. That failure is caught and returns an
empty profile, so the panel rendered with no rows and no explanation.

An empty Clinical summary is indistinguishable from a client with no history. On a
clinical surface that is the wrong thing to show, so it now says so instead. The Fee
card on the same page already did this ("Pricing unavailable").
"""
from dashboard.biofield_report_html import render_clinical_checklist

ROW = {"label": "Cataracts", "checked": False, "covered_by": "", "layer": None,
       "common_remedies": [], "stress_pattern": "", "remembered_pattern": "",
       "pattern_is_suggested": False}


def test_a_genuinely_empty_list_says_nothing_special():
    h = render_clinical_checklist([], [])
    assert "couldn't reach console" not in h


def test_an_unreachable_console_is_stated_not_rendered_as_empty():
    h = render_clinical_checklist([], [], profile_unavailable=True)
    assert "couldn't reach console" in h
    assert "may be incomplete" in h


def test_rows_we_do_have_still_render_under_the_warning():
    """Items added by hand live locally and survive the outage. Hiding them would be
    its own scare, so they stay, under the warning."""
    h = render_clinical_checklist([ROW], [], profile_unavailable=True)
    assert "couldn't reach console" in h
    assert "Cataracts" in h
    assert h.index("couldn't reach console") < h.index("Cataracts")


def test_the_warning_is_absent_when_the_fetch_worked():
    h = render_clinical_checklist([ROW], [])
    assert "couldn't reach console" not in h


def test_the_local_fetcher_returns_none_when_it_cannot_reach_the_console(monkeypatch):
    """The signal the page depends on. {} would be indistinguishable from a client
    with nothing on file."""
    import urllib.request

    import biofield_local_app as loc
    monkeypatch.setenv("CONSOLE_SECRET", "k")

    def boom(*a, **kw):
        raise OSError("connection refused")

    # urllib is imported inside the function, so patch the real module.
    monkeypatch.setattr(urllib.request, "urlopen", boom)
    assert loc._default_fetch_profile("someone@example.com") is None


def test_the_local_fetcher_returns_the_profile_when_it_can(monkeypatch):
    import io
    import json as _j
    import urllib.request

    import biofield_local_app as loc
    monkeypatch.setenv("CONSOLE_SECRET", "k")
    monkeypatch.setattr(urllib.request, "urlopen",
                        lambda *a, **kw: io.BytesIO(
                            _j.dumps({"profile": {"email": "x@y.z"}}).encode()))
    assert loc._default_fetch_profile("x@y.z") == {"email": "x@y.z"}


def test_a_blank_email_is_not_reported_as_unreachable(monkeypatch):
    """No email means nothing was looked up, so there is nothing to warn about."""
    import biofield_local_app as loc
    assert loc._default_fetch_profile("") == {}
