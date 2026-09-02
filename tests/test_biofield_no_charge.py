"""A Biofield test the practitioner runs without charging for the analysis.

The three benefits a Biofield Analysis carries -- the immediate care window
(_grant_biofield_line_on_paid), the 30-day month from remedy delivery
(_extend_biofield_month_on_delivery) and the member pricing that follows from
holding either -- all key off a `biofield-analysis` line existing in a paid
order.  So withholding them is exactly "put no such line on the invoice", and
these tests pin that absence rather than any grant-side exclusion.
"""
import sqlite3

from biofield_local_app import create_app
from dashboard.biofield_authoring import (
    add_chain_row, create_test, get_no_charge, init_auth_tables, set_no_charge,
)
from dashboard.biofield_fee import build_fee_state
from dashboard.biofield_invoice import BIOFIELD_SLUG
from dashboard.biofield_report_html import render_fee_panel


def test_no_charge_defaults_off_and_persists_per_test(tmp_path):
    with sqlite3.connect(tmp_path / "x.db") as cx:
        init_auth_tables(cx)
        a = create_test(cx, "A", "a@x.com", "2026-09-02")
        b = create_test(cx, "B", "b@x.com", "2026-09-02")
        assert get_no_charge(cx, a) is False
        assert set_no_charge(cx, a, True) is True
        assert get_no_charge(cx, a) is True
        # Comping one analysis must not comp another client's.
        assert get_no_charge(cx, b) is False
        assert set_no_charge(cx, a, False) is False
        assert get_no_charge(cx, a) is False


def test_fee_panel_shows_the_toggle_and_its_state():
    state = build_fee_state("a@x.com", lambda e: {"available": True, "courtesy_cents": None})
    assert state["no_charge"] is False
    assert "id=fee_no_charge" in render_fee_panel(state)
    assert "id=fee_no_charge checked" not in render_fee_panel(state)

    state = build_fee_state("a@x.com", lambda e: {"available": True, "courtesy_cents": None},
                            no_charge=True)
    assert state["no_charge"] is True
    html = render_fee_panel(state)
    assert "id=fee_no_charge checked" in html
    assert "earns no membership or member pricing" in html


def _app(tmp_path, monkeypatch, created):
    monkeypatch.delenv("CONSOLE_SECRET", raising=False)
    import dashboard
    monkeypatch.setattr(dashboard, "CONSOLE_SECRET", "", raising=False)
    db = str(tmp_path / "chat_log.db")
    with sqlite3.connect(db) as cx:
        init_auth_tables(cx)
        tid = create_test(cx, "Pam", "pam@example.com", "2026-09-02")
        add_chain_row(cx, tid, 1, "Terrain", "Terrain", "Terrain Restore",
                      frequency="1 daily", confirmed=1, origin="live")

    def invoice_create(client, lines, **kw):
        created.append(lines)
        return {"ok": True, "order_id": 5150, "external_ref": "ref-5150", "total_cents": 0,
                "accepted_slugs": [l["slug"] for l in lines]}

    app = create_app(
        db,
        fetch_profile=lambda email: {},
        invoice_fetch_catalog=lambda: [{"name": "Terrain Restore",
                                        "slug": "terrain-restore"}],
        invoice_paid_check=lambda email: {"paid": False},
        invoice_create=invoice_create,
        invoice_latest=lambda email: {},
        invoice_link=lambda oid: {"ok": False},
    )
    return app.test_client(), tid


def test_a_normal_test_still_invoices_the_analysis(tmp_path, monkeypatch):
    created = []
    client, tid = _app(tmp_path, monkeypatch, created)
    body = client.post(f"/author/{tid}/invoice", json={}).get_json()
    assert body["ok"] is True
    assert BIOFIELD_SLUG in [l["slug"] for l in created[-1]]
    assert body.get("no_charge") is not True


def test_no_charge_leaves_the_analysis_off_the_invoice(tmp_path, monkeypatch):
    created = []
    client, tid = _app(tmp_path, monkeypatch, created)
    assert client.post(f"/author/{tid}/fee/no-charge",
                       json={"on": True}).get_json()["no_charge"] is True

    body = client.post(f"/author/{tid}/invoice", json={}).get_json()
    assert body["ok"] is True
    slugs = [l["slug"] for l in created[-1]]
    # THE assertion: no biofield-analysis line, so no order can ever grant the
    # care window, start the month on delivery, or begin member pricing.
    assert BIOFIELD_SLUG not in slugs
    assert slugs == ["terrain-restore"]
    # And it must not masquerade as "already paid" -- the client paid nothing.
    assert body["no_charge"] is True
    assert body["already_paid"] is False
    assert "already paid" not in (body.get("warning") or "").lower()
    assert "no membership or member pricing" in body["warning"]


def test_no_charge_with_no_remedies_raises_no_invoice_at_all(tmp_path, monkeypatch):
    created = []
    client, tid = _app(tmp_path, monkeypatch, created)
    with sqlite3.connect(str(tmp_path / "chat_log.db")) as cx:
        cx.execute("DELETE FROM biofield_auth_chain")
    client.post(f"/author/{tid}/fee/no-charge", json={"on": True})
    body = client.post(f"/author/{tid}/invoice", json={}).get_json()
    assert body["ok"] is True
    assert created == []
    assert body["no_charge"] is True
    assert "no charge" in (body.get("note") or "").lower()
