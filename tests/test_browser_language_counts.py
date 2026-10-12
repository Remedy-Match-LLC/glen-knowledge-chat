"""Translate step 0: count the language each home or begin page load asks for.

Glen, 2026-10-12: "start step 0". Counts only: no address, no visitor, no page.
"""
import sqlite3
import threading as _real_threading

import pytest

from dashboard import browser_languages as BL


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.setattr(BL, "_ready", False)


@pytest.mark.parametrize("header,expect", [
    ("es-MX,es;q=0.9,en;q=0.8", "es"),
    ("en-US,en;q=0.9", "en"),
    ("fr;q=0.5, de;q=0.8", "de"),          # the weight wins, not the order
    ("ja, ko", "ja"),                       # a tie keeps the browser's order
    ("haw", "haw"),                         # 3-letter codes, Hawaiian among them
    ("*", BL.NONE),
    ("", BL.NONE),
    (None, BL.NONE),
    ("es;q=0", BL.NONE),                    # q=0 means "not this one"
    ("<script>", BL.NONE),
    ("x-klingon", BL.NONE),
])
def test_the_preferred_language_is_read_from_the_header(header, expect):
    assert BL.primary_language(header) == expect


@pytest.mark.parametrize("path,counted", [
    ("/", True), ("/begin", True), ("/begin/scan", True),
    ("/shop", False), ("/console", False), ("/beginner", False), ("/api/orders", False),
])
def test_only_home_and_begin_pages_are_counted(path, counted):
    assert BL.counted_path(path) is counted


@pytest.mark.parametrize("ua,bot", [
    ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0) Safari/604.1", False),
    ("Googlebot/2.1 (+http://www.google.com/bot.html)", True),
    ("python-urllib/3.12", True),
    ("", True),
    (None, True),
])
def test_robots_are_not_counted(ua, bot):
    assert BL.is_bot(ua) is bot


def test_counts_add_up_per_day_and_language(tmp_path):
    cx = sqlite3.connect(str(tmp_path / "t.db"))
    for lang in ("es", "es", "en", BL.NONE):
        BL.record(cx, lang, day="2026-10-12")
    BL.record(cx, "es", day="2026-10-13")
    assert BL.totals(cx) == {"es": 3, "en": 1, BL.NONE: 1}
    assert BL.totals(cx, since="2026-10-13") == {"es": 1}
    cols = [r[1] for r in cx.execute(f"PRAGMA table_info({BL.TABLE})")]
    assert cols == ["day", "lang", "n"], "the table must hold counts and nothing else"


def test_the_day_is_hawaii_time():
    import datetime
    utc = datetime.datetime(2026, 10, 13, 5, 0, tzinfo=datetime.timezone.utc)  # 19:00 HST on the 12th
    assert BL.today_hst(utc) == "2026-10-12"


class _Sync:
    """threading stand-in whose Thread runs at once, so the count is there to read."""
    def __getattr__(self, name):
        return getattr(_real_threading, name)

    class Thread:
        def __init__(self, target=None, args=(), **_):
            self._t, self._a = target, args

        def start(self):
            self._t(*self._a)


@pytest.fixture
def served(monkeypatch, tmp_path):
    import app
    dbp = tmp_path / "chat_log.db"
    monkeypatch.setattr(app, "LOG_DB", dbp)
    monkeypatch.setattr(app, "threading", _Sync())
    return app.app.test_client(), dbp


def _counts(dbp):
    cx = sqlite3.connect(str(dbp))
    try:
        return BL.totals(cx)
    finally:
        cx.close()


BROWSER = "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) Safari/605.1.15"


def test_a_begin_page_load_is_counted_by_language(served):
    client, dbp = served
    r = client.get("/begin", headers={"Accept-Language": "es-MX,es;q=0.9", "User-Agent": BROWSER})
    assert r.status_code == 200
    assert _counts(dbp) == {"es": 1}


def test_a_robot_and_another_page_are_not_counted(served):
    client, dbp = served
    client.get("/begin", headers={"Accept-Language": "es", "User-Agent": "Googlebot/2.1"})
    r = client.get("/privacy", headers={"Accept-Language": "es", "User-Agent": BROWSER})
    assert r.status_code == 200 and r.mimetype == "text/html", "the check needs a real page"
    assert not dbp.exists() or _counts(dbp) == {}


def test_a_failed_count_never_costs_the_page(served, monkeypatch, capsys):
    client, _ = served

    def boom(*a, **k):
        raise RuntimeError("db down")
    monkeypatch.setattr(BL, "record", boom)
    r = client.get("/begin", headers={"Accept-Language": "es", "User-Agent": BROWSER})
    assert r.status_code == 200
    assert "count skipped" in capsys.readouterr().out
