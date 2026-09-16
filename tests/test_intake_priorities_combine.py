"""Two intake answers can be combined into one condition.

Glen, 2026-09-16: "Still no function to combine two stress patterns in Clinical Summary",
clarified as "I meant the 'significant symptoms and conditions from intake' not 'Stress
pattern (head & tail)'". That is the "Top Health Goals, as the client ranked them" list,
which was read-only: two answers that are really one condition pulled in as two rows that
then had to be combined by hand.

Asked whether a fold declared here should be standing, he confirmed the first reading:
combine through the SAME alias store the checklist rows use, so it is one judgement
applying across clients rather than a second parallel mechanism.

The client's own wording is never discarded. A folded line names what went into it.
"""
import pytest

from dashboard import biofield_report_html as html
from dashboard.biofield_clinical_checklist import _norm


def _render(priorities, items=None, alias_map=None, display_map=None):
    return html.render_clinical_checklist(
        items or [], intake_priorities=priorities,
        alias_map=alias_map or {}, display_map=display_map or {})


DRY = {"concern": "dry eyes", "rating": 8, "years_since_onset": 3}
BURN = {"concern": "burning at night", "rating": 6, "years_since_onset": None}


def test_without_a_fold_both_answers_show():
    out = _render([DRY, BURN])
    assert "dry eyes" in out and "burning at night" in out
    assert out.count("<li") == 2


def test_a_folded_pair_renders_as_one_line():
    out = _render([DRY, BURN], alias_map={_norm("burning at night"): "dry eyes"})
    assert out.count("<li") == 1, "two answers declared to be one must render as one line"


def test_the_folded_line_names_what_went_into_it():
    """Silently swallowing one of the client's own answers is worse than two lines."""
    out = _render([DRY, BURN], alias_map={_norm("burning at night"): "dry eyes"})
    assert "with burning at night" in out


def test_the_chosen_name_is_used_when_there_is_one():
    out = _render([DRY, BURN],
                  alias_map={_norm("burning at night"): "dry eyes"},
                  display_map={_norm("dry eyes"): "Ocular Surface"})
    assert "Ocular Surface" in out
    assert "with burning at night" in out, "the client's own words still appear"


def test_the_client_ranking_order_is_kept():
    """The intake asks for them in order of importance, so form order is the ranking."""
    out = _render([DRY, BURN])
    assert out.index("dry eyes") < out.index("burning at night")


def test_the_pull_count_counts_folded_items_once():
    """Two answers folded into one are one thing to pull, not two."""
    out = _render([DRY, BURN], alias_map={_norm("burning at night"): "dry eyes"})
    assert "Pull 1 from intake" in out


def test_an_already_listed_survivor_is_marked_and_not_counted():
    items = [{"label": "dry eyes", "checked": False, "covered_by": "", "layer": None,
              "common_remedies": [], "stress_pattern": "", "remembered_pattern": "",
              "pattern_is_suggested": False}]
    out = _render([DRY, BURN], items=items,
                  alias_map={_norm("burning at night"): "dry eyes"})
    assert "already listed" in out
    assert "All already listed" in out, "nothing left to pull"


def test_the_combine_control_is_offered_when_there_is_something_to_combine():
    out = _render([DRY, BURN])
    assert "combineIntakeItems(this)" in out
    assert "intake-combine-pick" in out


def test_a_single_answer_offers_no_combine_control():
    """Nothing to fold it into."""
    out = _render([DRY])
    assert "combineIntakeItems(this)" not in out


def test_the_control_does_not_offer_folding_an_item_into_itself():
    out = _render([DRY, BURN])
    first = out[out.index("<li"):out.index("</li>")]
    assert "burning at night" in first, "the other answer must be offered"
    assert first.count('value="dry eyes"') == 0, "a row must not offer itself"


def test_the_concern_text_is_escaped():
    out = _render([{"concern": "<script>alert(1)</script>", "rating": None,
                    "years_since_onset": None}])
    assert "<script>alert(1)</script>" not in out
    assert "&lt;script&gt;" in out


def test_no_maps_renders_exactly_as_before():
    """Every existing caller passed neither map. The default must change nothing."""
    assert html.render_clinical_checklist([], intake_priorities=[DRY, BURN]) == \
        _render([DRY, BURN])
