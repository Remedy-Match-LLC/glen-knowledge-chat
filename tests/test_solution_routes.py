"""Solution category routes: /solutions/ and /solutions/<slug>."""
import os

os.environ.setdefault("OPENAI_API_KEY", "sk-dummy")
os.environ.setdefault("PINECONE_API_KEY", "pc-dummy")

import json

import pytest

import app
from dashboard import solution_pages as sp

CAT = {
    "kloud-pemf-mini": {"name": "Kloud PEMF Mat (Mini)", "price_cents": 99900},
    "old-mat": {"name": "Old Mat", "inactive": True},
}
CATS = [
    {"slug": "pemf", "title": "PEMF", "principle": "Pulsed fields.", "choose_by_use": [],
     "products": ["kloud-pemf-mini", "old-mat"], "learn": ["cellular-energy"], "table_names": []},
    {"slug": "fasting", "title": "Fasting", "principle": "Rest.", "choose_by_use": [],
     "products": [], "learn": ["fasting"], "table_names": ["Fasting resources"]},
]
PUBLIC = {"cellular-energy": "Cellular Energy"}


@pytest.fixture()
def client(monkeypatch, tmp_path):
    f = tmp_path / "solution_categories.json"
    f.write_text(json.dumps({"categories": CATS}))
    monkeypatch.setattr(sp, "DATA_PATH", f)
    monkeypatch.setattr(app, "_get_product",
                        lambda s: dict(CAT[s], slug=s) if s in CAT else None)
    monkeypatch.setattr(app, "_solution_learn_name", lambda s: PUBLIC.get(s))
    return app.app.test_client()


def test_hub_lists_visible_categories_only(client):
    r = client.get("/solutions/")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert 'href="/solutions/pemf"' in html
    # Fasting has no product and its learn page is still pending, so it stays hidden.
    assert "/solutions/fasting" not in html


def test_bare_solutions_redirects_to_the_hub(client):
    r = client.get("/solutions")
    assert r.status_code in (301, 308)
    assert r.headers["Location"].endswith("/solutions/")


def test_category_page_shows_cards_and_learn_link_and_drops_inactive(client):
    r = client.get("/solutions/pemf")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert 'href="/begin/product/kloud-pemf-mini"' in html
    assert "/begin/product/old-mat" not in html
    assert 'href="/learn/cellular-energy"' in html


def test_unknown_slug_is_404(client):
    assert client.get("/solutions/no-such-thing").status_code == 404


def test_a_category_with_nothing_to_show_is_404(client):
    assert client.get("/solutions/fasting").status_code == 404


def test_a_missing_data_file_is_404_not_500(client, monkeypatch, tmp_path):
    monkeypatch.setattr(sp, "DATA_PATH", tmp_path / "gone.json")
    assert client.get("/solutions/").status_code == 404
    assert client.get("/solutions/pemf").status_code == 404
