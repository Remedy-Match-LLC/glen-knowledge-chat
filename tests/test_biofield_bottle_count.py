"""Glen, 2026-09-25: "a single bottle gets entered on the invoice unless we specify
otherwise, or change it on the invoice. We could add a field in the Biofield line items
for bottle count to enter on the invoice. default would be 1 for most every product."

Until now every Biofield path billed ceil(doses/day * 30 / doses_per_bottle). Peach
Goddard's order 198 got 3 Candida Cleanse from a titrated "up to six a day"; Debra
Herndon's order 164 got 3 IOP Syntropy. The count now comes from the line's own
Bottles field, default 1, on the Intake invoice, the handoff invoice and the portal
reorder basket alike."""
import sqlite3

from dashboard import biofield_invoice as bi
from dashboard import biofield_handoff as bh
from dashboard import biofield_portal_publish as bpp
from dashboard.biofield_authoring import (
    add_chain_row, authored_report, create_test, init_auth_tables, ordered_chain,
    update_chain_row)


def _fmp(cx):
    cx.execute("CREATE TABLE IF NOT EXISTS fmp_snap_products (product_name TEXT, dosage TEXT, "
               "dosage_freq TEXT, dosage_timing TEXT, doses_per_bottle TEXT)")
    cx.executemany("INSERT INTO fmp_snap_products VALUES (?,?,?,?,?)", [
        ("IOP Syntropy", "1 capsule", "3 times daily", "with food", "30"),
        ("Candida Cleanse", "1 capsule", "6 times a day", "", "60")])
    cx.commit()


def _chain(cx):
    init_auth_tables(cx)
    tid = create_test(cx, "Debra Herndon", "d@x.com", "2026-09-25")
    aid = f"a{tid}"
    add_chain_row(cx, aid, layer=1, head="Eye", most_affected="Glaucoma",
                  remedy="IOP Syntropy", dosage="1 capsule", frequency="3 times daily")
    add_chain_row(cx, aid, layer=2, head="Gut", most_affected="Candida",
                  remedy="Candida Cleanse", dosage="1 capsule", frequency="6 times a day")
    _fmp(cx)
    return aid


def test_line_bottles_defaults_to_one_and_honours_a_set_count():
    assert bi.line_bottles({}) == 1
    assert bi.line_bottles({"bottles": None}) == 1
    assert bi.line_bottles({"bottles": ""}) == 1
    assert bi.line_bottles({"bottles": 0}) == 1
    assert bi.line_bottles({"bottles": "abc"}) == 1
    assert bi.line_bottles({"bottles": 3}) == 3
    assert bi.line_bottles({"bottles": "2"}) == 2


def test_a_chain_row_stores_and_returns_its_bottle_count():
    cx = sqlite3.connect(":memory:")
    aid = _chain(cx)
    rows = ordered_chain(cx, aid)
    assert [r["bottles"] for r in rows] == [None, None]
    update_chain_row(cx, rows[1]["id"], bottles=2)
    assert [r["bottles"] for r in ordered_chain(cx, aid)] == [None, 2]


def test_handoff_invoice_bills_one_bottle_unless_set(tmp_path):
    db = str(tmp_path / "t.db")
    with sqlite3.connect(db) as cx:
        aid = _chain(cx)
        rid2 = ordered_chain(cx, aid)[1]["id"]
        rep = authored_report(cx, aid)
    got = {r["name"]: r["qty"] for r in bh.report_remedies_for_invoice(db, rep, bi.bottles_needed)}
    assert got == {"IOP Syntropy": 1, "Candida Cleanse": 1}     # was 3 and 3
    with sqlite3.connect(db) as cx:
        update_chain_row(cx, rid2, bottles=2)
        rep = authored_report(cx, aid)
    got = {r["name"]: r["qty"] for r in bh.report_remedies_for_invoice(db, rep, bi.bottles_needed)}
    assert got == {"IOP Syntropy": 1, "Candida Cleanse": 2}


def test_portal_reorder_basket_uses_the_same_count():
    cx = sqlite3.connect(":memory:")
    aid = _chain(cx)
    catalog = {"iop-syntropy": {"name": "IOP Syntropy"},
               "candida-cleanse": {"name": "Candida Cleanse"}}
    items = bpp.build_portal_content(cx, aid, special_price_cents=0,
                                     catalog=catalog)["content"]["reorder_items"]
    assert {i["slug"]: i["qty"] for i in items} == {"iop-syntropy": 1, "candida-cleanse": 1}
    update_chain_row(cx, ordered_chain(cx, aid)[0]["id"], bottles=3)
    items = bpp.build_portal_content(cx, aid, special_price_cents=0,
                                     catalog=catalog)["content"]["reorder_items"]
    assert {i["slug"]: i["qty"] for i in items} == {"iop-syntropy": 3, "candida-cleanse": 1}
