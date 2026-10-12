"""Rejuvenator layers get the Harmony Laser as an extra first-order remedy.

Glen, 2026-10-01: "For Rejuvenator layers, add the Harmony Laser as a 1st order remedy".
Glen, 2026-10-12: no laser on any layer that touches the thyroid or the abdomen, mixed
layers included. Pelvic codes, the other neck codes, the diaphragm and the oesophagus keep
it. This replaced the 2026-10-01 caution rule. Every laser row carries Glen's caution,
approved 2026-10-12. Humans and animals alike.
"""
import sqlite3

import pytest

from dashboard import biofield_reveal_import as RI

# Glen's approved wording, 2026-10-12, word for word.
CAUTION = ("Start with the smallest dose and increase only as you tolerate it. During "
           "pregnancy, do not use it over the abdomen or pelvis. Never use it over the testes.")
BLOCKED = ["ER33", "ER11", "ER2", "ER10", "ER12", "ER17", "ER20", "ER25", "ER29", "ER34",
           "ER42", "ER43", "ER58", "ER60", "ER62"]
PELVIC = ["ER14", "ER22", "ER45", "ER50", "ER55", "ER56", "ER64"]
LASER = {"name": "Harmony Laser", "caution": CAUTION}


def test_the_blocked_list_is_glens_list_exactly():
    assert RI.LASER_BLOCKED_ERS == frozenset(BLOCKED)


@pytest.mark.parametrize("code", BLOCKED)
def test_a_thyroid_or_abdomen_code_blocks_the_laser_even_on_a_mixed_layer(code):
    assert RI.harmony_laser_for([code]) is None
    assert RI.harmony_laser_for(["ER18", code, "ES1"]) is None


@pytest.mark.parametrize("code", PELVIC)
def test_pelvic_codes_keep_the_laser(code):
    assert RI.harmony_laser_for([code]) == LASER


@pytest.mark.parametrize("codes,expected", [
    (["ER25", "ER33"], None),                          # Linda Maher's layer, scan 1066032
    (["ER18", "ER28", "ER44", "ER61"], LASER),         # Kimberly Jantz's layer keeps it
    (["ER18", "er25"], None),                          # case and padding do not matter
    (["ER18", " ER33 "], None),
    (["er18"], LASER),
    (["ER18", "ER 33"], None),                          # forms the AI might write
    (["ER18", "ER033"], None),
    (["ER18", "ER25 Stomach"], None),
    (["ER180"], LASER),                                # not ER18 + 0
    (["ES1", "MB8"], None),
    (["MR3", "ER"], None),            # not a numbered ER code
    ([], None),
])
def test_which_layers_get_the_laser(codes, expected):
    assert RI.harmony_laser_for(codes) == expected


def test_the_caution_is_glens_approved_wording():
    assert RI.HARMONY_CAUTION == CAUTION
    assert "\u2014" not in CAUTION


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
        return {"scan_id": "s1", "scan_date": "2026-10-01"}, _raw(["ER18", "ER44", "ES1"])
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
    assert CAUTION in rows[1][2], "every laser row carries the caution"


def test_a_layer_touching_the_thyroid_gets_no_laser_row(tmp_path):
    def runner(email, scan_id, e4l_db, catalog, today):
        return {"scan_id": "s1", "scan_date": "2026-10-01"}, _raw(["ER25", "ER33", "ER18"])
    res = RI.synthesize_reveal_layers("c@x.com", today="2026-10-01", runner=runner)
    cx = sqlite3.connect(str(tmp_path / "c.db"))
    from dashboard.biofield_authoring import init_auth_tables
    init_auth_tables(cx)
    cx.execute("INSERT INTO biofield_auth_tests (id) VALUES (7)")
    assert RI.import_layers_to_test(cx, "a7", res["layers"]) == 1
    assert "Harmony" not in str(cx.execute("SELECT remedy FROM biofield_auth_chain").fetchall())


# --- Glen 2026-10-01: recommended on the report, never invoiced or carted ("no") ---

