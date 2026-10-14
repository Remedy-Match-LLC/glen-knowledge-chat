"""A chat product link opens on the client host, where the funnel host's rm_ref cookie
cannot reach, so the link carries ?ref= and the product page keeps it (Glen, 2026-10-13).
The first-touch rule still holds: an existing referral is never replaced."""
import importlib
import sys
from pathlib import Path

import pytest


def _app(monkeypatch, tmp_path):
    root = Path(__file__).resolve().parent.parent
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    app = importlib.import_module("app")
    monkeypatch.setattr(app, "LOG_DB", str(tmp_path / "chat_log.db"))
    return app


def _slug(app):
    for slug, p in app._PRODUCTS["products"].items():
        got = app._get_product(slug)
        if got and got["slug"] == slug and not p.get("inactive"):
            return slug
    pytest.skip("no live product in the catalog")


def _ref_cookie(resp):
    return [c for c in resp.headers.getlist("Set-Cookie") if c.startswith("rm_ref=")]


def test_the_product_page_keeps_a_referral_from_the_link(monkeypatch, tmp_path):
    app = _app(monkeypatch, tmp_path)
    r = app.app.test_client().get(f"/begin/product/{_slug(app)}?ref=janedoe")
    assert r.status_code == 200
    assert [c.split(";")[0] for c in _ref_cookie(r)] == ["rm_ref=janedoe"]


def test_an_existing_referral_is_never_replaced(monkeypatch, tmp_path):
    app = _app(monkeypatch, tmp_path)
    client = app.app.test_client()
    client.set_cookie("rm_ref", "firsttouch")
    r = client.get(f"/begin/product/{_slug(app)}?ref=janedoe")
    assert r.status_code == 200
    assert _ref_cookie(r) == []


@pytest.mark.parametrize("query", ["", "?ref=bad%20slug!", "?ref="])
def test_no_valid_referral_writes_nothing(monkeypatch, tmp_path, query):
    app = _app(monkeypatch, tmp_path)
    r = app.app.test_client().get(f"/begin/product/{_slug(app)}{query}")
    assert r.status_code == 200
    assert _ref_cookie(r) == []
