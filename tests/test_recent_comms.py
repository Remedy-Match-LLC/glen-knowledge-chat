import json
import sqlite3
from datetime import datetime, timezone, timedelta
from dashboard.recent_comms import recent_comms


def _db(tmp_path):
    cx = sqlite3.connect(str(tmp_path / "c.db"))
    cx.executescript("""
        CREATE TABLE inbound_leads(id INTEGER PRIMARY KEY AUTOINCREMENT, email TEXT,
            source TEXT, first_name TEXT, raw_json TEXT);
        CREATE TABLE inquiries(id INTEGER PRIMARY KEY AUTOINCREMENT, client_email TEXT,
            main_challenge TEXT, main_goal TEXT, created_at TEXT);
        CREATE TABLE query_log(id INTEGER PRIMARY KEY AUTOINCREMENT, email TEXT,
            query TEXT, ts TEXT);
    """)
    return cx


def _iso(days_ago):
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()


def test_windows_chat_and_inquiries_keeps_latest_intake(tmp_path):
    cx = _db(tmp_path)
    # intake (old, but always kept)
    quiz = {"data": {"total_score": {"percent": 80},
                     "quiz_questions": [{"question": "Energy?", "answers": [{"answer": "Low"}]}]}}
    cx.execute("INSERT INTO inbound_leads(email,source,first_name,raw_json) VALUES(?,?,?,?)",
               ("j@x.com", "scoreapp", "Jane", json.dumps(quiz)))
    # inquiries: one recent, one old
    cx.execute("INSERT INTO inquiries(client_email,main_challenge,main_goal,created_at) VALUES(?,?,?,?)",
               ("j@x.com", "fatigue", "more energy", _iso(2)))
    cx.execute("INSERT INTO inquiries(client_email,main_challenge,main_goal,created_at) VALUES(?,?,?,?)",
               ("j@x.com", "ancient", "old", _iso(60)))
    # queries: one recent, one old
    cx.execute("INSERT INTO query_log(email,query,ts) VALUES(?,?,?)", ("j@x.com", "why tired", _iso(1)))
    cx.execute("INSERT INTO query_log(email,query,ts) VALUES(?,?,?)", ("j@x.com", "stale q", _iso(30)))
    cx.commit()
    out = recent_comms(cx, "j@x.com", days_window=7)
    assert "Energy?: Low" in out["intake_summary"] and "Jane" in out["intake_summary"]
    assert [i["main_challenge"] for i in out["recent_inquiries"]] == ["fatigue"]   # old excluded
    assert [q["question"] for q in out["recent_queries"]] == ["why tired"]          # old excluded


def test_question_column_fallback(tmp_path):
    cx = sqlite3.connect(str(tmp_path / "c.db"))
    cx.executescript("""CREATE TABLE query_log(id INTEGER PRIMARY KEY AUTOINCREMENT,
        email TEXT, question TEXT, ts TEXT);""")
    cx.execute("INSERT INTO query_log(email,question,ts) VALUES(?,?,?)",
               ("j@x.com", "from question col", _iso(1)))
    cx.commit()
    out = recent_comms(cx, "j@x.com")
    assert [q["question"] for q in out["recent_queries"]] == ["from question col"]


def test_empty_email_and_missing_tables(tmp_path):
    cx = sqlite3.connect(str(tmp_path / "c.db"))   # no tables at all
    assert recent_comms(cx, "") == {"intake_summary": "", "recent_inquiries": [], "recent_queries": [], "recent_feedback": []}
    # Every section is missing, so each is named as unread, never passed off as empty.
    assert recent_comms(cx, "j@x.com") == {"intake_summary": "", "recent_inquiries": [], "recent_queries": [], "recent_feedback": [],
                                           "unavailable": ["intake", "inquiries", "queries", "feedback"]}


import json as _json
from datetime import datetime, timezone, timedelta


def _fb_db(tmp_path):
    import sqlite3
    cx = sqlite3.connect(str(tmp_path / "fb.db"))
    cx.executescript("""
        CREATE TABLE users(id INTEGER PRIMARY KEY AUTOINCREMENT, email TEXT);
        CREATE TABLE personal_email_feedback(id INTEGER PRIMARY KEY AUTOINCREMENT,
            received_at TEXT, user_id INTEGER, raw_text TEXT, ai_summary TEXT,
            extracted_topics TEXT, extracted_conditions TEXT);
    """)
    cx.execute("INSERT INTO users(id,email) VALUES(1,'j@x.com')")
    cx.execute("INSERT INTO users(id,email) VALUES(2,'other@x.com')")
    return cx


def _ago(days):
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


