import datetime, sqlite3, uuid
from dashboard import membership_products as mp

def _mk_db(tmp_path):
    cx = sqlite3.connect(tmp_path / "t.db")
    cx.execute("""CREATE TABLE memberships (id TEXT PRIMARY KEY, email TEXT NOT NULL,
        granted_at TEXT NOT NULL, expires_at TEXT, granted_by TEXT, source TEXT,
        truly_vip_ref TEXT, notes TEXT, last_reminder_at TEXT)""")
    cx.commit()
    return cx

def _grant(cx, email, source, days):
    now = datetime.datetime.utcnow()
    exp = (now + datetime.timedelta(days=days)).isoformat()
    cx.execute("INSERT INTO memberships (id,email,granted_at,expires_at,granted_by,source) "
               "VALUES (?,?,?,?,?,?)",
               (uuid.uuid4().hex, email, now.isoformat(), exp, source, source))
    cx.commit()

def test_membership_grant_owns_group(tmp_path):
    cx = _mk_db(tmp_path)
    _grant(cx, "a@x.com", "membership_month", 34)
    assert mp.owns_group(cx, "a@x.com") is True

def test_prepay_grant_does_not_own_group(tmp_path):
    cx = _mk_db(tmp_path)
    _grant(cx, "b@x.com", "prepay_12mo", 369)  # different namespace
    assert mp.owns_group(cx, "b@x.com") is False

def test_expired_membership_grant_does_not_own(tmp_path):
    cx = _mk_db(tmp_path)
    _grant(cx, "c@x.com", "membership_year_prepay", -1)  # already expired
    assert mp.owns_group(cx, "c@x.com") is False


# ── Glen's lifetime grant (expires_at NULL), money 2026-09-24 ────────────────
# Kauilani and Keikilani Perdomo were made members for life. The only route stores
# expires_at NULL, and `expires_at > now` never matched it, so the portal would have
# offered them the live group at $99/mo.

def _grant_null(cx, email, source):
    cx.execute("INSERT INTO memberships (id,email,granted_at,expires_at,granted_by,source) "
               "VALUES (?,?,?,NULL,?,?)",
               (uuid.uuid4().hex, email, datetime.datetime.utcnow().isoformat(), "glen", source))
    cx.commit()


def test_a_lifetime_grant_owns_group(tmp_path):
    cx = _mk_db(tmp_path)
    _grant_null(cx, "life@x.com", "owner_lifetime")
    assert mp.owns_group(cx, "life@x.com") is True
    assert mp.owns_group(cx, "LIFE@x.com") is True


def test_a_null_biofield_trial_row_does_not_own_group(tmp_path):
    """The $1 unlock is lifetime by design; it must never become membership."""
    cx = _mk_db(tmp_path)
    _grant_null(cx, "trial@x.com", "biofield_trial")
    assert mp.owns_group(cx, "trial@x.com") is False


def test_a_null_row_from_any_other_source_does_not_own_group(tmp_path):
    cx = _mk_db(tmp_path)
    _grant_null(cx, "m@x.com", "membership_month")   # a tier source with no expiry
    _grant_null(cx, "p@x.com", "prepay_12mo")
    assert mp.owns_group(cx, "m@x.com") is False
    assert mp.owns_group(cx, "p@x.com") is False


def test_a_dated_owner_lifetime_grant_owns_until_it_expires(tmp_path):
    cx = _mk_db(tmp_path)
    _grant(cx, "d@x.com", "owner_lifetime", 30)
    _grant(cx, "e@x.com", "owner_lifetime", -1)
    assert mp.owns_group(cx, "d@x.com") is True
    assert mp.owns_group(cx, "e@x.com") is False


def test_the_member_backfill_includes_a_lifetime_member_only(tmp_path, monkeypatch):
    """subscriptions.backfill_member_people ensures each current member has a people row.
    The people write is recorded, not faked: the query that picks members is what changed."""
    from dashboard import subscriptions as subs
    cx = _mk_db(tmp_path)
    cx.execute("CREATE TABLE subscriptions (email TEXT, kind TEXT, status TEXT)")
    cx.execute("CREATE TABLE people (email TEXT)")
    _grant_null(cx, "life@x.com", "owner_lifetime")
    _grant_null(cx, "trial@x.com", "biofield_trial")
    _grant(cx, "dated@x.com", "cash", 30)
    made = []
    monkeypatch.setattr(subs._customers, "find_or_create_by_email",
                        lambda cx, email: made.append(email))
    assert subs.backfill_member_people(cx) == 2
    assert sorted(made) == ["dated@x.com", "life@x.com"]


def test_the_console_member_backfill_route_picks_lifetime_members(tmp_path, monkeypatch):
    """app.py's /api/console/backfill-member-people used `expires_at > ?` and skipped
    NULL-expiry lifetime grants (money, 2026-09-24). Dry run: it names who it would add."""
    import importlib, os
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake")
    monkeypatch.setenv("PINECONE_API_KEY", "pcsk_fake")
    import app as appmod
    db = str(tmp_path / "chat_log.db")
    monkeypatch.setattr(appmod, "LOG_DB", db)
    monkeypatch.setattr(appmod, "_bos_actor", lambda: object())
    cx = _mk_db_at(db)
    cx.execute("CREATE TABLE subscriptions (email TEXT, kind TEXT, status TEXT)")
    cx.execute("CREATE TABLE people (email TEXT)")
    _grant_null(cx, "life@x.com", "owner_lifetime")
    _grant_null(cx, "trial@x.com", "biofield_trial")
    cx.commit(); cx.close()
    r = appmod.app.test_client().post("/api/console/backfill-member-people?dry_run=1")
    assert r.status_code == 200
    assert r.get_json()["emails"] == ["life@x.com"]


def _mk_db_at(path):
    cx = sqlite3.connect(path)
    cx.execute("""CREATE TABLE memberships (id TEXT PRIMARY KEY, email TEXT NOT NULL,
        granted_at TEXT NOT NULL, expires_at TEXT, granted_by TEXT, source TEXT,
        truly_vip_ref TEXT, notes TEXT, last_reminder_at TEXT)""")
    return cx
