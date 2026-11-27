"""Printable purchase orders: PO numbers continue FileMaker's, hand-made POs, the PDF.

Glen, 2026-11-26: make a PO in the console, tap a button, get a PDF to attach to a
supplier email. FileMaker's last PDFs were titled "EXPENSE #: 1076".
"""
import importlib
import io
import sqlite3
import sys
from pathlib import Path

import pytest


def _schema(cx):
    from dashboard.ingredient_catalog import init_ingredients_schema
    from dashboard.materials_catalog import init_materials_schema
    from dashboard.purchase_orders import init_purchase_orders_schema
    init_ingredients_schema(cx); init_materials_schema(cx); init_purchase_orders_schema(cx)


@pytest.fixture
def cx():
    cx = sqlite3.connect(":memory:")
    _schema(cx)
    cx.execute("INSERT INTO suppliers (company, address_street, address_city, address_province, "
               "address_postal_code, email, phone_business) VALUES "
               "('Lonza', '1 Lab Way', 'Walkersville', 'MD', '21793', 'orders@lonza.example', '301-555-0100')")
    cx.commit()
    yield cx
    cx.close()


def _sid(cx):
    return cx.execute("SELECT id FROM suppliers WHERE company='Lonza'").fetchone()[0]


LINE = {"label": "Capsule shells size 0", "qty": 5, "qty_unit": "kg", "cost": 12.5}


# ── numbering ────────────────────────────────────────────────────────────────

def test_floor_is_the_live_filemaker_high():
    from dashboard import purchase_orders as po
    assert po.FMP_LAST_PO_NUMBER == 1119


def test_imported_po_keeps_its_filemaker_number(cx):
    from dashboard import purchase_orders as po
    cx.execute("INSERT INTO purchase_orders (fmp_id, vendor_po_no) VALUES ('1076', 'X')"); cx.commit()
    pid = cx.execute("SELECT id FROM purchase_orders WHERE fmp_id='1076'").fetchone()[0]
    assert po.assign_po_number(cx, pid) == 1076
    assert cx.execute("SELECT po_number FROM purchase_orders WHERE id=?", (pid,)).fetchone()[0] == 1076


def test_new_po_gets_1120_when_nothing_is_higher(cx):
    from dashboard import purchase_orders as po
    cx.execute("INSERT INTO purchase_orders (fmp_id) VALUES ('1110')"); cx.commit()
    pid = po.create_po(cx, _sid(cx), [LINE])
    assert po.assign_po_number(cx, pid) == 1120


def test_new_po_goes_past_a_higher_fmp_id(cx):
    from dashboard import purchase_orders as po
    cx.execute("INSERT INTO purchase_orders (fmp_id) VALUES ('1200')"); cx.commit()
    pid = po.create_po(cx, _sid(cx), [LINE])
    assert po.assign_po_number(cx, pid) == 1201


def test_new_po_goes_past_a_higher_fmp_id_and_po_number(cx):
    from dashboard import purchase_orders as po
    cx.execute("INSERT INTO purchase_orders (fmp_id) VALUES ('1200')")
    cx.execute("INSERT INTO purchase_orders (fmp_id, po_number) VALUES ('abc', 1300)"); cx.commit()
    pid = po.create_po(cx, _sid(cx), [LINE])
    assert po.assign_po_number(cx, pid) == 1301


def test_assign_is_idempotent_and_two_pos_differ(cx):
    from dashboard import purchase_orders as po
    a = po.create_po(cx, _sid(cx), [LINE])
    b = po.create_po(cx, _sid(cx), [LINE])
    na = po.assign_po_number(cx, a)
    assert po.assign_po_number(cx, a) == na
    nb = po.assign_po_number(cx, b)
    assert {na, nb} == {1120, 1121}


def test_lost_race_retries_with_the_next_number(cx, monkeypatch):
    """A concurrent caller took 1120 between our read and our write."""
    from dashboard import purchase_orders as po
    other = po.create_po(cx, _sid(cx), [LINE])
    mine = po.create_po(cx, _sid(cx), [LINE])
    real = po._next_po_number
    calls = {"n": 0}

    def stale(c):
        calls["n"] += 1
        if calls["n"] == 1:
            cx.execute("UPDATE purchase_orders SET po_number=1120 WHERE id=?", (other,)); cx.commit()
            return 1120
        return real(c)
    monkeypatch.setattr(po, "_next_po_number", stale)
    assert po.assign_po_number(cx, mine) == 1121


def test_unique_index_refuses_a_duplicate_number(cx):
    cx.execute("INSERT INTO purchase_orders (po_number) VALUES (1500)")
    cx.commit()
    with pytest.raises(sqlite3.IntegrityError):
        cx.execute("INSERT INTO purchase_orders (po_number) VALUES (1500)")


