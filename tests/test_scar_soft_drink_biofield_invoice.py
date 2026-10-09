"""Review round 3, F2 (2026-10-09). A Biofield report that lists a product not ready to
order keeps its name and page link. It adds nothing to an invoice."""
import sqlite3

import pytest

import app
from biofield_local_app import create_app
from dashboard import biofield_invoice

SCAR = "scar-soft-drink"
CATALOG = [{"name": "Liver Support", "slug": "liver-support", "waitlist_only": False},
           {"name": "Scar Soft Drink", "slug": SCAR, "waitlist_only": True}]


@pytest.fixture(autouse=True)
def _no_console_gate(monkeypatch):
    monkeypatch.delenv("CONSOLE_SECRET", raising=False)
    import dashboard as _d
    monkeypatch.setattr(_d, "CONSOLE_SECRET", "", raising=False)


def test_invoice_lines_leave_a_not_ready_remedy_off():
    built = biofield_invoice.build_invoice_lines(
        {}, [{"name": "Liver Support", "qty": 2}, {"name": "Scar Soft Drink", "qty": 1}], CATALOG)
    assert [l["slug"] for l in built["lines"]] == ["biofield-analysis", "liver-support"]
    assert built["not_ready"] == ["Scar Soft Drink"] and built["skipped"] == []


def test_console_catalog_marks_scar_and_a_bundle_holding_it(monkeypatch):
    # Its own catalog: the full CI run reaches this test after others have replaced
    # app's in-memory products (it passed alone and failed only in the suite).
    products = {
        SCAR: {"name": "Scar Soft Drink", "waitlist_only": True},
        "scar-silk": {"name": "Scar Silk"},
        "scar-reduction-program": {"name": "Scar Support Program", "bundle": True,
                                   "bundle_component_slugs": [{"slug": "scar-silk", "qty": 1}]},
        "scar-core-test": {"name": "Core", "bundle": True,
                           "bundle_component_slugs": [{"slug": SCAR, "qty": 1}]},
    }
    monkeypatch.setattr(app, "_PRODUCTS", {"products": products})
    monkeypatch.setattr(app._bos_products, "catalog",
                        lambda **kw: [dict(v, slug=k, price_cents=7000) for k, v in products.items()])
    monkeypatch.setattr(app, "_portal_console_ok", lambda: True)
    rows = {p["slug"]: p for p in
            app.app.test_client().get("/api/console/biofield-portal/catalog").get_json()["products"]}
    assert rows[SCAR]["waitlist_only"] is True
    assert rows["scar-silk"]["waitlist_only"] is False
    assert rows["scar-reduction-program"]["waitlist_only"] is False
    assert rows["scar-core-test"]["waitlist_only"] is True


def test_handoff_invoice_leaves_scar_off_and_the_portal_keeps_its_name(tmp_path, monkeypatch):
    from dashboard.biofield_authoring import init_auth_tables, create_test, add_chain_row
    db = str(tmp_path / "chat_log.db")
    cx = sqlite3.connect(db)
    init_auth_tables(cx)
    tid = create_test(cx, "Pt", "pt@x.com", "2026-10-09")
    add_chain_row(cx, tid, 1, "Head", "Tail", "Liver Support", "1 cap", "daily", "")
    add_chain_row(cx, tid, 2, "Scar", "Tail", "Scar Soft Drink", "1 cup", "daily", "")
    cx.commit()
    pushed, captured = {}, {}
    monkeypatch.setattr(biofield_invoice, "default_handoff_push",
                        lambda email, name, content, scan_date="": pushed.update(content) or {"ok": True})

    def fake_create(cust, lines, replace_open=False, invoice_note=None, idempotency_key=""):
        captured["lines"] = lines
        return {"ok": True, "order_id": 9, "total_cents": 1, "external_ref": "INH"}
    client = create_app(db, invoice_fetch_catalog=lambda: CATALOG, invoice_create=fake_create,
                        invoice_latest=lambda email: {"ok": False}).test_client()
    j = client.post("/author/%s/handoff" % tid, json={}).get_json()
    assert j["ok"] is True and j["invoice"]["ok"] is True, j
    assert [l["slug"] for l in captured["lines"]] == ["biofield-analysis", "liver-support"]
    assert j["invoice"]["not_ready"] == ["Scar Soft Drink"]
    # The report still names it: the layer text and the reorder row the portal shows
    # as name plus page link.
    assert any("Scar Soft Drink" in L["remedy"] for L in pushed["layers"])
    assert SCAR in [i["slug"] for i in pushed["reorder_items"]]
