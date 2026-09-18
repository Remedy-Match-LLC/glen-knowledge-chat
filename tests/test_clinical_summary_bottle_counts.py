"""Bottles bought before, shown where the remedy is chosen.

Glen, 2026-09-18: "the numbers also need to show in the clinical summary for remedies
that have been previously used."

The count already appeared in two places: the collapsed Previously-dispensed panel, and
the chain card once a remedy has been picked. The Clinical Summary is where the picking
actually happens, and it had no number at all.

ZERO IS TREATED DIFFERENTLY HERE, ON PURPOSE. On a chain card the remedy is already
chosen, so "0 bottles" is a fact worth stating, and Glen asked for that zero explicitly on
2026-09-16: a blank reads as "no history looked up" as easily as "never had it". In the
summary the list is CANDIDATES, several per row, and a column of zeros would bury the one
number that matters. His wording this time scopes it to remedies previously used.

That is a genuine ambiguity between two of his own instructions, so it is written down
rather than silently resolved, and one word flips it.

UNAVAILABLE IS NOT ZERO. If the history could not be read, the chip says so. Without
that, an unbought remedy and an unreachable server look identical -- which is precisely
the bug the dispensed panel had for a day.
"""
from dashboard.biofield_report_html import _summary_bottles_chip, render_clinical_checklist

BOTTLES = {"fungifuge": 2, "brain boost": 1, "candida cleanse": 1}
ITEMS = [{"label": "Gut terrain", "common_remedies": ["Fungifuge", "Microbiome"],
          "chosen_remedies": [], "layer": None}]


def test_a_previously_used_remedy_shows_its_count():
    assert "2 bottles" in _summary_bottles_chip("Fungifuge", BOTTLES)


def test_one_bottle_is_singular():
    assert "1 bottle<" in _summary_bottles_chip("Brain Boost", BOTTLES)


def test_the_match_is_case_insensitive():
    """The catalog name and the order line rarely agree on case."""
    assert "2 bottles" in _summary_bottles_chip("FUNGIFUGE", BOTTLES)
    assert "2 bottles" in _summary_bottles_chip("  fungifuge  ", BOTTLES)


def test_a_remedy_never_bought_shows_an_explicit_zero():
    """The regression. Hiding it was my first reading and Glen corrected it: a blank is
    ambiguous between "never bought" and "history not looked up"."""
    out = _summary_bottles_chip("Microbiome", BOTTLES)
    assert "0 bottles" in out, "a never-purchased remedy must say 0, not go blank"
    assert "zerobuy" in out, "the zero should be muted so a real count stands out"


def test_an_unknown_history_says_so_rather_than_implying_zero():
    """The distinction the dispensed panel lacked for a day."""
    out = _summary_bottles_chip("Microbiome", None, unavailable=True)
    assert "history unavailable" in out
    assert "0 bottle" not in out


def test_unavailable_wins_even_for_a_remedy_with_a_count():
    """If the read failed, no number on the page is trustworthy, including a stale one."""
    out = _summary_bottles_chip("Fungifuge", BOTTLES, unavailable=True)
    assert "history unavailable" in out and "2 bottles" not in out


def test_the_checklist_renders_the_chip_beside_the_remedy():
    html = render_clinical_checklist(ITEMS, bottles_by_remedy=BOTTLES)
    assert "Fungifuge" in html and "2 bottles" in html
    i, j = html.index("Fungifuge"), html.index("2 bottles")
    assert 0 < j - i < 160, "the count must sit beside its remedy, not elsewhere"


def test_the_checklist_shows_a_zero_for_an_unbought_candidate():
    html = render_clinical_checklist(ITEMS, bottles_by_remedy=BOTTLES)
    k = html.index("Microbiome")
    assert "0 bottles" in html[k:k + 200]


def test_the_checklist_passes_the_unavailable_flag_through():
    html = render_clinical_checklist(ITEMS, bottles_by_remedy=None, bottles_unavailable=True)
    assert "history unavailable" in html


def test_it_still_renders_with_no_bottle_data_at_all():
    """Every existing caller omits the new arguments. None of them may break."""
    html = render_clinical_checklist(ITEMS)
    assert "Fungifuge" in html
    # With no data at all every remedy reads 0, which is honest: nothing is known to have
    # been bought. The UNAVAILABLE case is what distinguishes "could not look" from that.
    assert "0 bottles" in html
    assert "history unavailable" not in html


def test_the_author_page_feeds_it_from_the_same_source_as_the_chain_card():
    """One source of truth. Two panels computing bottles differently would eventually
    disagree on the same page."""
    import pathlib
    src = (pathlib.Path(__file__).resolve().parents[1]
           / "dashboard" / "biofield_report_html.py").read_text()
    assert "bottles_by_remedy=_bottles_by_remedy(dispensed)" in src
    assert "bottles_unavailable=bool(dispensed_error)" in src
