"""A service's product page is not a remedy's page.

Marketing, 2026-09-22, on /begin/product/evox-session (EVOX Session, service=true):
it said "Your remedy", teased "The research" as "Studies behind the key ingredients",
and "Dr. Glen recommends" offered ED10 Skin Driver. Fixed for every service-flagged
product. (An empty-looking Overview is NOT dropped here: it may be filled later from
the generated-copy cache or streamed when opened, and the filter runs before both.)
"""
import pytest

from dashboard.product_page_sections import filter_sections


def _secs(desc="text"):
    return [{"id": "intro", "body": "x"}, {"id": "description", "body": desc},
            {"id": "research", "body": {}}, {"id": "cta", "body": {}}]


def test_a_service_loses_the_research_section():
    ids = [s["id"] for s in filter_sections(_secs(), has_ingredients=False,
                                            has_own_video=False, is_service=True)]
    assert "research" not in ids and "cta" in ids


def test_a_formula_keeps_it():
    ids = [s["id"] for s in filter_sections(_secs(), has_ingredients=True, has_own_video=False)]
    assert "research" in ids


@pytest.fixture
def page(monkeypatch, tmp_path):
    import app as appmod
    from dashboard import related_products as rp, related_store as rs
    monkeypatch.setattr(appmod, "LOG_DB", str(tmp_path / "chat_log.db"))
    monkeypatch.setattr(appmod, "_RELATED_PRODUCTS_ENABLED", True)
    monkeypatch.setattr(rs, "load_manual", lambda slug: [])
    monkeypatch.setattr(rs, "load_harvested", lambda slug: [])
    monkeypatch.setattr(appmod, "_related_semantic", lambda slug: [])
    monkeypatch.setattr(rp, "resolve_related", lambda slug, **k: {
        "featured": ["microbiome"], "more": [], "reasons": {}})
    appmod.app.config["TESTING"] = True
    c = appmod.app.test_client()
    return lambda slug: c.get(f"/begin/product-page-data/{slug}").get_json()


def test_the_evox_page_is_served_as_a_service(page):
    d = page("evox-session")
    ids = [s["id"] for s in d["sections"]]
    assert d["is_service"] is True
    assert "research" not in ids, "REGRESSION: 'Studies behind the key ingredients' on a service"
    assert "related" not in ids, "REGRESSION: a service recommended a product (ED10 Skin Driver)"


def test_a_formula_page_is_unchanged(page):
    d = page("clear-the-way")
    ids = [s["id"] for s in d["sections"]]
    assert d["is_service"] is False
    assert "research" in ids and "related" in ids
