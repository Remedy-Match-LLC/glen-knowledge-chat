import app as app_module


def test_console_client_page_served():
    app_module.app.config["TESTING"] = True
    c = app_module.app.test_client()
    r = c.get("/console/client")
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "client-360" in body            # the page fetches the bundle endpoint
    assert "op-nav.js" in body
    assert "no-store" in r.headers.get("Cache-Control", "")


def test_client_picker_includes_portal_only_clients_and_opens_portal():
    body = app_module.STATIC.joinpath("console-client.html").read_text()
    assert "/api/console/client-search" in body
    assert "Open portal" in body
    assert 'searchParams.set("from","console")' in body
    assert 'href=\'/console/client?email="+encodeURIComponent(p.email)+"&key=' not in body


def test_client_page_exposes_editable_commerce_status():
    body = app_module.STATIC.joinpath("console-client.html").read_text()
    assert "Product pricing" in body
    assert "Shipping" in body
    assert "Membership" in body
    assert "/api/console/client-commerce-status" in body
    assert "/api/console/client-prices" in body
    assert "/api/console/client-prefs" in body
    assert "/api/console/membership/enroll" in body
    assert "/api/console/membership/revoke" in body
    assert 'id="account-health"' in body
    assert "Address needed" in body