def test_the_laser_is_never_an_invoice_line():
    from dashboard import biofield_invoice as bi
    catalog = [{"slug": "harmony-laser", "name": "Harmony Laser", "price_cents": 99700},
               {"slug": "wholomega", "name": "WholOmega", "price_cents": 6997}]
    got = bi.build_invoice_lines({}, ["WholOmega", "Harmony Laser"], catalog, include_fee=False)
    assert [l["slug"] for l in got["lines"]] == ["wholomega"]
    assert got["skipped"] == []                       # left out on purpose, not unresolved


def test_the_laser_shows_on_the_portal_report_but_never_in_the_cart():
    from dashboard import biofield_portal_publish as bpp
    from dashboard.biofield_authoring import create_test, add_chain_row, init_auth_tables
    cx = sqlite3.connect(":memory:")
    init_auth_tables(cx)
    aid = f"a{create_test(cx, 'C', 'c@x.com', '2026-10-01')}"
    add_chain_row(cx, aid, layer=1, head="Layer 1", most_affected="x", remedy="WholOmega",
                  dosage="1 capsule", frequency="twice daily")
    add_chain_row(cx, aid, layer=1, head="Layer 1", most_affected="x", remedy="Harmony Laser",
                  timing=CAUTION)
    catalog = {"harmony-laser": {"name": "Harmony Laser"}, "wholomega": {"name": "WholOmega"}}
    out = bpp.build_portal_content(cx, aid, special_price_cents=5000, catalog=catalog)
    assert [i["slug"] for i in out["content"]["reorder_items"]] == ["wholomega"]
    assert "Harmony Laser" in out["content"]["layers"][0]["remedy"]
    assert out["unresolved"] == []


def test_rae_handoff_never_invoices_or_carts_the_laser(tmp_path, monkeypatch):
    """Review round 3: the hand-off rebuilt its invoice from the seed's reorder_items, which
    carried the laser, so a $997 line reached Rae's invoice and the client's cart."""
    from biofield_local_app import create_app
    from dashboard.biofield_authoring import init_auth_tables, create_test, add_chain_row
    from dashboard import biofield_invoice
    import dashboard as _d
    monkeypatch.delenv("CONSOLE_SECRET", raising=False)
    monkeypatch.setattr(_d, "CONSOLE_SECRET", "", raising=False)
    db = str(tmp_path / "chat_log.db")
    cx = sqlite3.connect(db)
    init_auth_tables(cx)
    cx.execute("CREATE TABLE fmp_snap_products (product_name TEXT, doses_per_bottle INTEGER)")
    tid = create_test(cx, "Pat", "p@x.com", "2026-10-01")
    add_chain_row(cx, tid, 1, "Head", "Tail", "Liver Support", "1 cap", "daily", "")
    add_chain_row(cx, tid, 1, "Head", "Tail", "Harmony Laser", "", "", CAUTION)
    cx.commit()
    pushed = {}
    monkeypatch.setattr(biofield_invoice, "default_handoff_push",
                        lambda *a, **k: pushed.update(args=a, kw=k) or {"ok": True})
    captured = {}
    client = create_app(
        db,
        invoice_fetch_catalog=lambda: [{"name": "Liver Support", "slug": "liver-support"},
                                       {"name": "Harmony Laser", "slug": "harmony-laser"}],
        invoice_create=lambda c, lines, replace_open=False, invoice_note=None,
                       idempotency_key="": captured.update(lines=lines) or {"ok": True, "order_id": 5},
        invoice_paid_check=lambda email: {"paid": False},
    ).test_client()
    r = client.post("/author/%s/handoff" % tid, json={})
    j = r.get_json()
    assert j and j["ok"] is True, (r.status_code, r.data[:300])
    slugs = [l["slug"] for l in captured["lines"]]
    assert "liver-support" in slugs and "harmony-laser" not in slugs
    assert "harmony-laser" not in repr(pushed)
    assert "Harmony Laser" in repr(pushed)           # still on the report
