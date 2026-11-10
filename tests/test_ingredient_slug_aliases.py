"""An old ingredient page address sends visitors to its new home (2026-11-10).

The phosphatides lines moved from 50% to 30% (PR #2251), which changed three ingredient
page addresses. Without an alias the old address loaded an empty page."""
import json
from pathlib import Path

import pytest

import app
from dashboard import ingredients as ing

ROOT = Path(__file__).resolve().parent.parent
LIVE = {"new-page": "New Page", "live-old": "Still Live"}
ALIASES = {"old-page": "new-page", "live-old": "new-page", "gone": "nowhere",
           "hop1": "hop2", "hop2": "new-page", "loop-a": "loop-b", "loop-b": "loop-a"}


@pytest.fixture
def fake(monkeypatch):
    monkeypatch.setattr(ing, "_name_index", lambda: LIVE)
    monkeypatch.setattr(ing, "_slug_aliases", lambda: ALIASES)


@pytest.mark.parametrize("slug,expect", [
    ("old-page", "new-page"),
    ("live-old", None),        # a live page is never hidden by an alias
    ("gone", None),            # a target that does not resolve is not used
    ("hop1", "new-page"),
    ("loop-a", None),
    ("new-page", None),
    ("", None),
])
def test_moved_to(fake, slug, expect):
    assert ing.moved_to(slug) == expect


def test_the_page_and_its_data_redirect(fake):
    c = app.app.test_client()
    r = c.get("/begin/ingredient/old-page?x=1")
    assert r.status_code == 302 and r.headers["Location"].endswith("/begin/ingredient/new-page?x=1")
    r = c.get("/begin/ingredient-page-data/old-page")
    assert r.status_code == 302 and r.headers["Location"].endswith("/begin/ingredient-page-data/new-page")


def test_an_unknown_slug_still_404s(fake):
    r = app.app.test_client().get("/begin/ingredient-page-data/gone")
    assert r.status_code == 404


def test_the_alias_file_parses_and_names_the_phosphatides_pages():
    data = json.loads((ROOT / "data" / "ingredient-slug-aliases.json").read_text())["aliases"]
    assert data["phosphatides-50-helianthus-annuus"] == "phosphatides-30-helianthus-annuus"
    assert all(k != v for k, v in data.items())
    ing._ALIAS_CACHE.clear()
    assert ing._slug_aliases() == data


def test_a_failed_read_is_not_cached(monkeypatch, tmp_path):
    ing._ALIAS_CACHE.clear()
    monkeypatch.setattr(ing, "_ALIASES", tmp_path / "missing.json")
    assert ing._slug_aliases() == {}
    (tmp_path / "missing.json").write_text('{"aliases": {"a": "b"}}')
    assert ing._slug_aliases() == {"a": "b"}
    ing._ALIAS_CACHE.clear()


@pytest.mark.parametrize("paid,state", [(False, "locked"), (True, None)])
def test_the_redirected_data_keeps_the_paid_gate(fake, monkeypatch, paid, state):
    monkeypatch.setattr(app, "_ingredient_paid_ok", lambda email: paid)
    r = app.app.test_client().get("/begin/ingredient-page-data/old-page", follow_redirects=True)
    body = r.get_json()
    assert body["slug"] == "new-page"
    if state:
        assert body["state"] == "locked" and "sections" not in body
    else:
        assert body["state"] != "locked"
