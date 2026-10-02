"""Rejuvenator layers get the Harmony Laser as an extra first-order remedy.

Glen, 2026-10-01: "For Rejuvenator layers, add the Harmony Laser as a 1st order remedy".
ER2 (Large Intestine) and ER33 (Thyroid) alone get no laser ("Don't show it on those two
caution ERs"); ER11 Parathyroid gets it ("leave it on parathyroid"). A mixed layer gets the
laser with his caution, approved word for word ("perfect"). Humans and animals alike.
"""
import sqlite3

import pytest

from dashboard import biofield_reveal_import as RI

CAUTION = ("Take care using infrared directly over the thyroid or the colon. Cleansing "
           "reactions are more likely there, so start with the smallest dose and increase "
           "only as you tolerate it.")


@pytest.mark.parametrize("codes,expected", [
    (["ER17"], {"name": "Harmony Laser", "caution": ""}),
    (["ER11", "ES1"], {"name": "Harmony Laser", "caution": ""}),
    (["ER2"], None),
    (["ER33"], None),
    (["ER2", "ER33"], None),
    (["ER2", "ER17"], {"name": "Harmony Laser", "caution": CAUTION}),
    (["ER33", "ER11"], {"name": "Harmony Laser", "caution": CAUTION}),
    (["ES1", "MB8"], None),
    (["MR3", "ER"], None),            # not a numbered ER code
    ([], None),
])
def test_which_layers_get_the_laser(codes, expected):
    assert RI.harmony_laser_for(codes) == expected


def test_the_caution_is_glens_approved_wording():
    assert RI.HARMONY_CAUTION == CAUTION
    assert "—" not in CAUTION


def test_the_laser_name_resolves_to_its_product_on_publish():
    """The FileMaker name does not resolve; an unresolved remedy blocks publishing."""
    from dashboard import biofield_portal_publish as P
    assert P.name_to_slug(RI.HARMONY_LASER, P.load_catalog()) == "harmony-laser"


def _raw(codes, remedy="WholOmega"):
    return [{"n": 1, "title": "Layer 1", "summary": "", "remedy": {"name": remedy},
             "pattern_labels": ["x"] * len(codes), "patterns": codes, "alternatives": []}]


@pytest.mark.parametrize("is_animal", [False, True])
def test_import_adds_the_laser_beside_the_layers_remedy(tmp_path, is_animal):
    def runner(email, scan_id, e4l_db, catalog, today):
        return {"scan_id": "s1", "scan_date": "2026-10-01"}, _raw(["ER2", "ER17", "ES1"])
    res = RI.synthesize_reveal_layers("c@x.com", today="2026-10-01", runner=runner,
                                      is_animal=is_animal,
                                      infoceutical_names={"ES1": "ES1 Immune Energetic Star Infoceutical"})
    cx = sqlite3.connect(str(tmp_path / "c.db"))
    from dashboard.biofield_authoring import init_auth_tables
    init_auth_tables(cx)
    cx.execute("INSERT INTO biofield_auth_tests (id) VALUES (7)")
    n = RI.import_layers_to_test(cx, "a7", res["layers"])
    rows = cx.execute("SELECT layer, remedy, timing FROM biofield_auth_chain ORDER BY id").fetchall()
    first = "ES1 Immune Energetic Star Infoceutical" if is_animal else "WholOmega"
    assert n == 2
    assert rows[0][:2] == (1, first)
    assert rows[1][0] == 1 and rows[1][1] == "Harmony Laser"
    assert CAUTION in rows[1][2]


def test_a_layer_with_only_caution_ers_gets_no_laser_row(tmp_path):
    def runner(email, scan_id, e4l_db, catalog, today):
        return {"scan_id": "s1", "scan_date": "2026-10-01"}, _raw(["ER2", "ER33"])
    res = RI.synthesize_reveal_layers("c@x.com", today="2026-10-01", runner=runner)
    cx = sqlite3.connect(str(tmp_path / "c.db"))
    from dashboard.biofield_authoring import init_auth_tables
    init_auth_tables(cx)
    cx.execute("INSERT INTO biofield_auth_tests (id) VALUES (7)")
    assert RI.import_layers_to_test(cx, "a7", res["layers"]) == 1
    assert "Harmony" not in str(cx.execute("SELECT remedy FROM biofield_auth_chain").fetchall())
