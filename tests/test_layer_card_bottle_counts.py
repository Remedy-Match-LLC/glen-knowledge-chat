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


def test_a_remedy_never_bought_shows_an_explicit_zero():
    """Glen, 2026-09-16: he wants a zero on a product never purchased before. A
    blank reads as "no history looked up" as easily as "never had it"."""
    assert "0 bottles" in _line("Transform")


def test_the_match_ignores_case_and_padding():
    assert "11 bottles" in _line("  NEUROPROTECT ")


def test_an_empty_remedy_shows_no_number():
    """No remedy chosen yet, so there is nothing to count."""
    assert "bottle" not in _line("")


def test_a_client_with_no_history_at_all_shows_zeros():
    """Which is the answer: this client has never bought it."""
    h = _remedy_line({"rid": "7", "remedy": "Neuroprotect"}, [])
    assert "0 bottles" in h
    assert 'id="r7_remedy"' in h
