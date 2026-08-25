import sqlite3

import app as app_module
import dashboard
from dashboard import client_prefs, client_prices, cart_store, points, subscriptions, client_portal


def test_commerce_status_reports_pricing_shipping_and_membership(monkeypatch, tmp_path):
    path = str(tmp_path / "commerce.db")
    monkeypatch.setattr(app_module, "LOG_DB", path)
    monkeypatch.setattr(app_module, "CONSOLE_SECRET", "sekret")
    monkeypatch.setattr(dashboard, "CONSOLE_SECRET", "sekret")
    with sqlite3.connect(path) as cx:
        client_prefs.init_table(cx)
        client_prices.init_table(cx)
        client_prefs.set_pickup_default(cx, "a@b.com", True)
        client_prices.set_ff_flat(cx, "a@b.com", 4200)
        app_module.init_membership_tables(cx)
        app_module._grant_membership(cx, "a@b.com", 30, "membership_month")
        cart_store.init_cart_tables(cx)
        cart_store.get_or_create(cx, "cart-a", email="a@b.com")
        cart_store.add_item(cx, "cart-a", "vitreous-vitality", qty=2)
        points.init_points_table(cx)
        points.earn(cx, "a@b.com", full_price_cents=10000, earn_pct=.05, order_ref="order-a")
        subscriptions.init_subscriptions_table(cx)
        client_portal.init_client_portal_table(cx)
        client_portal.upsert_portal(cx, "a@b.com", "A", {})
        cx.commit()

    response = app_module.app.test_client().get(
        "/api/console/client-commerce-status?email=A@B.COM&key=sekret")
    assert response.status_code == 200
    data = response.get_json()
    assert data["pricing"]["ff_flat_cents"] == 4200
    assert data["shipping"]["pickup_default"] is True
    assert data["membership"]["active"] is True
    assert data["membership"]["tier"] == "month"
    assert data["account_health"]["cart_count"] == 2
    assert data["account_health"]["points_cents"] == 500
    assert data["account_health"]["portal_active"] is True
    assert {t["key"] for t in data["membership_tiers"]} == {
        "month", "year_monthly", "year_prepay"}


def test_commerce_status_requires_owner_and_email(monkeypatch, tmp_path):
    monkeypatch.setattr(app_module, "LOG_DB", str(tmp_path / "commerce.db"))
    monkeypatch.setattr(app_module, "CONSOLE_SECRET", "sekret")
    monkeypatch.setattr(dashboard, "CONSOLE_SECRET", "sekret")
    client = app_module.app.test_client()
    assert client.get("/api/console/client-commerce-status?email=a@b.com&key=wrong").status_code == 401
    assert client.get("/api/console/client-commerce-status?key=sekret").status_code == 400
