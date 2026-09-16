"""Glen, 2026-09-16, asked for the bottle count "when remedies show in Biofield
Intake", then confirmed he wants it on the layer cards too, not only in the
Previously-dispensed panel.

It is decision-relevant where the decision is made: seeing that a client has already
had eleven bottles of something is different from seeing it in a panel you have to
open.
"""
from dashboard.biofield_report_html import _remedy_line

BOTTLES = {"neuroprotect": 11, "brain boost": 1}


def _line(remedy, bottles=BOTTLES):
    return _remedy_line({"rid": "7", "remedy": remedy}, [], bottles_by_remedy=bottles)


def test_a_previously_bought_remedy_shows_its_bottle_count():
    h = _line("Neuroprotect")
    assert "11 bottles" in h


def test_one_bottle_is_singular():
    assert "1 bottle" in _line("Brain Boost") and "1 bottles" not in _line("Brain Boost")


def test_a_remedy_never_bought_shows_no_number():
    h = _line("Transform")
    assert "bottle" not in h


def test_the_match_ignores_case_and_padding():
    assert "11 bottles" in _line("  NEUROPROTECT ")


def test_an_empty_remedy_shows_no_number():
    assert "bottle" not in _line("")


def test_the_page_still_renders_without_any_history():
    """bottles_by_remedy defaults to nothing, so every existing caller is unchanged."""
    h = _remedy_line({"rid": "7", "remedy": "Neuroprotect"}, [])
    assert "bottle" not in h
    assert 'id="r7_remedy"' in h
