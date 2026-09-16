"""A combined condition can carry a name the practitioner chose.

Glen, 2026-09-16: "You could suggest a name that I can edit if I want for the combination."
Asked whether it should apply to every client, since the alias store is not client-scoped,
he confirmed: "yes - across clients."

Builds on #1704, which folds two conditions with an alias rather than by rewriting labels.
That was the right call and this does not undo it. The name is a DISPLAY override, resolved
at render time.

Why it is not simply the label. Remembered remedies, the stress pattern, the layer
assignment and the shared catalog are all stored against the canonical label. Renaming it
would mint a new catalog term for every combination, and strand everything recorded under
the old name. That was the exact risk raised before #1704 was written, and an override
avoids it: deleting one row restores the original name and nothing else moves.
"""
import sqlite3

import pytest

from dashboard import biofield_clinical_checklist as cc


@pytest.fixture()
def cx():
    c = sqlite3.connect(":memory:")
    cc.ensure_alias_schema(c)
    cc.ensure_display_schema(c)
    yield c
    c.close()


def test_the_suggestion_joins_both_names():
    assert cc.suggested_combined_label("Eyes", "Adrenals") == "Eyes + Adrenals"


def test_the_suggestion_degrades_sensibly():
    assert cc.suggested_combined_label("Eyes", "") == "Eyes"
    assert cc.suggested_combined_label("", "Adrenals") == "Adrenals"
    assert cc.suggested_combined_label("Eyes", "eyes") == "Eyes", (
        "the same condition twice must not read 'Eyes + eyes'"
    )


def test_a_name_is_stored_and_read_back(cx):
    cc.set_display_label(cx, "Eyes", "Adrenal-Eye Axis")
    assert cc.display_labels(cx)[cc._norm("Eyes")] == "Adrenal-Eye Axis"


def test_the_name_is_keyed_the_same_way_labels_are_matched(cx):
    """The checklist matches labels through _norm, so the override must too."""
    cc.set_display_label(cx, "Eyes", "Adrenal-Eye Axis")
    assert cc.display_labels(cx).get(cc._norm("  eyes  ")) == "Adrenal-Eye Axis"


def test_an_empty_name_clears_the_override(cx):
    """Cancelling the prompt must leave the survivor's own name, as before this existed."""
    cc.set_display_label(cx, "Eyes", "Adrenal-Eye Axis")
    cc.set_display_label(cx, "Eyes", "")
    assert cc.display_labels(cx) == {}


def test_naming_it_the_same_thing_stores_nothing(cx):
    """An override equal to the label is noise, and would survive a later rename."""
    cc.set_display_label(cx, "Eyes", "Eyes")
    assert cc.display_labels(cx) == {}


def test_renaming_replaces_rather_than_duplicating(cx):
    cc.set_display_label(cx, "Eyes", "First Name")
    cc.set_display_label(cx, "Eyes", "Second Name")
    rows = cc.display_labels(cx)
    assert rows == {cc._norm("Eyes"): "Second Name"}


def test_no_connection_means_no_override_and_no_crash():
    """Every existing caller passes no connection. It must behave as before."""
    assert cc.display_labels(None) == {}


def test_the_override_never_becomes_the_stored_label(cx):
    """The whole point. Remedies stay recorded against the canonical name.

    If the override leaked into the label, remember_remedies would key the shared
    catalog on "Adrenal-Eye Axis" and everything recorded under "Eyes" would be lost
    to it.
    """
    cc.ensure_catalog_schema(cx)
    cc.remember_remedies(cx, "Eyes", ["Bilberry"])
    cc.set_display_label(cx, "Eyes", "Adrenal-Eye Axis")

    assert "Bilberry" in cc.custom_remedies(cx, "Eyes")
    assert cc.custom_remedies(cx, "Adrenal-Eye Axis") == [], (
        "the display name must not exist in the catalog at all"
    )
    rows = cc.catalog_items(cx)
    names = {(r.get("label") or "") for r in rows}
    assert "Adrenal-Eye Axis" not in names, (
        "a combination must not mint a catalog term; that was the risk this design avoids"
    )


def test_the_row_carries_the_display_name_separately_from_its_label():
    """render_clinical_checklist shows display_label and keys data-label on label."""
    from dashboard import biofield_report_html as html
    out = html.render_clinical_checklist([{
        "label": "Eyes", "display_label": "Adrenal-Eye Axis",
        "checked": False, "covered_by": "", "layer": None,
        "common_remedies": [], "stress_pattern": "", "remembered_pattern": "",
        "pattern_is_suggested": False,
    }])
    assert 'data-label="Eyes"' in out, "the canonical label must key the row"
    assert ">Adrenal-Eye Axis<" in out, "the chosen name must be what is shown"


def test_without_a_chosen_name_the_row_shows_its_own_label():
    from dashboard import biofield_report_html as html
    out = html.render_clinical_checklist([{
        "label": "Eyes", "display_label": "",
        "checked": False, "covered_by": "", "layer": None,
        "common_remedies": [], "stress_pattern": "", "remembered_pattern": "",
        "pattern_is_suggested": False,
    }])
    assert 'data-label="Eyes"' in out
    assert ">Eyes<" in out
