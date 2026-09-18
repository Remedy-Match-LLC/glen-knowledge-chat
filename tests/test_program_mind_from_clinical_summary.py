"""The Mind stage is the checked Clinical Summary factors and their checked remedies.

Glen, 2026-09-18: "I tried the minimum program button. It says mind: nothing here. It
doesn't include the remedies checked in the Clinical Summary. It seems the Clinical
Summary input is missing." Then: "Mind stage is the checked Clinical summary factors and
their checked remedies."

WHAT WAS WRONG. Mind filtered the STRESS list to sources ("tag", "comm", "manual"). On
the test he was looking at that was two rows, an E4L code and a flower essence, and
neither is a condition. The six conditions he had checked live in
biofield_clinical_selection and nothing in the program builder ever read that table. So
"mind: nothing here" was the honest output of an input that was never connected.

THE TWO TABLES DISAGREE ON THE KEY. biofield_clinical_selection stores 'a37';
biofield_auth_stress stores '37'. The stress reader strips the 'a', so stripping it again
for the selections would return nothing. The route passes the raw test_id and says so.

HIS REMEDY IS PLACED, NEVER SUBSTITUTED. The set-cover chooses for Spirit and Body. Here
he has already chosen, so every checked remedy is placed as-is.

A FACTOR HE CHECKED WITH NO REMEDY IS REPORTED, NOT DROPPED. It comes back in `uncovered`,
because a condition with nothing against it is the thing he most needs to see. Note that
biofield_clinical_proposals.selections() filters those out for the page's own purposes,
which is why selected_items exists.
"""
import pathlib

import pytest

from dashboard.biofield_program import FULL, MINIMUM, build_program
from dashboard.biofield_stress import _norm

REAL = [  # the six actually checked on test a37
    {"label": "cataracts", "remedies": ["Clarity", "Crystalline Clarity",
                                        "Golden Book", "OcuHeal Eye Drops"]},
    {"label": "certain perfumes — inhalation irritant", "remedies": ["Immune Modulation"]},
    {"label": "digestion", "remedies": ["Digestzymes HomeoEnergetic Drops"]},
    {"label": "dryer sheets — inhalation irritant", "remedies": []},
    {"label": "heart, arteries", "remedies": ["Heart Health"]},
    {"label": "toenail fungus", "remedies": []},
]


def _run(mode=FULL, mind=None, stresses=None, seed=None, cover=None, covers_of=None,
         functions_of=None):
    p = build_program(stresses=stresses or [], spirit_layers=seed or [],
                      cover=cover or (lambda t: []),
                      functions_of=functions_of or (lambda t: ()),
                      mode=mode, mind_items=mind, covers_of=covers_of)
    return p, {s["stage"]: s for s in p["stages"]}


@pytest.mark.parametrize("mode", [FULL, MINIMUM])
def test_the_checked_factors_reach_the_mind_stage(mode):
    """The reported bug: it said "nothing here" with six factors checked."""
    _, st = _run(mode, REAL)
    assert st["mind"]["picks"], "mind is still empty with checked factors present"


@pytest.mark.parametrize("mode", [FULL, MINIMUM])
def test_every_checked_remedy_is_placed_exactly_as_chosen(mode):
    """Glen's pick is not a suggestion for the cover to improve on."""
    _, st = _run(mode, REAL)
    placed = [p["remedy"] for p in st["mind"]["picks"]]
    assert placed == ["Clarity", "Crystalline Clarity", "Golden Book",
                      "OcuHeal Eye Drops", "Immune Modulation",
                      "Digestzymes HomeoEnergetic Drops", "Heart Health"]


def test_a_factor_with_no_remedy_is_reported_not_dropped():
    _, st = _run(FULL, REAL)
    assert [u["label"] for u in st["mind"]["uncovered"]] == [
        "dryer sheets — inhalation irritant", "toenail fungus"]
    assert not any(p["remedy"] == "" for p in st["mind"]["picks"])


def test_mind_runs_between_spirit_and_body():
    """His priority sequence: Spirit, then Mind, then Body."""
    p, _ = _run(FULL, REAL)
    assert [s["stage"] for s in p["stages"]] == ["spirit", "mind", "body"]