def test_schema_init_is_idempotent(cx):
    _schema(cx); _schema(cx)
    cols = [r[1] for r in cx.execute("PRAGMA table_info(purchase_orders)")]
    assert cols.count("po_number") == 1


def test_missing_po_raises_lookup(cx):
    from dashboard import purchase_orders as po
    with pytest.raises(LookupError):
        po.assign_po_number(cx, 99999)


# ── create_po validation ─────────────────────────────────────────────────────

def test_create_po_stores_draft_and_lines(cx):
    from dashboard import purchase_orders as po
    pid = po.create_po(cx, _sid(cx), [LINE, {"label": "Jars", "qty": "200", "qty_unit": "ea"}],
                       notes="n", po_date="2026-11-26")
    hdr = cx.execute("SELECT status, supplier_name, po_date, po_number, notes FROM purchase_orders "
                     "WHERE id=?", (pid,)).fetchone()
    assert hdr == ("draft", "Lonza", "2026-11-26", None, "n")
    rows = cx.execute("SELECT item_label, qty, qty_unit, cost, item_kind FROM po_items WHERE po_id=? "
                      "ORDER BY id", (pid,)).fetchall()
    assert rows == [("Capsule shells size 0", 5.0, "kg", 12.5, "other"), ("Jars", 200.0, "ea", None, "other")]


@pytest.mark.parametrize("sid, lines, msg", [
    (424242, [LINE], "supplier not found"),
    (None, [LINE], "pick a supplier"),
    ("S", [], "at least one line"),
    ("S", [{"label": "x", "qty": 0}], "more than 0"),
    ("S", [{"label": "x", "qty": -2}], "more than 0"),
    ("S", [{"label": "x", "qty": "lots"}], "must be a number"),
    ("S", [{"label": "  ", "qty": 1}], "description is required"),
    ("S", [{"label": "x", "qty": 1, "cost": -1}], "cannot be negative"),
    ("S", [{"label": "x", "qty": "1e309"}], "too large"),
    ("S", [{"label": "x", "qty": "nan"}], "too large"),
    ("S", [{"label": "x", "qty": 1, "cost": "NaN"}], "too large"),
    ("S", [{"label": "x", "qty": 1, "cost": "Infinity"}], "too large"),
    ("S", [{"label": "x", "qty": 1, "material_id": "abc"}], "bad material"),
    ("S", {"label": "x"}, "must be a list"),
    (2**40, [LINE], "pick a supplier"),
])
def test_create_po_validation(cx, sid, lines, msg):
    from dashboard import purchase_orders as po
    with pytest.raises(ValueError, match=msg):
        po.create_po(cx, _sid(cx) if sid == "S" else sid, lines)
    assert cx.execute("SELECT COUNT(*) FROM purchase_orders").fetchone()[0] == 0


def test_replace_lines_only_on_a_draft(cx):
    from dashboard import purchase_orders as po
    pid = po.create_po(cx, _sid(cx), [LINE])
    assert po.replace_draft_lines(cx, pid, [{"label": "New", "qty": 2}]) == 1
    assert cx.execute("SELECT item_label FROM po_items WHERE po_id=?", (pid,)).fetchall() == [("New",)]
    cx.execute("UPDATE purchase_orders SET status='posted' WHERE id=?", (pid,)); cx.commit()
    with pytest.raises(ValueError, match="only a draft"):
        po.replace_draft_lines(cx, pid, [{"label": "Other", "qty": 2}])
    assert cx.execute("SELECT item_label FROM po_items WHERE po_id=?", (pid,)).fetchall() == [("New",)]


# ── PDF renderer ─────────────────────────────────────────────────────────────

def _text(pdf_bytes):
    pypdf = pytest.importorskip("pypdf")
    return "\n".join(p.extract_text() for p in pypdf.PdfReader(io.BytesIO(pdf_bytes)).pages)


def test_blank_costs_print_no_totals():
    from dashboard import po_pdf
    po_row = {"po_number": 1120, "supplier_name": "Lonza", "po_date": "2026-11-26"}
    items = [{"item_label": "Priced", "qty": 2, "cost": 3}, {"item_label": "Unpriced", "qty": 1, "cost": None}]
    assert po_pdf.totals_rows(po_row, items) == []
    t = _text(po_pdf.render_po_pdf(po_row, items, None, po_pdf.PO_COMPANY))
    assert "$0.00" not in t and "Total" not in t and "$6.00" in t


def test_totals_when_every_line_is_priced():
    from dashboard import po_pdf
    rows = po_pdf.totals_rows({"shipping_amount": 10, "tax": None},
                              [{"qty": 2, "cost": 3}, {"qty": 1, "cost": 4}])
    assert rows == [("Subtotal", "$10.00"), ("Shipping", "$10.00"), ("Total", "$20.00")]


