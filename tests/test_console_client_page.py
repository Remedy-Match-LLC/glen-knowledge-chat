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
