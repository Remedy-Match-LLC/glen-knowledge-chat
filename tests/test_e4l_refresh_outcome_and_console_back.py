"""Two small Biofield Intake fixes (Glen, 2026-10-16).

"Check E4L now" said "Up to date" when E4L had not listed a fresh scan yet, and nothing
recorded what each refresh found. The "← Console" button always went to /console, not
the console page the intake was opened from.
"""
import sqlite3

import pytest

from biofield_local_app import create_app
from dashboard.biofield_e4l import scan_context, scrape_counts
from dashboard.biofield_report_html import _BACK_JS, _bar
from dashboard.console_launcher import console_back_path, local_tool_url


@pytest.fixture(autouse=True)
def _no_gate(monkeypatch):
    monkeypatch.delenv("CONSOLE_SECRET", raising=False)
    import dashboard as _d
    monkeypatch.setattr(_d, "CONSOLE_SECRET", "", raising=False)


def test_scrape_counts_reads_the_scrapers_own_lines():
    out = ("[1/1] Test Person (ID: 1) — 2 new scan(s)\n"
           "    download error scan 11: timeout\n    ✓ 12.pdf\n")
    assert scrape_counts(out) == {"new_scans": 2, "download_errors": 1}
    assert scrape_counts("[1/1] Test Person (ID: 1) — 0 new scan(s)\n") == \
        {"new_scans": 0, "download_errors": 0}
    assert scrape_counts(None) == {"new_scans": 0, "download_errors": 0}


def _seed(path):
    cx = sqlite3.connect(str(path))
    cx.executescript("""
        CREATE TABLE e4l_clients(client_id INTEGER PRIMARY KEY, name TEXT, email TEXT);
        CREATE TABLE e4l_scans(scan_id INTEGER PRIMARY KEY, client_id INTEGER, scan_date TEXT);
        CREATE TABLE e4l_scan_results(id INTEGER PRIMARY KEY AUTOINCREMENT,
            scan_id INTEGER, item_code TEXT, priority_rank INTEGER);
        CREATE TABLE e4l_items(code TEXT PRIMARY KEY, name TEXT, full_name TEXT,
            category TEXT, e4l_description TEXT, clinical_notes TEXT);
        INSERT INTO e4l_clients VALUES(1,'Test Person','tp@example.com');
        INSERT INTO e4l_scans VALUES(10,1,'2026-06-20');
    """)
    cx.commit(); cx.close()


def test_each_refresh_is_logged_and_returns_its_download_errors(tmp_path, capsys):
    e4l = tmp_path / "e4l.db"; _seed(e4l)
    c = create_app(str(tmp_path / "chat_log.db"),
                   scan_lookup=lambda em: scan_context(em, "2026-06-24", db_path=str(e4l)),
                   fetch_runner=lambda **k: {"ok": True, "new_scans": 1,
                                             "download_errors": 1}).test_client()
    tid = c.post("/author/new").headers["Location"].rstrip("/").split("/")[-1]
    c.post(f"/author/{tid}/header", json={"client_name": "Test Person",
                                          "client_email": "tp@example.com"})
    j = c.post(f"/author/{tid}/e4l/refresh", json={"client_id": 1}).get_json()
    assert j["newer"] is False and j["download_errors"] == 1
    log = capsys.readouterr().out
    assert f"[e4l-refresh]" in log and f"test {tid}:" in log
    assert "new_scans=1" in log and "download_errors=1" in log and "newer=False" in log


def test_the_page_no_longer_says_up_to_date():
    import dashboard.biofield_report_html as h
    src = open(h.__file__).read()
    assert "Up to date." not in src
    assert "E4L may still be processing; try again in a minute." in src


@pytest.mark.parametrize("ref,want", [
    ("https://illtowell.com/console/orders?tab=open", "/console/orders?tab=open"),
    ("https://illtowell.com/console", "/console"),
    ("https://evil.example/console/orders", ""),
    ("https://illtowell.com/begin/scan", ""),
    ("https://illtowell.com/consoles", ""),
    ("https://illtowell.com/console//evil.example", ""),
    ("https://illtowell.com/console/biofield-intake", ""),
    ("javascript:alert(1)", ""),
    ("", ""),
    (None, ""),
])
def test_console_back_path_keeps_only_a_same_host_console_page(ref, want):
    assert console_back_path(ref, "illtowell.com") == want


def test_the_launcher_url_carries_back_beside_the_key():
    u = local_tool_url("NO_SUCH_ENV", "http://127.0.0.1:8011", "k",
                       back="/console/orders?tab=open")
    assert u == "http://127.0.0.1:8011/?key=k&back=%2Fconsole%2Forders%3Ftab%3Dopen"
    assert local_tool_url("NO_SUCH_ENV", "http://127.0.0.1:8011", "") == \
        "http://127.0.0.1:8011/"


def test_the_console_button_reads_the_back_path():
    from dashboard.biofield_report_html import _page
    assert "id=opconsole" in _bar("", "")
    assert _BACK_JS in _page("t", "")
    assert "sessionStorage" in _BACK_JS and "back" in _BACK_JS