def test_filename():
    from dashboard import po_pdf
    assert po_pdf.filename({"po_number": 1120, "supplier_company": "Lonza"}) == "PO 1120 Lonza.pdf"
    assert po_pdf.filename({"po_number": 7, "supplier_name": 'A/B "C"'}) == "PO 7 AB C.pdf"


def test_company_block_and_no_em_dash():
    from dashboard import po_pdf
    src = (Path(__file__).resolve().parents[1] / "dashboard" / "po_pdf.py").read_text()
    assert chr(0x2014) not in src
    assert po_pdf.PO_COMPANY["phone"] == "(808) 217-9647"


# ── routes ───────────────────────────────────────────────────────────────────

def _client(tmp_path, monkeypatch, secret=""):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    db = str(tmp_path / "chat_log.db")
    with sqlite3.connect(db) as c:
        _schema(c)
        c.execute("INSERT INTO suppliers (company, address_city, email, phone_business) "
                  "VALUES ('Lonza', 'Walkersville', 'orders@lonza.example', '301-555-0100')")
        c.execute("INSERT INTO purchase_orders (fmp_id, vendor_po_no) VALUES ('1110', 'PO-1110')")
        c.commit()
    monkeypatch.delenv("CONSOLE_SECRET", raising=False)
    import dashboard as _d
    monkeypatch.setattr(_d, "CONSOLE_SECRET", secret, raising=False)
    repo = Path(__file__).resolve().parent.parent
    if str(repo) not in sys.path:
        sys.path.insert(0, str(repo))
    try:
        import app as appmod
        importlib.reload(appmod)
    except ImportError as e:
        # Only a missing optional dependency skips; any other startup error fails.
        pytest.skip(f"app not importable: {e}")
    with sqlite3.connect(db) as c:
        sid = c.execute("SELECT id FROM suppliers WHERE company='Lonza'").fetchone()[0]
    return appmod.app.test_client(), sid


def test_create_then_pdf_route(tmp_path, monkeypatch):
    c, sid = _client(tmp_path, monkeypatch)
    r = c.post("/api/po", json={"supplier_id": sid, "lines": [LINE]})
    assert r.status_code == 200, r.get_json()
    pid = r.get_json()["data"]["id"]
    r = c.post(f"/api/po/{pid}/pdf")
    assert r.status_code == 200 and r.mimetype == "application/pdf"
    assert r.headers["Content-Disposition"] == 'attachment; filename="PO 1120 Lonza.pdf"'
    t = _text(r.data)
    for want in ("Purchase Order", "PO 1120", "Lonza", "Capsule shells size 0", "(808) 217-9647",
                 "$12.50", "$62.50", "Ship to"):
        assert want in t, want
    assert "EXPENSE" not in t.upper()
    # Same number on a second press, and the panel can read it back.
    r2 = c.post(f"/api/po/{pid}/pdf")
    assert r2.headers["X-PO-Number"] == "1120"
    assert c.get(f"/api/po/{pid}").get_json()["data"]["po"]["po_number"] == 1120
    assert c.get("/api/po/search?q=1120").get_json()["data"][0]["id"] == pid


def test_imported_po_pdf_keeps_fmp_number(tmp_path, monkeypatch):
    c, _ = _client(tmp_path, monkeypatch)
    pid = c.get("/api/po/search?q=PO-1110").get_json()["data"][0]["id"]
    r = c.post(f"/api/po/{pid}/pdf")
    assert r.status_code == 200 and r.headers["X-PO-Number"] == "1110"


def test_pdf_route_404(tmp_path, monkeypatch):
    c, _ = _client(tmp_path, monkeypatch)
    assert c.post("/api/po/99999/pdf").status_code == 404


def test_create_route_400_on_bad_input(tmp_path, monkeypatch):
    c, sid = _client(tmp_path, monkeypatch)
    r = c.post("/api/po", json={"supplier_id": sid, "lines": []})
    assert r.status_code == 400 and "at least one line" in r.get_json()["error"]


def test_lines_route_drafts_only(tmp_path, monkeypatch):
    c, sid = _client(tmp_path, monkeypatch)
    pid = c.post("/api/po", json={"supplier_id": sid, "lines": [LINE]}).get_json()["data"]["id"]
    assert c.put(f"/api/po/{pid}/lines", json={"lines": [{"label": "B", "qty": 1}]}).status_code == 200
    imported = c.get("/api/po/search?q=PO-1110").get_json()["data"][0]["id"]
    assert c.put(f"/api/po/{imported}/lines", json={"lines": [{"label": "B", "qty": 1}]}).status_code == 400