def test_recent_feedback_windowed_joined_parsed(tmp_path):
    cx = _fb_db(tmp_path)
    # in-window row for j@x.com
    cx.execute("INSERT INTO personal_email_feedback(received_at,user_id,raw_text,ai_summary,"
               "extracted_topics,extracted_conditions) VALUES(?,?,?,?,?,?)",
               (_ago(2), 1, "SECRET RAW REPLY", "feels exhausted lately",
                _json.dumps(["sleep", "energy"]), _json.dumps(["adrenal fatigue"])))
    # out-of-window row for j@x.com
    cx.execute("INSERT INTO personal_email_feedback(received_at,user_id,ai_summary) VALUES(?,?,?)",
               (_ago(60), 1, "old feedback"))
    # other user's row
    cx.execute("INSERT INTO personal_email_feedback(received_at,user_id,ai_summary) VALUES(?,?,?)",
               (_ago(1), 2, "not jane's"))
    cx.commit()
    out = recent_comms(cx, "J@X.com", days_window=7)               # case-insensitive join
    fb = out["recent_feedback"]
    assert len(fb) == 1
    assert fb[0]["summary"] == "feels exhausted lately"
    assert fb[0]["topics"] == ["sleep", "energy"]
    assert fb[0]["conditions"] == ["adrenal fatigue"]
    # raw_text must never be exposed
    assert "raw_text" not in fb[0] and "SECRET RAW REPLY" not in str(out)


def test_recent_feedback_bad_json_and_missing_table(tmp_path):
    cx = _fb_db(tmp_path)
    cx.execute("INSERT INTO personal_email_feedback(received_at,user_id,ai_summary,"
               "extracted_topics,extracted_conditions) VALUES(?,?,?,?,?)",
               (_ago(1), 1, "sum", "not json", ""))
    cx.commit()
    out = recent_comms(cx, "j@x.com")
    assert out["recent_feedback"][0]["topics"] == [] and out["recent_feedback"][0]["conditions"] == []
    # missing tables entirely -> empty section, no raise
    import sqlite3
    bare = sqlite3.connect(str(tmp_path / "bare.db"))
    assert recent_comms(bare, "j@x.com")["recent_feedback"] == []


def test_empty_email_includes_feedback_key(tmp_path):
    cx = _fb_db(tmp_path)
    assert recent_comms(cx, "")["recent_feedback"] == []


class _PgLike:
    """sqlite underneath, Postgres's rule on top: after a failed statement every statement
    fails until ROLLBACK TO SAVEPOINT. That rule emptied recent_queries and recent_feedback
    in prod for every client: query_log has no `question` column (2026-10-16)."""

    def __init__(self, cx):
        self.cx, self.aborted, self.sql = cx, False, []

    @property
    def row_factory(self):
        return self.cx.row_factory

    @row_factory.setter
    def row_factory(self, v):
        self.cx.row_factory = v

    def execute(self, sql, args=()):
        self.sql.append(sql)
        if sql.startswith("ROLLBACK TO SAVEPOINT"):
            self.aborted = False
            return None
        if sql.startswith(("SAVEPOINT", "RELEASE SAVEPOINT")):
            return None
        if self.aborted:
            raise RuntimeError("current transaction is aborted")
        try:
            return self.cx.execute(sql, args)
        except Exception:
            self.aborted = True
            raise


def _prod_shaped_db(tmp_path):
    cx = _fb_db(tmp_path)
    cx.executescript("""
        CREATE TABLE query_log(id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, query TEXT, email TEXT);
        CREATE TABLE inquiries(client_email TEXT, main_challenge TEXT, main_goal TEXT, created_at TEXT);
        CREATE TABLE inbound_leads(id INTEGER PRIMARY KEY AUTOINCREMENT, email TEXT, first_name TEXT,
            raw_json TEXT, source TEXT);
    """)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    cx.execute("INSERT INTO query_log(ts,query,email) VALUES(?,?,?)", (now, "my eyes ache", "j@x.com"))
    cx.execute("INSERT INTO personal_email_feedback(received_at,user_id,ai_summary,extracted_topics,"
               "extracted_conditions) VALUES(?,?,?,?,?)", (now, 1, "cataract worse", "[]", '["cataract"]'))
    cx.commit()
    return cx


def test_on_a_postgres_shaped_connection_queries_and_feedback_survive(tmp_path):
    pg = _PgLike(_prod_shaped_db(tmp_path))
    out = recent_comms(pg, "j@x.com")
    assert [q["question"] for q in out["recent_queries"]] == ["my eyes ache"]
    assert [f["summary"] for f in out["recent_feedback"]] == ["cataract worse"]
    assert "unavailable" not in out
    # The real column is asked for first, so no failed statement is needed at all.
    assert not any("SELECT question" in s for s in pg.sql)


def test_a_failed_section_is_named_and_does_not_empty_the_rest(tmp_path):
    cx = _prod_shaped_db(tmp_path)
    cx.execute("DROP TABLE inquiries")
    pg = _PgLike(cx)
    out = recent_comms(pg, "j@x.com")
    assert out["unavailable"] == ["inquiries"]
    assert out["recent_queries"] and out["recent_feedback"]


def test_testimonial_scan_text_carries_the_email_feedback_summary(tmp_path, monkeypatch):
    """recent_comms returns `summary`; the scan read `ai_summary` and always got ""."""
    import app
    monkeypatch.setattr("dashboard.recent_comms.recent_comms", lambda cx, e, **k: {
        "intake_summary": "", "recent_inquiries": [], "recent_queries": [],
        "recent_feedback": [{"summary": "my vision is clearer", "topics": [], "conditions": []}]})
    assert app._gather_comms_text(None, "j@x.com") == "Email feedback: my vision is clearer"
