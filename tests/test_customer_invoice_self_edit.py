"""Customer invoice editing preserves quoted pricing and exposes a safe catalog."""
import importlib
import sys
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parent.parent
HTML = (REPO / "static" / "invoice.html").read_text()


def _app():
    if str(REPO) not in sys.path:
        sys.path.insert(0, str(REPO))
    try:
        return importlib.import_module("app")
    except Exception as exc:
        pytest.skip(f"app import requires runtime secrets: {exc}")


def test_invoice_picker_uses_token_scoped_catalog():
    assert "fetch(API+'/products')" in HTML
    assert "fetch('/api/products?all=1')" not in HTML


def test_invoice_has_customer_packaging_control_and_posts_format():
    assert "Capsules only (refill)" in HTML
    assert "function setFormat(i,v)" in HTML
    assert "format:l.format||'bottle'" in HTML


def test_invoice_update_preserves_terms_and_owner_override(monkeypatch):
    appmod = _app()
    order = {
        "id": 165, "source": "in-house", "external_ref": "INH-DEBRA",
        "email": "chakamom1@gmail.com", "name": "Debra Herndon",
        "status": "proposed", "channel": "retail", "discount_cents": 4000,
        "adjustment_cents": -1000, "shipping_cents": 1319,
        "items": [
            {"slug": "iop-syntropy", "qty": 1, "unit_cents": 5000,
             "override": True, "note": "capsules", "source": "biofield"},
        ],
    }
    captured = {}
    monkeypatch.setattr(appmod, "_invoice_order_for_token", lambda _token: order)
    monkeypatch.setattr(appmod, "_get_product", lambda slug: {"name": slug, "price_cents": 6997})

    def fake_reprice(_cx, _order, lines, **kwargs):
        captured.update(lines=lines, kwargs=kwargs)
        return ({"items_rec": lines, "subtotal_cents": 10000,
                 "discount_cents": 4000, "adjustment_cents": -1000,
                 "shipping_cents": 1319, "get_cents": 0,
                 "points_redeemed_cents": 0, "total_cents": 6319}, False)

    monkeypatch.setattr(appmod, "_reprice_and_persist_invoice", fake_reprice)
    monkeypatch.setattr(appmod._bos_orders, "get_order", lambda _cx, _oid: order)
    monkeypatch.setattr(appmod, "_invoice_summary", lambda _order: {"total_cents": 6319})
    monkeypatch.setattr(appmod._inbox, "send_email", lambda *a, **k: None)
    response = appmod.app.test_client().post(
        "/api/invoice/token/update",
        json={"lines": [{"slug": "iop-syntropy", "qty": 2, "format": "refill"}]})

    assert response.status_code == 200
    assert captured["lines"] == [{"slug": "iop-syntropy", "qty": 2,
                                   "format": "refill", "unit_cents": 5000,
                                   "note": "capsules", "source": "biofield"}]
    assert captured["kwargs"]["discount_cents_in"] == 4000
    assert captured["kwargs"]["adjustment_cents_in"] == -1000
    assert captured["kwargs"]["shipping_override_cents_in"] == 1319


def test_invoice_catalog_is_token_scoped_and_customer_safe(monkeypatch):
    appmod = _app()
    monkeypatch.setattr(appmod, "_invoice_order_for_token",
                        lambda token: {"id": 165} if token == "good" else None)
    monkeypatch.setattr(appmod._bos_products, "catalog", lambda **_kwargs: [
        {"slug": "perfect-skin", "name": "Perfect Skin", "price_cents": 6997,
         "ingredients": ["private-noise"]},
    ])
    client = appmod.app.test_client()
    assert client.get("/api/invoice/bad/products").status_code == 404
    body = client.get("/api/invoice/good/products").get_json()
    assert body == {"ok": True, "products": [
        {"slug": "perfect-skin", "name": "Perfect Skin", "price_cents": 6997},
    ]}


def test_invoice_line_view_round_trips_refill_format():
    appmod = _app()
    view = appmod._invoice_line_view({
        "slug": "iop-syntropy", "name": "IOP Syntropy", "qty": 2,
        "unit_cents": 5000, "line_cents": 10000, "format": "refill"})
    assert view["format"] == "refill"
