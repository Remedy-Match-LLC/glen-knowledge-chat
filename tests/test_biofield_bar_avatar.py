"""The Intake bar shows the client's photo beside their name.

Glen, 2026-09-16: "In Biofield Intake, show a small version of the client's image to the
right of their name (when available)."

The bar is built as a string with no network of its own, so "when available" cannot be
answered here. It is answered by the server: /client-photo/<email> is console-gated and
returns 404 when there is no photo, and `onerror` removes the element so the bar closes up.
"""
import importlib

import pytest


@pytest.fixture(scope="module")
def mod():
    return importlib.import_module("dashboard.biofield_report_html")


def test_the_photo_sits_after_the_name_not_before_it(mod):
    bar = mod._bar("Ashu Paul", "ashu@example.com")
    assert bar.index("Ashu Paul") < bar.index("opavatar"), (
        "Glen asked for the image to the RIGHT of the name"
    )


def test_the_photo_points_at_the_console_and_hides_itself_when_missing(mod):
    bar = mod._bar("Ashu Paul", "ashu@example.com")
    assert f"{mod.CONSOLE_BASE}/client-photo/ashu%40example.com" in bar
    assert "onerror='this.remove()'" in bar, "a 404 must leave no broken image in the bar"


def test_no_email_means_no_photo(mod):
    bar = mod._bar("Ashu Paul", "")
    assert "opavatar" not in bar
    assert "Ashu Paul" in bar, "the name must still show"


def test_no_name_means_no_photo(mod):
    """A floating portrait with nothing to label it is worse than none."""
    assert "opavatar" not in mod._bar("", "ashu@example.com")


def test_the_email_is_url_quoted_into_the_path(mod):
    """An address with a + or a space must not break the URL or escape the path segment."""
    bar = mod._bar("Ann Bauder", "ann+lens test@example.com")
    assert "ann%2Blens%20test%40example.com" in bar
    assert "/client-photo/ann+lens test@example.com" not in bar


def test_a_hostile_address_cannot_break_out_of_the_attribute(mod):
    bar = mod._bar("X", "a'onerror='alert(1)@example.com")
    assert "alert(1)" not in bar or "%27" in bar
    assert bar.count("onerror='this.remove()'") == 1, "only the intended onerror may survive"


def test_the_name_is_still_escaped(mod):
    bar = mod._bar("<script>alert(1)</script>", "x@example.com")
    assert "<script>alert(1)</script>" not in bar
    assert "&lt;script&gt;" in bar


def test_the_bar_still_works_with_no_client_at_all(mod):
    """Every other page in this app renders the bar with no client."""
    bar = mod._bar()
    assert "opavatar" not in bar and "opclient" not in bar
    assert "Biofield Intake" in bar