def test_console_key_required(tmp_path, monkeypatch):
    c, sid = _client(tmp_path, monkeypatch, secret="ci-fake-console-secret")
    assert c.post("/api/po", json={"supplier_id": sid, "lines": [LINE]}).status_code == 401
    assert c.post("/api/po/1/pdf").status_code == 401
    assert c.put("/api/po/1/lines", json={"lines": [LINE]}).status_code == 401
    ok = c.post("/api/po", json={"supplier_id": sid, "lines": [LINE]},
                headers={"X-Console-Key": "ci-fake-console-secret"})
    assert ok.status_code == 200


def test_console_has_make_pdf_and_new_po_form():
    html = (Path(__file__).resolve().parents[1] / "static" / "admin-ingredients.html").read_text()
    assert 'onclick="makePoPdf()"' in html and '"/api/po/" + id + "/pdf"' in html
    assert 'id="new-po-form"' in html and 'api("/api/po", {' in html


def test_header_is_the_po_box_and_goods_ship_to_wailuku_drive():
    # Glen, platform tab, 2026-11-26: "yes, PO Box header with Wailuku ship-to".
    from dashboard import po_pdf
    po_row = {"po_number": 1120, "supplier_name": "Lonza", "po_date": "2026-11-26"}
    t = _text(po_pdf.render_po_pdf(po_row, [{"item_label": "Pullulan 00", "qty": 1,
                                              "cost": 1}]))
    head, _, ship = t.partition("Ship to")
    assert "PO Box 126" in head and "Hilo, HI 96721" in head
    assert "support@remedymatch.com" in head.lower()
    assert "351 Wailuku Drive" in ship and "96720" in ship
    assert "PO Box" not in ship
    assert t.count("(808) 217-9647") >= 1
    assert "Wailuku" not in head



# ---- review rounds 1 to 3 ------------------------------------------------------------

def test_a_printed_po_cannot_have_its_lines_changed(cx):
    from dashboard import purchase_orders as po
    pid = po.create_po(cx, _sid(cx), [LINE])
    n = po.assign_po_number(cx, pid)
    with pytest.raises(ValueError, match=f"PO {n} has been printed"):
        po.replace_draft_lines(cx, pid, [{"label": "Other", "qty": 2}])
    assert cx.execute("SELECT item_label FROM po_items WHERE po_id=?",
                      (pid,)).fetchall() == [("Capsule shells size 0",)]


def test_unit_cost_is_stored_as_printed(cx):
    from dashboard import purchase_orders as po
    pid = po.create_po(cx, _sid(cx), [{"label": "x", "qty": 1000, "cost": "0.33333"}])
    assert cx.execute("SELECT cost FROM po_items WHERE po_id=?", (pid,)).fetchone()[0] == 0.3333


def test_subtotal_is_the_sum_of_the_printed_lines():
    from dashboard import po_pdf
    items = [{"qty": 3, "cost": 1.115}, {"qty": 1, "cost": 2.675}]
    assert [po_pdf._money(po_pdf._amount(i)) for i in items] == ["$3.35", "$2.68"]
    assert po_pdf.totals_rows({}, items) == [("Subtotal", "$6.03"), ("Total", "$6.03")]


def test_shipping_alone_never_prints_under_an_unpriced_order():
    from dashboard import po_pdf
    rows = po_pdf.totals_rows({"shipping_amount": 10, "tax": 2},
                              [{"qty": 1, "cost": 3}, {"qty": 1, "cost": None}])
    assert rows == []


def test_a_reorder_line_prices_by_the_pack():
    # Reorder drafts store a pack price with the quantity in grams (dashboard/reorder.py).
    import json
    from dashboard import po_pdf
    it = {"item_label": "Ashwagandha", "qty": 1000.0, "qty_unit": "g", "cost": 334.0,
          "extras": json.dumps({"unit_size": 1000.0, "packs": 1, "est_cost": 334.0})}
    assert po_pdf._amount(it) == 334
    assert po_pdf._qty_text(it) == "1 \u00d7 1,000 g"
    assert po_pdf.totals_rows({}, [it])[-1] == ("Total", "$334.00")
    t = _text(po_pdf.render_po_pdf({"po_number": 1120, "supplier_name": "Lonza",
                                    "po_date": "2026-11-26"}, [it]))
    assert "$334,000" not in t and "$334.00" in t


def test_a_filemaker_date_prints_as_its_own_date():
    from dashboard import po_pdf
    assert po_pdf.po_date_text("11/26/2025") == "Nov 26, 2025"
    assert po_pdf.po_date_text("2025-11-26") == "Nov 26, 2025"