@pytest.mark.parametrize("mode", [FULL, MINIMUM])
def test_spirit_suppresses_a_factor_it_already_covers(mode):
    seed = [{"remedy": "Eye Restore", "covers": [_norm("cataracts")]}]
    _, st = _run(mode, [{"label": "cataracts", "remedies": ["Clarity"]}], seed=seed)
    assert not st["mind"]["picks"]
    assert st["mind"]["suppressed"][0]["by"] == "Eye Restore"


def test_a_mind_remedy_suppresses_the_body_finding_it_covers():
    """Glen's rule 3: Body "if not already addressed by relevant remedies from 1 & 2".

    A scan token is an E4L CODE and a condition token is a normalised LABEL, so they never
    match by accident. Without covers_of the Mind pick covers only its own label and this
    rule silently does nothing.
    """
    _, st = _run(FULL, [{"label": "cataracts", "remedies": ["Clarity"]}],
                 stresses=[{"source": "scan", "code": "ED3", "label": "eyes",
                            "balance": "required"}],
                 cover=lambda t: [{"remedy": "Body Pick", "covers": list(t)}],
                 covers_of=lambda r: {"ED3"} if r == "Clarity" else set())
    assert st["body"]["suppressed"] and st["body"]["suppressed"][0]["by"] == "Clarity"
    assert not st["body"]["picks"], "the body stage placed a remedy it did not need"


def test_without_covers_of_the_mind_pick_still_covers_its_own_label():
    """The fallback must not crash or cover nothing at all."""
    _, st = _run(FULL, [{"label": "digestion", "remedies": ["Digestzymes"]}])
    assert st["mind"]["picks"][0]["covers"] == [_norm("digestion")]


def test_minimum_suppresses_a_factor_whose_function_is_addressed():
    """The difference between the two buttons, preserved for Mind."""
    seed = [{"remedy": "Eye Restore", "covers": ["other"]}]
    kw = dict(mind=[{"label": "cataracts", "remedies": ["Clarity"]}], seed=seed,
              functions_of=lambda t: ("vision",))
    _, full = _run(FULL, **kw)
    _, mini = _run(MINIMUM, **kw)
    assert full["mind"]["picks"], "FULL should still place it"
    assert not mini["mind"]["picks"], "MINIMUM should suppress on a shared function"


def test_the_stress_sources_still_feed_mind_as_well():
    """ADDITIVE, not a replacement, and this is a judgement call worth stating.

    Glen asked on 2026-09-17 for his own typed stresses to ride with Mind, and six tests
    pinned that. His sentence on 2026-09-18 widens the stage; it is not an instruction to
    discard the other half. I first implemented it as a replacement and those six tests
    went red, which is the system telling me I was throwing something away.

    One word makes it exclusive if that is what he meant.
    """
    _, st = _run(FULL, [], stresses=[{"source": "tag", "label": "old mind row",
                                      "balance": "required"}],
                 cover=lambda t: [{"remedy": "X", "covers": list(t)}])
    assert [p["remedy"] for p in st["mind"]["picks"]] == ["X"]


def test_the_checked_factors_lead_the_stress_derived_picks():
    """His explicit choices come first in the stage."""
    _, st = _run(FULL, [{"label": "digestion", "remedies": ["Digestzymes"]}],
                 stresses=[{"source": "tag", "label": "worthiness",
                            "balance": "required"}],
                 cover=lambda t: [{"remedy": "R-worthiness", "covers": list(t)}])
    assert [p["remedy"] for p in st["mind"]["picks"]] == ["Digestzymes", "R-worthiness"]


def test_a_checked_factor_suppresses_a_manual_stress_saying_the_same_thing():
    """Otherwise one condition placed twice: once by his check, once by the cover."""
    _, st = _run(FULL, [{"label": "worthiness", "remedies": ["Clarity"]}],
                 stresses=[{"source": "tag", "label": "worthiness",
                            "balance": "required"}],
                 cover=lambda t: [{"remedy": "R-worthiness", "covers": list(t)}])
    assert [p["remedy"] for p in st["mind"]["picks"]] == ["Clarity"]


def test_no_mind_items_is_an_empty_stage_not_a_crash():
    p, st = _run(FULL, None)
    assert st["mind"]["picks"] == [] and st["mind"]["uncovered"] == []
    assert p["remedies"] == []


