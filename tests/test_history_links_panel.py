"""History-to-finding links panel (spec 2026-10-01). Pure: no app env needed."""
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dashboard import history_links as HP  # noqa: E402


def _db(tmp_path):
    p = str(tmp_path / "e4l.db")
    cx = sqlite3.connect(p)
    cx.executescript("""
    CREATE TABLE e4l_scans(scan_id INTEGER PRIMARY KEY, client_id INTEGER, scan_date TEXT);
    CREATE TABLE e4l_items(code TEXT PRIMARY KEY, name TEXT, full_name TEXT);
    CREATE TABLE e4l_identity_merges(dup_client_id INTEGER PRIMARY KEY,
        canonical_client_id INTEGER NOT NULL, note TEXT, confirmed_at TEXT,
        route_reports INTEGER NOT NULL DEFAULT 0);
    CREATE TABLE client_history(id INTEGER PRIMARY KEY, client_id INTEGER, phrase TEXT,
        kind TEXT, source TEXT, source_ref TEXT, confirmed INTEGER, first_seen TEXT,
        last_seen TEXT, retired_at TEXT);
    CREATE TABLE finding_conditions(id INTEGER PRIMARY KEY, item_code TEXT, condition TEXT,
        origin TEXT, note TEXT, reviewed INTEGER, removed INTEGER, reviewed_at TEXT);
    CREATE TABLE finding_history_links(scan_id INTEGER, item_code TEXT, history_id INTEGER,
        finding_condition_id INTEGER, linked_at TEXT);
    INSERT INTO e4l_scans VALUES (10, 1, '2026-08-01'), (11, 1, '2026-09-01'),
                                 (12, 1, '2026-12-01'), (20, 5, '2026-09-05');
    INSERT INTO e4l_items VALUES ('ED5','Circulation','ED5 Circulation'),
                                 ('ED9','Stomach','ED9 Stomach Driver');
    INSERT INTO client_history VALUES
      (1,1,'macular degeneration','structured','document','41',1,'x','x',NULL),
      (2,1,'tinnitus','structured','chat','focus:tinnitus',0,'x','x',NULL),
      (3,1,'gout','structured','pb','pb:gout',1,'x','x',NULL),
      (4,1,'old thing','structured','pb','pb:old',1,'x','x','2026-09-01');
    INSERT INTO finding_conditions VALUES
      (1,'ED9','macular degeneration','glen','Stomach <b>carries</b> metals',1,0,'x'),
      (2,'ED5','tinnitus','drafted','',0,0,NULL);
    INSERT INTO finding_history_links VALUES (11,'ED9',1,1,'x'), (11,'ED5',2,2,'x'),
                                             (10,'ED5',3,2,'x');
    """)
    cx.commit()
    return p


def test_panel_shows_the_scan_on_or_before_the_test_date(tmp_path):
    html = HP.render_panel(_db(tmp_path), "1", "2026-09-15")
    assert "2026-09-01" in html and "2026-12-01" not in html
    assert "macular degeneration" in html and "ED9 Stomach Driver" in html
    assert "uploaded report" in html
    assert "from chat, unconfirmed" in html
    assert html.count("unreviewed") == 1                      # ED5 row only
    assert "gout" not in html                                  # linked on another scan
    assert "1 reported condition no finding links to" in html  # gout; retired not counted


def test_notes_and_phrases_are_escaped(tmp_path):
    html = HP.render_panel(_db(tmp_path), "1", "2026-09-15")
    assert "Stomach &lt;b&gt;carries&lt;/b&gt; metals" in html
    assert "<b>carries" not in html


def test_a_merged_duplicate_shows_the_canonical_persons_links(tmp_path):
    p = _db(tmp_path)
    with sqlite3.connect(p) as cx:
        cx.execute("INSERT INTO e4l_identity_merges(dup_client_id, canonical_client_id)"
                   " VALUES (5, 1)")
    # 2026-09-03: before client 5's own scan 20, so the canonical scan 11 is the latest
    assert "macular degeneration" in HP.render_panel(p, "5", "2026-09-03")


def test_panel_is_empty_when_it_cannot_say_anything(tmp_path):
    p = _db(tmp_path)
    assert HP.render_panel(p, "", "2026-09-15") == ""
    assert HP.render_panel(p, "abc", "2026-09-15") == ""
    assert HP.render_panel(p, "1", "2026-07-01") == ""        # no scan yet
    assert HP.render_panel(str(tmp_path / "missing.db"), "1", "2026-09-15") == ""
