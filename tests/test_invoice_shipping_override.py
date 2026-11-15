"""Manual shipping-fee override on the in-house invoice pricer.

Edit Invoice (and the order builder) can send `shipping_cents` to REPLACE the
geometry-computed shipping fee — for products whose box dims aren't in the catalog,
or to match a hand-quoted rate. This exercises the real `_price_inhouse_invoice`
branch with the rate engine (`_price_cart`) and catalog (`_get_product`) stubbed so
the test stays deterministic and secretless.
"""
import pytest

app_mod = pytest.importorskip("app")


@pytest.fixture
def stub_pricer(monkeypatch):
    # A single non-FF, shippable product priced by an explicit per-line override, so the
    # line math is trivial and the test isolates the shipping branch.
    monkeypatch.setattr(app_mod, "_get_product",
                        lambda slug: {"slug": "widget", "name": "Widget", "price_cents": 1000}
                        if slug == "widget" else None)
    # Geometry engine always quotes $7.00; GET absorbed at $0.
    monkeypatch.setattr(app_mod, "_price_cart",
                        lambda *a, **k: {"shipping_cents": 700, "priced": {"get_cents": 0}})
    # Keep membership/repertoire out of it.
    monkeypatch.setattr(app_mod, "_is_paid_member", lambda *a, **k: False)
    monkeypatch.setattr(app_mod, "_resolve_repertoire_slugs", lambda *a, **k: [])
    return app_mod


LINES = [{"slug": "widget", "qty": 1, "unit_cents": 1000}]
SHIP = {"name": "B", "street": "1 A St", "city": "Hilo", "state": "HI", "zip": "96720", "country": "US"}


def test_no_override_uses_computed_shipping(stub_pricer):
    p = stub_pricer._price_inhouse_invoice(LINES, email="", pickup=False, ship=SHIP)
    assert p["shipping_cents"] == 700
    assert p["total_cents"] == 1000 + 700


def test_override_replaces_computed_shipping(stub_pricer):
    p = stub_pricer._price_inhouse_invoice(
        LINES, email="", pickup=False, ship=SHIP, shipping_override_cents_in=1234)
    assert p["shipping_cents"] == 1234                 # replaces the $7.00 quote
    assert p["total_cents"] == 1000 + 1234


def test_override_zero_is_honored(stub_pricer):
    # $0.00 is a real choice (free shipping), not "blank" — it must stick.
    p = stub_pricer._price_inhouse_invoice(
        LINES, email="", pickup=False, ship=SHIP, shipping_override_cents_in=0)
    assert p["shipping_cents"] == 0
    assert p["total_cents"] == 1000


def test_blank_override_falls_back_to_computed(stub_pricer):
    for blank in (None, ""):
        p = stub_pricer._price_inhouse_invoice(
            LINES, email="", pickup=False, ship=SHIP, shipping_override_cents_in=blank)
        assert p["shipping_cents"] == 700, blank


def test_negative_override_clamped_to_zero(stub_pricer):
    p = stub_pricer._price_inhouse_invoice(
        LINES, email="", pickup=False, ship=SHIP, shipping_override_cents_in=-500)
    assert p["shipping_cents"] == 0


def test_pickup_forces_zero_over_override(stub_pricer):
    # Pickup means no shipment — an override must never resurrect a shipping charge.
    p = stub_pricer._price_inhouse_invoice(
        LINES, email="", pickup=True, ship=SHIP, shipping_override_cents_in=1234)
    assert p["shipping_cents"] == 0
    assert p["total_cents"] == 1000


