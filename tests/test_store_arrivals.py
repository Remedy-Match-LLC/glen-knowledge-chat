"""Store arrivals: a tagged visit to /begin/product/<slug> is logged, nothing else is.

Glen, 2026-09-21: GrooveKart links 71 products to the new store with
utm_source=groovekart, and nothing recorded those visits. See dashboard/store_arrivals.py.
"""
import importlib
import sqlite3
import sys
from pathlib import Path

import pytest

KEY = "test-console-key"
UA = "Mozilla/5.0 (Macintosh) Safari/605"
GK = "/begin/product/msm-powder?utm_source=groovekart&utm_medium=product&utm_campaign=gk-description"


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    repo = Path(__file__).resolve().parent.parent
    if str(repo) not in sys.path:
        sys.path.insert(0, str(repo))
    try:
        import app as appmod
        importlib.reload(appmod)
    except Exception as e:
        pytest.skip(f"app not importable: {e}")
    appmod.app.config["TESTING"] = True
    monkeypatch.setattr(appmod, "CONSOLE_SECRET", KEY)
    monkeypatch.setattr(appmod, "_get_product",
                        lambda s: {"slug": "msm-powder", "name": "MSM Powder"}
                        if s in ("msm-powder", "msm") else None)
    client = appmod.app.test_client()
    db = str(tmp_path / "chat_log.db")
    return appmod, client, db


def _rows(db):
    with sqlite3.connect(db) as cx:
        try:
            return cx.execute("SELECT product_slug, utm_source, utm_medium, utm_campaign, "
                              "session FROM store_arrivals ORDER BY id").fetchall()
        except sqlite3.OperationalError:
            return []


def test_a_tagged_visit_is_recorded_with_its_session(env):
    appmod, client, db = env
    client.set_cookie("amg_session", "s1")
    r = client.get(GK, headers={"User-Agent": UA})
    assert r.status_code == 200
    assert _rows(db) == [("msm-powder", "groovekart", "product", "gk-description", "s1")]


def test_a_new_visitor_is_recorded_under_the_cookie_it_is_given(env):
    appmod, client, db = env
    r = client.get(GK, headers={"User-Agent": UA})
    assert r.status_code == 200
    cookie = client.get_cookie("amg_session")
    assert cookie is not None and cookie.value
    rows = _rows(db)
    assert len(rows) == 1 and rows[0][4] == cookie.value


def test_an_untagged_visit_records_nothing(env):
    appmod, client, db = env
    r = client.get("/begin/product/msm-powder", headers={"User-Agent": UA})
    assert r.status_code == 200
    # Reached: the page rendered, so the guard is what kept the log empty.
    assert b"MSM Powder" in r.data
    assert _rows(db) == []


@pytest.mark.parametrize("ua", ["", "Googlebot/2.1", "facebookexternalhit/1.1", "curl/8.4"])
def test_a_crawler_records_nothing(env, ua):
    appmod, client, db = env
    r = client.get(GK, headers={"User-Agent": ua})
    assert r.status_code == 200 and b"MSM Powder" in r.data
    assert _rows(db) == []


def test_a_superseded_slug_is_recorded_as_the_live_product(env):
    appmod, client, db = env
    client.get("/begin/product/msm?utm_source=GrooveKart", headers={"User-Agent": UA})
    assert [r[:2] for r in _rows(db)] == [("msm-powder", "groovekart")]


def test_an_unknown_product_records_nothing(env):
    appmod, client, db = env
    r = client.get("/begin/product/nope?utm_source=groovekart", headers={"User-Agent": UA})
    assert r.status_code == 404
    assert _rows(db) == []


def test_a_recording_failure_still_serves_the_page(env, monkeypatch):
    appmod, client, db = env
    from dashboard import store_arrivals as sa

    def boom(*a, **k):
        raise RuntimeError("db down")
    monkeypatch.setattr(sa, "record", boom)
    r = client.get(GK, headers={"User-Agent": UA})
    assert r.status_code == 200 and b"MSM Powder" in r.data


def test_the_report_needs_the_header_key(env):
    appmod, client, db = env
    assert client.get("/api/admin/store-arrivals").status_code == 401
    assert client.get(f"/api/admin/store-arrivals?key={KEY}").status_code == 401
    assert client.get("/api/admin/store-arrivals",
                      headers={"X-Console-Key": "wrong"}).status_code == 401


def test_the_report_counts_arrivals_and_sessions(env):
    appmod, client, db = env
    client.set_cookie("amg_session", "s1")
    client.get(GK, headers={"User-Agent": UA})
    client.get(GK, headers={"User-Agent": UA})
    client.set_cookie("amg_session", "s2")
    client.get(GK, headers={"User-Agent": UA})
    client.get("/begin/product/msm-powder?utm_source=newsletter", headers={"User-Agent": UA})
    r = client.get("/api/admin/store-arrivals?days=7", headers={"X-Console-Key": KEY})
    body = r.get_json()
    assert r.status_code == 200 and body["ok"]
    assert (body["arrivals"], body["sessions"]) == (4, 2)
    gk = [s for s in body["by_source"] if s["utm_source"] == "groovekart"]
    assert gk == [{"utm_source": "groovekart", "utm_medium": "product",
                   "arrivals": 3, "sessions": 2}]
    assert body["by_product"] == [{"product_slug": "msm-powder", "arrivals": 4, "sessions": 2}]
    assert len(body["by_day"]) == 1

    only = client.get("/api/admin/store-arrivals?source=groovekart",
                      headers={"X-Console-Key": KEY}).get_json()
    assert (only["arrivals"], only["sessions"], only["days"]) == (3, 2, 30)


def test_days_is_clamped():
    from dashboard import store_arrivals as sa
    assert sa.clamp_days("abc") == 30
    assert sa.clamp_days("0") == 1
    assert sa.clamp_days("5000") == sa.MAX_DAYS
