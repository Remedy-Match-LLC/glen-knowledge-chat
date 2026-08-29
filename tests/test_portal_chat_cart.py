import importlib
import sqlite3

import pytest


@pytest.fixture()
def app_client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test")
    monkeypatch.setenv("PINECONE_API_KEY", "test")
    monkeypatch.setenv("PINECONE_INDEX", "test")
    import app
    importlib.reload(app)
    app.app.config["TESTING"] = True
    return app, app.app.test_client()


def test_explicit_portal_chat_order_persists_and_streams_cart_event(app_client, monkeypatch):
    app, client = app_client
    from dashboard import client_portal

    with sqlite3.connect(app.LOG_DB) as cx:
        client_portal.init_client_portal_table(cx)
        token, _ = client_portal.upsert_portal(
            cx, email="buyer@example.com", name="Buyer", content={})

    monkeypatch.setattr(app, "_PORTAL_CART_ENABLED", True)
    monkeypatch.setattr(app, "_PRODUCTS", {"products": {
        "brain-boost": {"name": "Brain Boost", "price_cents": 5000},
        "man-manna": {"name": "Man Manna", "price_cents": 5000},
    }})
    monkeypatch.setattr(app, "embed", lambda _q: (_ for _ in ()).throw(RuntimeError("off")))

    class BrokenMessages:
        def stream(self, **_kwargs):
            raise RuntimeError("off")

    class BrokenClient:
        messages = BrokenMessages()

    monkeypatch.setattr(app, "_cl", BrokenClient())
    response = client.post(
        f"/api/portal/{token}/chat",
        json={"query": "Add two Brain Boost and one Man Manna to my order"})
    body = response.data.decode()
    assert response.status_code == 200
    assert '"cart_added"' in body

    with sqlite3.connect(app.LOG_DB) as cx:
        rows = cx.execute(
            "SELECT ci.slug,ci.qty FROM cart_items ci JOIN carts c ON c.token=ci.token "
            "WHERE c.email=? ORDER BY ci.slug", ("buyer@example.com",)).fetchall()
    assert rows == [("brain-boost", 2), ("man-manna", 1)]


def test_portal_chat_question_does_not_create_cart(app_client, monkeypatch):
    app, client = app_client
    from dashboard import client_portal
    with sqlite3.connect(app.LOG_DB) as cx:
        client_portal.init_client_portal_table(cx)
        token, _ = client_portal.upsert_portal(
            cx, email="question@example.com", name="Q", content={})
    monkeypatch.setattr(app, "_PORTAL_CART_ENABLED", True)
    monkeypatch.setattr(app, "_PRODUCTS", {"products": {
        "brain-boost": {"name": "Brain Boost", "price_cents": 5000}}})

    class BrokenMessages:
        def stream(self, **_kwargs):
            raise RuntimeError("off")
    class BrokenClient:
        messages = BrokenMessages()
    monkeypatch.setattr(app, "_cl", BrokenClient())
    monkeypatch.setattr(app, "embed", lambda _q: (_ for _ in ()).throw(RuntimeError("off")))
    client.post(f"/api/portal/{token}/chat", json={"query": "Do I need Brain Boost?"})
    with sqlite3.connect(app.LOG_DB) as cx:
        exists = cx.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='carts'").fetchone()
        count = cx.execute("SELECT COUNT(*) FROM carts").fetchone()[0] if exists else 0
    assert count == 0


def test_practitioner_chat_adds_to_persistent_wholesale_cart(app_client, monkeypatch):
    app, client = app_client
    saved = {}
    catalog = [{"slug": "brain-boost", "name": "Brain Boost", "description": ""}]
    monkeypatch.setattr(app, "_practitioner_session_pid", lambda: "prac-1")
    monkeypatch.setattr(app, "_build_ff_catalog", lambda: catalog)
    monkeypatch.setattr(app._chat, "scoped_reply",
                        lambda *_a, **_k: {"reply": "Done.", "suggested_slugs": []})
    monkeypatch.setattr(app._pp, "cart_items", lambda _pid: [{"slug": "brain-boost", "qty": 1}])
    monkeypatch.setattr(app._pp, "is_orderable", lambda _slug: True)
    monkeypatch.setattr(app._pp, "cart_set",
                        lambda pid, slug, qty: saved.update(pid=pid, slug=slug, qty=qty))
    response = client.post("/api/practitioner/chat", json={
        "token": "session", "message": "Add two Brain Boost to my order"})
    assert response.status_code == 200
    assert saved == {"pid": "prac-1", "slug": "brain-boost", "qty": 3}
    assert response.get_json()["cart_added"] == [{
        "slug": "brain-boost", "name": "Brain Boost", "qty": 2, "basket_qty": 3}]