def test_edit_route_persists_shipping_override(stub_pricer, monkeypatch, tmp_path):
    from dashboard import orders as O
    db_path = str(tmp_path / "orders.db")
    monkeypatch.setattr(stub_pricer, "LOG_DB", db_path)
    monkeypatch.setattr(
        stub_pricer, "_bos_actor",
        lambda: type("A", (), {"role": stub_pricer._bos_rbac.OWNER})())
    monkeypatch.setattr(
        stub_pricer, "_push_invoice_edit_to_qbo",
        lambda *a, **k: {"pushed": False})
    with O.db.connect(db_path) as cx:
        O.init_orders_table(cx)
        oid = O.upsert_order(
            cx, source="in-house", external_ref="INH-SHIP", email="s@x.com",
            items=[{"slug": "widget", "name": "Widget", "qty": 1,
                    "unit_cents": 1000, "line_cents": 1000}],
            total_cents=1700, shipping_cents=700,
            address=SHIP, channel="retail", status="proposed")
    r = stub_pricer.app.test_client().post(
        f"/api/orders/{oid}/edit",
        json={"lines": LINES, "shipping_cents": 1234, "pickup": False})
    assert r.status_code == 200, r.get_data(as_text=True)
    assert r.get_json()["totals"]["shipping_cents"] == 1234
    with O.db.connect(db_path) as cx:
        cx.row_factory = __import__("sqlite3").Row
        saved = O.get_order(cx, oid)
    assert saved["shipping_cents"] == 1234
    assert saved["total_cents"] == 2234


# ---- 2026-11-14: who set the figure (Glen: a customer edit recalculates) ----------

def test_a_typed_figure_that_differs_is_hand_set(stub_pricer):
    p = stub_pricer._price_inhouse_invoice(
        LINES, email="", pickup=False, ship=SHIP, shipping_override_cents_in=1234)
    assert p["shipping_hand_set"] is True and p["computed_shipping_cents"] == 700


def test_a_prefilled_figure_equal_to_the_rate_is_computed(stub_pricer):
    # Edit Invoice pre-fills the stored figure and sends it back on save.
    p = stub_pricer._price_inhouse_invoice(
        LINES, email="", pickup=False, ship=SHIP, shipping_override_cents_in=700)
    assert p["shipping_hand_set"] is False


def test_no_figure_and_pickup_are_never_hand_set(stub_pricer):
    assert stub_pricer._price_inhouse_invoice(
        LINES, email="", pickup=False, ship=SHIP)["shipping_hand_set"] is False
    assert stub_pricer._price_inhouse_invoice(
        LINES, email="", pickup=True, ship=SHIP,
        shipping_override_cents_in=1234)["shipping_hand_set"] is False


