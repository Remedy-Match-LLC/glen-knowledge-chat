"""History-to-finding links panel (spec 2026-10-01). Pure: no app env needed."""
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from dashboard import history_links as HP  # noqa: E402


def _db(tmp_path):
    p = str(tmp_path / "e4l.db")
    cx = sqlite3.connect(p)
    cx.executescript("""
    CREATE TABLE e4l_scans(scan_id INTEGER PRIMARY KEY, client_id INTEGER, scan_date TEXT);
    CREATE TABLE e4l_clients(client_id INTEGER PRIMARY KEY, email TEXT);
    INSERT INTO e4l_clients VALUES (1, 'a@x.com'), (5, 'e@x.com'), (6, 'f@x.com');
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


# ---- Task 11: tests added after breaking each guard on purpose -------------------------

def _edit(p, sql, *args):
    with sqlite3.connect(p) as cx:
        cx.execute(sql, args)


def test_phrase_source_and_finding_name_are_each_escaped(tmp_path):
    p = _db(tmp_path)
    _edit(p, "UPDATE client_history SET phrase='<i>x</i>', source='odd<s>src' WHERE id=1")
    _edit(p, "UPDATE e4l_items SET full_name='<u>Stomach</u>' WHERE code='ED9'")
    html = HP.render_panel(p, "1", "2026-09-15")
    assert "&lt;i&gt;x&lt;/i&gt;" in html and "<i>x" not in html          # phrase
    assert "odd&lt;s&gt;src" in html and "odd<s>src" not in html          # source label
    assert "&lt;u&gt;Stomach&lt;/u&gt;" in html and "<u>Stomach" not in html   # finding name


def test_the_scan_date_is_escaped(tmp_path):
    p = _db(tmp_path)
    _edit(p, "UPDATE e4l_scans SET scan_date='2026-09-01<i>d</i>' WHERE scan_id=11")
    html = HP.render_panel(p, "1", "2026-09-15")
    assert "scan of 2026-09-01&lt;i&gt;d&lt;/i&gt;" in html and "<i>d" not in html


def test_a_database_without_the_merge_table_still_renders(tmp_path):
    p = _db(tmp_path)
    _edit(p, "DROP TABLE e4l_identity_merges")
    assert "macular degeneration" in HP.render_panel(p, "1", "2026-09-15")


def test_a_scan_on_the_test_date_itself_is_the_one_shown(tmp_path):
    p = _db(tmp_path)
    assert "scan of 2026-09-01" in HP.render_panel(p, "1", "2026-09-01")
    assert "scan of 2026-08-01" in HP.render_panel(p, "1", "2026-08-31")


def _merged(tmp_path):
    p = _db(tmp_path)
    with sqlite3.connect(p) as cx:
        cx.executescript("""
        INSERT INTO e4l_identity_merges(dup_client_id, canonical_client_id) VALUES (5,1),(6,1);
        INSERT INTO e4l_scans VALUES (30, 6, '2026-09-06');
        INSERT INTO client_history VALUES
          (7,6,'asthma','structured','pb','pb:asthma',1,'x','x',NULL);
        INSERT INTO finding_conditions VALUES (3,'ED5','asthma','drafted','',0,0,NULL);
        INSERT INTO finding_history_links VALUES (30,'ED5',7,3,'x');
        """)
    return p


def test_the_canonical_client_sees_a_duplicates_later_scan(tmp_path):
    html = HP.render_panel(_merged(tmp_path), "1", "2026-09-15")
    assert "scan of 2026-09-06" in html and "asthma" in html


def test_one_duplicate_sees_its_sibling_duplicates_scan(tmp_path):
    html = HP.render_panel(_merged(tmp_path), "5", "2026-09-15")
    assert "scan of 2026-09-06" in html and "asthma" in html


def test_an_unlinked_count_ignores_a_phrase_that_is_linked_from_another_source(tmp_path):
    p = _db(tmp_path)
    _edit(p, "INSERT INTO client_history VALUES"
             " (6,1,'macular degeneration','structured','pb','pb:mac',1,'x','x',NULL)")
    assert "1 reported condition no finding links to" in HP.render_panel(p, "1", "2026-09-15")


def test_the_unlinked_count_uses_the_plural_for_two_or_more(tmp_path):
    p = _db(tmp_path)
    _edit(p, "INSERT INTO client_history VALUES"
             " (6,1,'asthma','structured','pb','pb:asthma',1,'x','x',NULL)")
    assert "2 reported conditions no finding links to" in HP.render_panel(p, "1", "2026-09-15")


def test_a_scan_with_history_but_no_links_says_so(tmp_path):
    html = HP.render_panel(_db(tmp_path), "1", "2026-12-15")        # scan 12 has no links
    assert "No links on this scan." in html
    assert "3 reported conditions no finding links to" in html


def test_a_scan_with_no_links_and_no_history_renders_nothing(tmp_path):
    assert HP.render_panel(_db(tmp_path), "5", "2026-09-15") == ""


def test_the_database_is_opened_read_only(tmp_path, monkeypatch):
    p = _db(tmp_path)
    opened = []
    real = sqlite3.connect

    def spy(*a, **k):
        cx = real(*a, **k)
        opened.append(cx)
        return cx
    monkeypatch.setattr(HP.sqlite3, "connect", spy)
    assert HP.render_panel(p, "1", "2026-09-15") != ""
    assert opened
    for cx in opened:
        with pytest.raises(sqlite3.OperationalError):
            cx.execute("CREATE TABLE should_not_exist(a)")


def test_a_database_without_the_link_tables_renders_nothing(tmp_path):
    p = str(tmp_path / "old.db")
    with sqlite3.connect(p) as cx:
        cx.executescript("CREATE TABLE e4l_scans(scan_id INTEGER PRIMARY KEY, client_id INTEGER,"
                         " scan_date TEXT); INSERT INTO e4l_scans VALUES (1, 1, '2026-09-01');")
    assert HP.render_panel(p, "1", "2026-09-15") == ""


# ---- the /author/<test_id> route ---------------------------------------------------------

def _author_client(tmp_path):
    from biofield_local_app import create_app
    from dashboard.biofield_authoring import create_test, init_auth_tables, update_header
    e4l = _db(tmp_path)
    db = str(tmp_path / "chat.db")
    with sqlite3.connect(db) as cx:
        init_auth_tables(cx)
        tid = create_test(cx, name="Pat", email="pat@x.com", date="2026-09-15")
        update_header(cx, tid, client_id="1")
    app = create_app(db_path=db, e4l_db=e4l, fetch_profile=lambda e: {})
    app.testing = True
    return app.test_client(), tid, e4l


@pytest.fixture
def _open_console(monkeypatch):
    monkeypatch.delenv("CONSOLE_SECRET", raising=False)
    import dashboard as _d
    monkeypatch.setattr(_d, "CONSOLE_SECRET", "", raising=False)


def test_the_author_page_carries_the_panel_for_this_client(tmp_path, _open_console):
    c, tid, _ = _author_client(tmp_path)
    r = c.get(f"/author/{tid}")
    assert r.status_code == 200
    assert b"History and findings, scan of 2026-09-01" in r.data


def test_a_panel_failure_never_breaks_the_author_page(tmp_path, _open_console, monkeypatch):
    c, tid, _ = _author_client(tmp_path)

    def boom(*a, **k):
        raise RuntimeError("panel")
    monkeypatch.setattr(HP, "render_panel", boom)
    r = c.get(f"/author/{tid}")
    assert r.status_code == 200 and b"History and findings" not in r.data


def test_a_client_with_no_e4l_id_logs_no_panel_failure(tmp_path, _open_console, capsys):
    from biofield_local_app import create_app
    from dashboard.biofield_authoring import create_test, init_auth_tables
    db = str(tmp_path / "chat.db")
    with sqlite3.connect(db) as cx:
        init_auth_tables(cx)
        tid = create_test(cx, name="Pat", email="pat@x.com", date="2026-09-15")
    app = create_app(db_path=db, e4l_db=_db(tmp_path), fetch_profile=lambda e: {})
    app.testing = True
    r = app.test_client().get(f"/author/{tid}")
    assert r.status_code == 200 and b"History and findings" not in r.data
    assert "panel skipped" not in capsys.readouterr().out


# ---- Final review fix wave (2026-10-01) ------------------------------------------------

def test_a_link_through_a_removed_row_is_not_shown(tmp_path):          # C1
    p = _db(tmp_path)
    _edit(p, "UPDATE finding_conditions SET removed=1 WHERE id=1")
    html = HP.render_panel(p, "1", "2026-09-15")
    assert "ED9 Stomach Driver" not in html and "Stomach" not in html


def test_a_link_to_a_retired_history_row_is_not_shown(tmp_path):       # I1
    p = _db(tmp_path)
    _edit(p, "UPDATE client_history SET retired_at='2026-09-10' WHERE id=1")
    html = HP.render_panel(p, "1", "2026-09-15")
    assert "macular degeneration" not in html and "ED9 Stomach Driver" not in html


def test_the_unlinked_count_counts_only_active_structured_rows(tmp_path):   # M2
    p = _db(tmp_path)
    _edit(p, "INSERT INTO client_history VALUES"
             " (8,1,'covid vaccine 2021','narrative','intake:narrative','vaccinations',1,'x','x',NULL),"
             " (9,1,'possible lyme','hedged','intake','diagnoses',1,'x','x',NULL)")
    assert "1 reported condition no finding links to" in HP.render_panel(p, "1", "2026-09-15")


def test_a_finding_reached_from_two_sources_is_listed_once(tmp_path):   # M2
    p = _db(tmp_path)
    _edit(p, "INSERT INTO client_history VALUES"
             " (6,1,'macular degeneration','structured','pb','pb:mac',1,'x','x',NULL)")
    _edit(p, "INSERT INTO finding_history_links VALUES (11,'ED9',6,1,'x')")
    html = HP.render_panel(p, "1", "2026-09-15")
    assert html.count("ED9 Stomach Driver") == 1
    assert "Practice Better, uploaded report" in html


def test_a_shared_address_renders_nothing(tmp_path):                    # I3
    p = _db(tmp_path)
    _edit(p, "INSERT INTO e4l_clients VALUES (9, ' A@x.com ')")      # a second person
    assert HP.render_panel(p, "1", "2026-09-15") == ""


def test_a_merged_account_on_the_same_address_is_one_person(tmp_path):  # I3
    p = _db(tmp_path)
    _edit(p, "INSERT INTO e4l_clients VALUES (9, 'a@x.com')")
    _edit(p, "INSERT INTO e4l_identity_merges(dup_client_id, canonical_client_id) VALUES (9, 1)")
    assert "macular degeneration" in HP.render_panel(p, "1", "2026-09-15")


def test_a_client_with_no_e4l_record_renders_nothing(tmp_path):         # I3, fail closed
    p = _db(tmp_path)
    _edit(p, "DELETE FROM e4l_clients WHERE client_id=1")
    assert HP.render_panel(p, "1", "2026-09-15") == ""
