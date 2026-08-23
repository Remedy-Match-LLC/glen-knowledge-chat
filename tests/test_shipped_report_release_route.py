import pytest


@pytest.fixture
def app_module(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("PINECONE_API_KEY", "test-key")
    import app as appmod
    monkeypatch.setattr(appmod, "LOG_DB", str(tmp_path / "chat_log.db"))
    appmod._init_auth_tables()
    monkeypatch.setattr(appmod, "CONSOLE_SECRET", "test-secret")
    appmod.app.config["TESTING"] = True
    return appmod


def test_shipped_report_backfill_route_is_dry_run_by_default(app_module):
    from dashboard import orders
    from dashboard import portal_biofield_reports as reports

    with app_module.db.connect(app_module.LOG_DB) as cx:
        orders.init_orders_table(cx)
        oid = orders.upsert_order(
            cx, source="biofield", external_ref="symons-catchup", status="shipped",
            email="family@symons.test", name="Symons Family",
            items=[{"slug": "liver-support", "qty": 1}],
        )
        reports.init_table(cx)
        reports.upsert_report(cx, "family@symons.test", "2026-08-01", "s1",
                              {"layers": []}, "confirmed")

    client = app_module.app.test_client()
    response = client.post(
        "/api/console/portal/release-shipped-reports",
        headers={"X-Console-Key": "test-secret"}, json={},
    )
    assert response.status_code == 200
    body = response.get_json()
    assert body["committed"] is False
    assert body["released"] == 1


def test_shipped_report_backfill_route_requires_console_auth(app_module):
    response = app_module.app.test_client().post(
        "/api/console/portal/release-shipped-reports", json={}
    )
    assert response.status_code == 401