def test_a_rate_error_keeps_the_stored_figure_never_zero(stub_pricer, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("rates down")
    monkeypatch.setattr(stub_pricer, "_price_cart", boom)
    p = stub_pricer._price_inhouse_invoice(
        LINES, email="", pickup=False, ship=SHIP, shipping_fallback_cents_in=3200)
    assert p["shipping_cents"] == 3200
    p = stub_pricer._price_inhouse_invoice(
        LINES, email="", pickup=False, ship=SHIP, shipping_fallback_cents_in=3200)
    assert p["total_cents"] == 1000 + 3200


def test_a_typed_figure_equal_to_the_rate_is_still_hand_set(stub_pricer):
    p = stub_pricer._price_inhouse_invoice(
        LINES, email="", pickup=False, ship=SHIP, shipping_override_cents_in=700,
        shipping_typed=True)
    assert p["shipping_hand_set"] is True


def test_an_untyped_prefill_that_differs_is_not_hand_set(stub_pricer):
    # Edit Invoice sent the stored figure back untouched; the rates have moved since.
    p = stub_pricer._price_inhouse_invoice(
        LINES, email="", pickup=False, ship=SHIP, shipping_override_cents_in=3200,
        shipping_typed=False)
    assert p["shipping_hand_set"] is False and p["shipping_cents"] == 3200


def test_the_editor_reports_a_typed_figure():
    from pathlib import Path
    html = (Path(__file__).resolve().parent.parent / "static" / "order-new.html").read_text()
    assert html.count("body.shipping_typed = SHIP_TYPED;") == 2
    assert "SHIP_TYPED = !!o.shipping_hand_set;" in html
    assert "(o.shipping_cents || o.shipping_hand_set)" in html       # a typed $0 shows
    assert 'SHIP_TYPED=true; recalc(); });' in html


def _persist_spy(monkeypatch, app):
    seen = {}

    def fake_price(lines, **kw):
        seen["price"] = kw
        ship = kw.get("shipping_override_cents_in")
        return {"items_rec": [], "shipping_cents": ship if ship is not None else 6500,
                "get_cents": 0, "discount_cents": 0, "adjustment_cents": 0,
                "points_redeemed_cents": 0, "total_cents": 1000,
                "shipping_hand_set": False}
    monkeypatch.setattr(app, "_price_inhouse_invoice", fake_price)
    monkeypatch.setattr(app._bos_orders, "upsert_order",
                        lambda cx, **kw: seen.__setitem__("upsert", kw) or 1)
    monkeypatch.setattr(app, "_harvest_line_note_snippets", lambda *a, **k: None)
    return seen


MONA = {"id": 300, "source": "in-house", "external_ref": "M", "email": "", "items": [],
        "shipping_cents": 3300, "shipping_hand_set": 1, "channel": "retail"}


def test_a_recomputing_caller_keeps_a_typed_figure(stub_pricer, monkeypatch):
    seen = _persist_spy(monkeypatch, stub_pricer)
    stub_pricer._reprice_and_persist_invoice(None, MONA, LINES, pickup=False)
    assert seen["price"]["shipping_override_cents_in"] == 3300
    assert seen["upsert"]["shipping_hand_set"] is None          # flag left as it is


def test_clearing_the_field_in_edit_invoice_recomputes(stub_pricer, monkeypatch):
    seen = _persist_spy(monkeypatch, stub_pricer)
    stub_pricer._reprice_and_persist_invoice(None, MONA, LINES, pickup=False,
                                             recompute_shipping=True)
    assert seen["price"]["shipping_override_cents_in"] is None
    assert seen["upsert"]["shipping_hand_set"] is False


def _edit_route_order(stub_pricer, monkeypatch, tmp_path, **order_kw):
    from dashboard import orders as O
    db_path = str(tmp_path / "orders.db")
    monkeypatch.setattr(stub_pricer, "LOG_DB", db_path)
    monkeypatch.setattr(
        stub_pricer, "_bos_actor",
        lambda: type("A", (), {"role": stub_pricer._bos_rbac.OWNER})())
    monkeypatch.setattr(stub_pricer, "_push_invoice_edit_to_qbo",
                        lambda *a, **k: {"pushed": False})
    with O.db.connect(db_path) as cx:
        O.init_orders_table(cx)
        oid = O.upsert_order(
            cx, source="in-house", external_ref="INH-HS", email="s@x.com",
            items=[{"slug": "widget", "name": "Widget", "qty": 1,
                    "unit_cents": 1000, "line_cents": 1000}],
            total_cents=4300, shipping_cents=3300, shipping_hand_set=True,
            address=SHIP, channel="retail", status="proposed", **order_kw)

    def saved():
        with O.db.connect(db_path) as cx:
            cx.row_factory = __import__("sqlite3").Row
            return O.get_order(cx, oid)
    return oid, saved


def test_edit_route_blank_field_recomputes_a_typed_figure(stub_pricer, monkeypatch, tmp_path):
    oid, saved = _edit_route_order(stub_pricer, monkeypatch, tmp_path)
    r = stub_pricer.app.test_client().post(f"/api/orders/{oid}/edit",
                                           json={"lines": LINES, "pickup": False})
    assert r.status_code == 200, r.get_data(as_text=True)
    assert saved()["shipping_cents"] == 700 and saved()["shipping_hand_set"] == 0


def test_edit_route_records_a_typed_figure(stub_pricer, monkeypatch, tmp_path):
    oid, saved = _edit_route_order(stub_pricer, monkeypatch, tmp_path)
    stub_pricer.app.test_client().post(
        f"/api/orders/{oid}/edit",
        json={"lines": LINES, "pickup": False, "shipping_cents": 700, "shipping_typed": True})
    assert saved()["shipping_cents"] == 700 and saved()["shipping_hand_set"] == 1
    stub_pricer.app.test_client().post(
        f"/api/orders/{oid}/edit",
        json={"lines": LINES, "pickup": False, "shipping_cents": 3300,
              "shipping_typed": False})
    assert saved()["shipping_hand_set"] == 0