def _code_of(path, fn):
    """The EXECUTABLE body of a function: no docstring, no comments.

    Five checks tonight matched prose instead of code -- a comment about lastrowid, a CSS
    class name, string order, and twice a docstring that explains the very thing the test
    forbids. Parsing beats substring-hunting, so this walks the syntax tree and unparses
    only the statements.
    """
    import ast
    tree = ast.parse(pathlib.Path(path).read_text())
    node = next(n for n in ast.walk(tree)
                if isinstance(n, ast.FunctionDef) and n.name == fn)
    body = list(node.body)
    if (body and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)):
        body = body[1:]                      # drop the docstring
    return "\n".join(ast.unparse(s) for s in body)


def test_the_reader_keeps_the_label_and_the_empty_remedy_list():
    """selected_items must not inherit selections()' `remedies IS NOT NULL` filter, which
    would hide exactly the factors Glen needs to see."""
    code = _code_of(pathlib.Path(__file__).resolve().parents[1]
                    / "dashboard" / "biofield_clinical_proposals.py", "selected_items")
    assert "SELECT label,remedies" in code
    assert "remedies IS NOT NULL" not in code, (
        "the filter that hides remedy-less factors came back"
    )


def test_the_route_passes_the_raw_test_id_to_the_selection_reader():
    """The two tables key differently: 'a37' here, '37' for stresses. Stripping the 'a'
    would return nothing and look like an empty Clinical Summary."""
    import pathlib
    src = (pathlib.Path(__file__).resolve().parents[1] / "biofield_local_app.py").read_text()
    assert "_cp.selected_items(cx, test_id)" in src
    assert "_cp.selected_items(cx, _num(test_id))" not in src


# --- whose mind, and whose signal -------------------------------------------------
#
# Glen, 2026-09-18: "The mind of the patient identifies symptoms and the mind of the
# physician identifies diagnoses. The mind is dominantly visual, associated with
# Iridium. The manual biofield stress responses and balancing remedies in remote
# biofield analysis come from coherent communication between the spirit of the tester as
# surrogate and the spirit of the client, based on meaning associated with Gold. The
# frequency patterns in the client's voice come directly from the client's own body,
# associated with Rhodium."
#
# Each stage is defined by WHERE THE SIGNAL CAME FROM, not by how it was entered.


def test_a_manual_biofield_response_is_spirit_not_mind():
    """The correction he asked for by name. A manual response is the tester's spirit
    reading the client's, which is Gold, not the physician's visual diagnosis."""
    from dashboard.biofield_program import MIND_SOURCES, SPIRIT_SOURCES
    assert "manual" in SPIRIT_SOURCES
    assert "manual" not in MIND_SOURCES


def test_mind_keeps_what_is_mined_from_history():
    """Symptoms and diagnoses. Tags and communications are the patient's own reports."""
    from dashboard.biofield_program import MIND_SOURCES
    assert set(MIND_SOURCES) == {"tag", "comm"}


def test_the_scan_is_body():
    """Frequency patterns from the client's own body. Rhodium."""
    from dashboard.biofield_program import BODY_SOURCES
    assert set(BODY_SOURCES) == {"scan"}


def test_no_source_is_claimed_by_two_stages():
    """A stress placed in two stages would be balanced twice."""
    from dashboard.biofield_program import BODY_SOURCES, MIND_SOURCES, SPIRIT_SOURCES
    pairs = [SPIRIT_SOURCES, MIND_SOURCES, BODY_SOURCES]
    seen = [s for group in pairs for s in group]
    assert len(seen) == len(set(seen)), f"a source appears twice: {seen}"


def test_a_manual_response_now_suppresses_a_mind_factor_it_covers():
    """The practical consequence of the move. Spirit leads, so a surrogate reading of
    the same thing now suppresses the checked condition rather than sitting after it."""
    _, st = _run(FULL, [{"label": "worthiness", "remedies": ["Clarity"]}],
                 stresses=[{"source": "manual", "label": "worthiness",
                            "balance": "required"}],
                 cover=lambda t: [{"remedy": "R-worthiness", "covers": list(t)}])
    assert [p["remedy"] for p in st["spirit"]["picks"]] == ["R-worthiness"]
    assert not st["mind"]["picks"], "the checked factor was already answered by Spirit"
    assert st["mind"]["suppressed"][0]["by"] == "R-worthiness"
