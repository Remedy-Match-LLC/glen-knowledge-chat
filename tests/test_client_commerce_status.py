import sqlite3

import app as app_module
import dashboard
from dashboard import client_prefs, client_prices


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
        cx.commit()

    response = app_module.app.test_client().get(
        "/api/console/client-commerce-status?email=A@B.COM&key=sekret")
    assert response.status_code == 200
    data = response.get_json()
    assert data["pricing"]["ff_flat_cents"] == 4200
    assert data["shipping"]["pickup_default"] is True
    assert data["membership"]["active"] is True
    assert data["membership"]["tier"] == "month"
    assert {t["key"] for t in data["membership_tiers"]} == {
        "month", "year_monthly", "year_prepay"}


def test_commerce_status_requires_owner_and_email(monkeypatch, tmp_path):
    monkeypatch.setattr(app_module, "LOG_DB", str(tmp_path / "commerce.db"))
    monkeypatch.setattr(app_module, "CONSOLE_SECRET", "sekret")
    monkeypatch.setattr(dashboard, "CONSOLE_SECRET", "sekret")
    client = app_module.app.test_client()
    assert client.get("/api/console/client-commerce-status?email=a@b.com&key=wrong").status_code == 401
    assert client.get("/api/console/client-commerce-status?key=sekret").status_code == 400
