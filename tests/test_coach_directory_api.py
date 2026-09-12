# tests/test_coach_directory_api.py
import sqlite3
from unittest import mock
import app as appmod
from dashboard import coach_directory as _cd


def _client():
    appmod.app.config["TESTING"] = True
    return appmod.app.test_client()


def _seed(email, *, with_window):
    from datetime import datetime, timezone, timedelta
    with sqlite3.connect(appmod.LOG_DB) as cx:
        cx.row_factory = sqlite3.Row
        from dashboard import evox as _ev, client_portal as _cp, coaching as _co
        _ev.init_evox_tables(cx); _cp.init_client_portal_table(cx)
        _cd.init_coach_tables(cx); _co.init_coaching_table(cx)
        _cd.upsert_volunteer(cx, email="coach@x.com", name="Cora", focus="sleep",
                             intro_video_url="https://rumble.com/v-c", capacity=3, cert_ok=1)
        if with_window:
            now = datetime.now(timezone.utc)
            started = now.isoformat()
            ends = (now + timedelta(days=10)).isoformat()
            cx.execute("INSERT INTO coaching_windows (email,order_id,started_at,ends_at,"
                       "source,created_at) VALUES (?,?,?,?,?,?)",
                       (email, 1, started, ends, "test", started))
        # Paid membership, not just a window. Glen, 2026-09-12: 1:1 coach connect
        # is "only for paid members", so _coach_connect_entitled requires BOTH an
        # active coaching window and current paid membership. A window alone used to
        # be enough, which is what these fixtures were built against.
        cx.execute("CREATE TABLE IF NOT EXISTS memberships (id TEXT, email TEXT, "
                   "granted_at TEXT, expires_at TEXT, granted_by TEXT, source TEXT, "
                   "truly_vip_ref TEXT, notes TEXT)")
        from datetime import datetime as _dt, timezone as _tz, timedelta as _td
        _n = _dt.now(_tz.utc)
        cx.execute("INSERT OR REPLACE INTO memberships (id,email,granted_at,expires_at,"
                   "granted_by,source,truly_vip_ref,notes) VALUES (?,?,?,?,?,?,?,?)",
                   (f"m-{email}", email, _n.isoformat(),
                    (_n + _td(days=60)).isoformat(), "test", "test", "", ""))
        token = _ev.ensure_portal_token(cx, email, "Mem")
        cx.commit()
    return token


def test_directory_member_with_window_sees_coaches():
    c = _client(); tok = _seed("m@x.com", with_window=True)
    d = c.get(f"/api/community/coaches?token={tok}").get_json()
    assert d["eligible"] is True
    assert d["coaches"][0]["name"] == "Cora"
    assert "email" not in d["coaches"][0]           # no coach email exposed


def test_directory_member_without_window_ineligible():
    c = _client(); tok = _seed("m2@x.com", with_window=False)
    d = c.get(f"/api/community/coaches?token={tok}").get_json()
    assert d["eligible"] is False and d["coaches"] == []


def test_directory_bad_token_404():
    assert _client().get("/api/community/coaches?token=nope").status_code == 404
