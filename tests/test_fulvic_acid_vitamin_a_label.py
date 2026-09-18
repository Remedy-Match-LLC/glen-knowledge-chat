"""Fulvic Acid's vitamin A line names the form and the activity.

Glen, 2026-09-17: "retinyl palmitate is the form."

WHAT WAS WRONG. The catalog read {"name": "Vitamin A", "dose": "100 mg"}, and that is
served publicly on the product page. It reads as 100 mg of vitamin A, which would be
roughly a hundred times the daily value.

WHAT IS ACTUALLY TRUE. 100 mg is the weight of the MATERIAL, which the vault records at
13.75% retinyl palmitate. The supplier row states the equivalence outright:

    Vitamin A Palmitate / Retinyl Palmitate, concentration 13.75%
    dosage_range: "7,500-25,000 IU = 30-100 mg"

Both ends check: 30 mg and 100 mg of a 13.75% material give 7,500 and 25,000 IU. So the
DOSE was right and the LABEL was wrong, and the alarm I raised about it was an artifact of
the label rather than a fact about the product.

WHY THE 650 mg FULVIC LINE IS UNTOUCHED. It is not in question. The vault's research range
for Fulvic Acid 95% is 1000-2000 mg/day, so 650 mg is an ordinary single dose.
"""
import json
import pathlib

CATALOG = json.loads(
    (pathlib.Path(__file__).resolve().parents[1] / "data" / "products.json").read_text()
)["products"]
P = CATALOG["fulvic-acid"]


def test_the_form_is_named_not_just_the_vitamin():
    """'Vitamin A' alone is the defect: it makes an ester's weight read as the vitamin's."""
    line = P["ingredients"][1]
    assert line["name"] == "Vitamin A (as Retinyl Palmitate)", line


def test_the_activity_is_stated_alongside_the_material_weight():
    """A weight without an activity cannot be read correctly by anyone, including us."""
    assert P["ingredients"][1]["dose"] == "100 mg (25,000 IU)"


def test_the_bare_vitamin_a_label_is_gone_from_this_product():
    assert not any(
        (i.get("name") or "").strip().lower() == "vitamin a" for i in P["ingredients"]
    ), "the bare label is back; it reads as 100 mg of the vitamin itself"


def test_the_fulvic_line_was_not_disturbed():
    """Only one line was in question. A fix that quietly edits its neighbour is a new bug."""
    assert P["ingredients"][0] == {"name": "Fulvic Acid", "dose": "650 mg"}


def test_the_reasoning_is_recorded_on_the_product():
    """This product has already been corrected once for a bad FMP match, and the note is
    what stopped it being re-derived. The same protection applies here."""
    note = P.get("enrichment_note") or ""
    assert "13.75%" in note and "25,000 IU" in note, note
    assert "retinyl palmitate" in note.lower()


def test_its_sibling_still_carries_glens_own_correction():
    """humic-acid was corrected by Glen for the SAME bad source, 'Fulvic Acid Complex'.
    If that note ever disappears, this pair is open to being re-derived wrongly again."""
    note = CATALOG["humic-acid"].get("enrichment_note") or ""
    assert "Fulvic Acid Complex" in note and "wrong" in note
