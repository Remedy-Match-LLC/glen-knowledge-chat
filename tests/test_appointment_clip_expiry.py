"""Appointment voice clips: deleted 90 days after sending, and on client erasure.

Glen, 2026-10-27, sales tab (brief: sales/plans/2026-10-27-five-element-voice-opt-in-brief.md,
"Also: appointment voice clips"). The file goes and audio_filename is cleared. The
message text stays.
"""
import importlib
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dashboard import appointment_proposals as ap

NOW = datetime(2026, 10, 27, 12, 0, tzinfo=timezone.utc)
CLIENT = "client@x.com"
OTHER = "other@x.com"


def _cx(tmp_path):
    cx = sqlite3.connect(str(tmp_path / "t.db"))
    ap.init_tables(cx)
    return cx


def _proposal(cx, email=CLIENT):
    return ap.create(cx, email=email, session_type="evox", start="2026-11-01T09:00:00",
                     proposed_by="client")


def _clip(cx, assets, pid, *, days_ago, text="see clip", sender="client", name=None):
    name = name or f"appt-{pid}-{('%024x' % (days_ago * 7919 + pid))}.webm"
    (assets / name).write_bytes(b"audio")
    sent = (NOW - timedelta(days=days_ago)).isoformat()
    cx.execute("INSERT INTO appointment_proposal_messages "
               "(proposal_id,sender,text,audio_filename,created_at) VALUES (?,?,?,?,?)",
               (pid, sender, text, name, sent))
    cx.commit()
    return name


# ---- 90 days ---------------------------------------------------------------------------

def test_a_clip_older_than_90_days_is_deleted_and_its_text_stays(tmp_path):
    cx = _cx(tmp_path)
    assets = tmp_path / "assets"
    assets.mkdir()
    pid = _proposal(cx)
    old = _clip(cx, assets, pid, days_ago=91, text="the old one")
    assert ap.expire_clips(cx, assets, now=NOW) == 1
    assert not (assets / old).exists()
    row = cx.execute("SELECT text, audio_filename FROM appointment_proposal_messages").fetchone()
    assert row == ("the old one", "")


def test_a_clip_younger_than_90_days_stays(tmp_path):
    cx = _cx(tmp_path)
    assets = tmp_path / "assets"
    assets.mkdir()
    pid = _proposal(cx)
    young = _clip(cx, assets, pid, days_ago=89)
    assert ap.expire_clips(cx, assets, now=NOW) == 0
    assert (assets / young).exists()
    assert cx.execute("SELECT audio_filename FROM appointment_proposal_messages"
                      ).fetchone()[0] == young


def test_the_boundary_is_exactly_90_days(tmp_path):
    cx = _cx(tmp_path)
    assets = tmp_path / "assets"
    assets.mkdir()
    pid = _proposal(cx)
    at = _clip(cx, assets, pid, days_ago=90)
    assert ap.expire_clips(cx, assets, now=NOW) == 1
    assert not (assets / at).exists()
    assert ap.CLIP_KEEP_DAYS == 90


def test_staff_clips_expire_too(tmp_path):
    cx = _cx(tmp_path)
    assets = tmp_path / "assets"
    assets.mkdir()
    pid = _proposal(cx)
    name = _clip(cx, assets, pid, days_ago=120, sender="rae")
    assert ap.expire_clips(cx, assets, now=NOW) == 1
    assert not (assets / name).exists()


def test_a_missing_file_still_clears_the_row(tmp_path):
    cx = _cx(tmp_path)
    assets = tmp_path / "assets"
    assets.mkdir()
    pid = _proposal(cx)
    name = _clip(cx, assets, pid, days_ago=100)
    (assets / name).unlink()
    assert ap.expire_clips(cx, assets, now=NOW) == 1
    assert cx.execute("SELECT audio_filename FROM appointment_proposal_messages"
                      ).fetchone()[0] == ""


def test_a_name_this_code_did_not_write_is_never_deleted(tmp_path):
    """A traversal or foreign name stays, file and row, and is logged."""
    cx = _cx(tmp_path)
    assets = tmp_path / "assets"
    assets.mkdir()
    pid = _proposal(cx)
    keep = tmp_path / "precious.txt"
    keep.write_text("x")
    cx.execute("INSERT INTO appointment_proposal_messages "
               "(proposal_id,sender,text,audio_filename,created_at) VALUES (?,?,?,?,?)",
               (pid, "client", "", "../precious.txt", (NOW - timedelta(days=200)).isoformat()))
    cx.commit()
    assert ap.expire_clips(cx, assets, now=NOW) == 0
    assert keep.exists()
    assert cx.execute("SELECT audio_filename FROM appointment_proposal_messages"
                      ).fetchone()[0] == "../precious.txt"


def test_a_file_that_will_not_delete_keeps_its_row(tmp_path, monkeypatch):
    cx = _cx(tmp_path)
    assets = tmp_path / "assets"
    assets.mkdir()
    pid = _proposal(cx)
    name = _clip(cx, assets, pid, days_ago=100)

    def refuse(self, *a, **k):
        raise PermissionError("read-only")

    monkeypatch.setattr(Path, "unlink", refuse)
    assert ap.expire_clips(cx, assets, now=NOW) == 0
    assert cx.execute("SELECT audio_filename FROM appointment_proposal_messages"
                      ).fetchone()[0] == name


def test_an_unreadable_date_falls_back_to_the_files_own_date(tmp_path):
    import os
    cx = _cx(tmp_path)
    assets = tmp_path / "assets"
    assets.mkdir()
    pid = _proposal(cx)
    old, young = f"appt-{pid}-{'a' * 24}.webm", f"appt-{pid}-{'c' * 24}.webm"
    for name, days in ((old, 100), (young, 10)):
        (assets / name).write_bytes(b"a")
        t = (NOW - timedelta(days=days)).timestamp()
        os.utime(assets / name, (t, t))
        cx.execute("INSERT INTO appointment_proposal_messages "
                   "(proposal_id,sender,text,audio_filename,created_at) VALUES (?,?,?,?,?)",
                   (pid, "client", "", name, "not a date"))
    cx.commit()
    assert ap.expire_clips(cx, assets, now=NOW) == 1
    assert not (assets / old).exists()
    assert (assets / young).exists()


def test_a_name_from_another_proposal_is_never_deleted(tmp_path):
    """A row on proposal A naming proposal B's clip must not delete B's file."""
    cx = _cx(tmp_path)
    assets = tmp_path / "assets"
    assets.mkdir()
    a = _proposal(cx)
    b = _proposal(cx, OTHER)
    b_clip = _clip(cx, assets, b, days_ago=1)
    cx.execute("INSERT INTO appointment_proposal_messages "
               "(proposal_id,sender,text,audio_filename,created_at) VALUES (?,?,?,?,?)",
               (a, "client", "", b_clip, (NOW - timedelta(days=200)).isoformat()))
    cx.commit()
    assert ap.expire_clips(cx, assets, now=NOW) == 0
    assert ap.erase_client_clips(cx, assets, [CLIENT]) == 0
    assert (assets / b_clip).exists()


def test_a_second_run_finds_nothing_more(tmp_path):
    cx = _cx(tmp_path)
    assets = tmp_path / "assets"
    assets.mkdir()
    pid = _proposal(cx)
    _clip(cx, assets, pid, days_ago=95)
    assert ap.expire_clips(cx, assets, now=NOW) == 1
    assert ap.expire_clips(cx, assets, now=NOW) == 0


def test_a_z_suffixed_or_naive_date_is_read_as_utc(tmp_path):
    cx = _cx(tmp_path)
    assets = tmp_path / "assets"
    assets.mkdir()
    pid = _proposal(cx)
    for i, ts in enumerate(("2026-01-01T00:00:00Z", "2026-01-01T00:00:00")):
        name = f"appt-{pid}-{('%024x' % (i + 1))}.webm"
        (assets / name).write_bytes(b"a")
        cx.execute("INSERT INTO appointment_proposal_messages "
                   "(proposal_id,sender,text,audio_filename,created_at) VALUES (?,?,?,?,?)",
                   (pid, "client", "", name, ts))
    cx.commit()
    assert ap.expire_clips(cx, assets, now=NOW) == 2


# ---- erasure ---------------------------------------------------------------------------

def test_erasure_deletes_every_clip_on_the_clients_threads_only(tmp_path):
    cx = _cx(tmp_path)
    assets = tmp_path / "assets"
    assets.mkdir()
    mine = _proposal(cx)
    theirs = _proposal(cx, OTHER)
    a = _clip(cx, assets, mine, days_ago=1)
    b = _clip(cx, assets, mine, days_ago=2, sender="glen")
    c = _clip(cx, assets, theirs, days_ago=1)
    assert ap.count_client_clips(cx, [CLIENT]) == 2
    assert ap.erase_client_clips(cx, assets, [CLIENT.upper()]) == 2
    cx.commit()
    assert not (assets / a).exists() and not (assets / b).exists()
    assert (assets / c).exists()
    assert ap.count_client_clips(cx, [CLIENT]) == 0
    assert ap.count_client_clips(cx, [OTHER]) == 1


def test_erasure_with_no_addresses_touches_nothing(tmp_path):
    cx = _cx(tmp_path)
    assets = tmp_path / "assets"
    assets.mkdir()
    c = _clip(cx, assets, _proposal(cx), days_ago=1)
    assert ap.erase_client_clips(cx, assets, ["", None]) == 0
    assert (assets / c).exists()


# ---- the console erasure route and the daily job ----------------------------------------

KEY = "test-console-secret"


def _reload(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CONSOLE_SECRET", KEY)
    monkeypatch.setenv("CLIENT_ERASURE_ENABLED", "1")
    import dashboard as _dash
    monkeypatch.setattr(_dash, "CONSOLE_SECRET", KEY)
    import app as appmod
    importlib.reload(appmod)
    appmod.app.config["TESTING"] = True
    return appmod


def test_the_console_erasure_deletes_the_clients_clips(monkeypatch, tmp_path):
    appmod = _reload(monkeypatch, tmp_path)
    assets = appmod._PORTAL_ASSETS_DIR
    assets.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(appmod.LOG_DB) as cx:
        ap.init_tables(cx)
        mine = _proposal(cx)
        theirs = _proposal(cx, OTHER)
        a = _clip(cx, assets, mine, days_ago=1)
        c = _clip(cx, assets, theirs, days_ago=1)
    client = appmod.app.test_client()
    h = {"X-Console-Key": KEY}
    pv = client.post("/api/console/client-erasure/preview", json={"email": CLIENT}, headers=h)
    assert pv.get_json()["tables"].get("appointment_voice_clips") == 1
    r = client.post("/api/console/client-erasure/erase",
                    json={"email": CLIENT, "confirm": CLIENT}, headers=h)
    assert r.status_code == 200
    assert r.get_json()["removed"].get("appointment_voice_clips") == 1
    assert not (assets / a).exists()
    assert (assets / c).exists()
    with sqlite3.connect(appmod.LOG_DB) as cx:
        assert ap.count_client_clips(cx, [CLIENT]) == 0


def test_a_refused_erasure_deletes_no_clip(monkeypatch, tmp_path):
    appmod = _reload(monkeypatch, tmp_path)
    assets = appmod._PORTAL_ASSETS_DIR
    assets.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(appmod.LOG_DB) as cx:
        ap.init_tables(cx)
        a = _clip(cx, assets, _proposal(cx), days_ago=1)
    r = appmod.app.test_client().post("/api/console/client-erasure/erase",
                                      json={"email": CLIENT, "confirm": "someone@else.com"},
                                      headers={"X-Console-Key": KEY})
    assert r.status_code == 400
    assert (assets / a).exists()


def test_the_daily_job_expires_old_clips(monkeypatch, tmp_path):
    appmod = _reload(monkeypatch, tmp_path)
    assets = appmod._PORTAL_ASSETS_DIR
    assets.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(appmod.LOG_DB) as cx:
        ap.init_tables(cx)
        pid = _proposal(cx)
        old = f"appt-{pid}-{'b' * 24}.webm"
        (assets / old).write_bytes(b"a")
        cx.execute("INSERT INTO appointment_proposal_messages "
                   "(proposal_id,sender,text,audio_filename,created_at) VALUES (?,?,?,?,?)",
                   (pid, "client", "", old,
                    (datetime.now(timezone.utc) - timedelta(days=200)).isoformat()))
        cx.commit()
    appmod._expire_appointment_clips()
    assert not (assets / old).exists()
    with sqlite3.connect(appmod.LOG_DB) as cx:
        assert cx.execute("SELECT audio_filename FROM appointment_proposal_messages"
                          ).fetchone()[0] == ""


def test_an_erasure_stops_when_a_clip_will_not_delete(monkeypatch, tmp_path):
    appmod = _reload(monkeypatch, tmp_path)
    assets = appmod._PORTAL_ASSETS_DIR
    assets.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(appmod.LOG_DB) as cx:
        ap.init_tables(cx)
        a = _clip(cx, assets, _proposal(cx), days_ago=1)
        cx.execute("CREATE TABLE IF NOT EXISTS intake_responses (email TEXT, answers_json TEXT)")
        cx.execute("INSERT INTO intake_responses (email, answers_json) VALUES (?, '{}')", (CLIENT,))
        cx.commit()

    def refuse(self, *a, **k):
        raise PermissionError("read-only")

    monkeypatch.setattr(Path, "unlink", refuse)
    r = appmod.app.test_client().post("/api/console/client-erasure/erase",
                                      json={"email": CLIENT, "confirm": CLIENT},
                                      headers={"X-Console-Key": KEY})
    assert r.status_code == 500
    assert r.get_json()["ok"] is False
    with sqlite3.connect(appmod.LOG_DB) as cx:
        assert cx.execute("SELECT COUNT(*) FROM intake_responses WHERE email=?",
                          (CLIENT,)).fetchone()[0] == 1


def test_the_daily_job_is_scheduled():
    import ast
    src = (Path(__file__).resolve().parent.parent / "app.py").read_text()
    fn = next(n for n in ast.walk(ast.parse(src))
              if isinstance(n, ast.FunctionDef) and n.name == "_start_scheduler")
    calls = [c for c in ast.walk(fn) if isinstance(c, ast.Call)
             and getattr(c.func, "attr", "") == "add_job" and c.args
             and getattr(c.args[0], "id", "") == "_expire_appointment_clips"]
    assert len(calls) == 1
    kw = {k.arg: getattr(k.value, "value", None) for k in calls[0].keywords}
    assert calls[0].args[1].value == "cron"
    assert kw["hour"] == 14 and kw["minute"] == 0


import pytest


@pytest.mark.parametrize("bad", ["appt-{pid}-../../precious.txt",
                                 "appt-{pid}-aaaaaaaaaaaaaaaaaaaaaaaa.webm/../../precious.txt",
                                 "appt-{pid}-aaaaaaaaaaaaaaaaaaaaaaaa.webm.txt",
                                 "appt-{pid}-aaaaaaaaaaaa/aaaaaaaaaaa.webm"])
def test_a_lookalike_name_is_never_deleted(tmp_path, bad):
    cx = _cx(tmp_path)
    assets = tmp_path / "assets"
    assets.mkdir()
    pid = _proposal(cx)
    name = bad.format(pid=pid)
    cx.execute("INSERT INTO appointment_proposal_messages "
               "(proposal_id,sender,text,audio_filename,created_at) VALUES (?,?,?,?,?)",
               (pid, "client", "", name, (NOW - timedelta(days=200)).isoformat()))
    cx.commit()
    assert ap.expire_clips(cx, assets, now=NOW) == 0
    assert cx.execute("SELECT audio_filename FROM appointment_proposal_messages"
                      ).fetchone()[0] == name


def test_the_console_erasure_covers_a_merged_address(monkeypatch, tmp_path):
    appmod = _reload(monkeypatch, tmp_path)
    assets = appmod._PORTAL_ASSETS_DIR
    assets.mkdir(parents=True, exist_ok=True)
    from dashboard import person_aliases as pal
    with sqlite3.connect(appmod.LOG_DB) as cx:
        ap.init_tables(cx)
        pal.init_tables(cx)
        pal.add_alias(cx, "old@x.com", CLIENT, 1)
        cx.commit()
        a = _clip(cx, assets, _proposal(cx, "old@x.com"), days_ago=1)
    r = appmod.app.test_client().post("/api/console/client-erasure/erase",
                                      json={"email": CLIENT, "confirm": CLIENT},
                                      headers={"X-Console-Key": KEY})
    assert r.status_code == 200
    assert not (assets / a).exists()


def test_a_partial_clip_failure_still_records_the_clips_that_went(monkeypatch, tmp_path):
    appmod = _reload(monkeypatch, tmp_path)
    assets = appmod._PORTAL_ASSETS_DIR
    assets.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(appmod.LOG_DB) as cx:
        ap.init_tables(cx)
        pid = _proposal(cx)
        good = _clip(cx, assets, pid, days_ago=1)
        stuck = _clip(cx, assets, pid, days_ago=2)
    real_unlink = Path.unlink

    def unlink(self, *a, **k):
        if self.name == stuck:
            raise PermissionError("read-only")
        return real_unlink(self, *a, **k)

    monkeypatch.setattr(Path, "unlink", unlink)
    r = appmod.app.test_client().post("/api/console/client-erasure/erase",
                                      json={"email": CLIENT, "confirm": CLIENT},
                                      headers={"X-Console-Key": KEY})
    assert r.status_code == 500
    assert not (assets / good).exists() and (assets / stuck).exists()
    from dashboard import client_erasure as _ce
    with sqlite3.connect(appmod.LOG_DB) as cx:
        assert "appointment_voice_clips" in str(_ce.erasures_for(cx, CLIENT))


def test_a_clip_another_run_already_cleared_is_not_counted_twice(tmp_path):
    cx = _cx(tmp_path)
    assets = tmp_path / "assets"
    assets.mkdir()
    pid = _proposal(cx)
    name = _clip(cx, assets, pid, days_ago=95)
    mid = cx.execute("SELECT id FROM appointment_proposal_messages").fetchone()[0]
    cx.execute("UPDATE appointment_proposal_messages SET audio_filename='' WHERE id=?", (mid,))
    cx.commit()
    assert ap._delete_clip(cx, assets, mid, pid, name) is False


def test_an_erasure_that_fails_after_the_clips_still_records_them(monkeypatch, tmp_path):
    appmod = _reload(monkeypatch, tmp_path)
    assets = appmod._PORTAL_ASSETS_DIR
    assets.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(appmod.LOG_DB) as cx:
        ap.init_tables(cx)
        a = _clip(cx, assets, _proposal(cx), days_ago=1)
    from dashboard import client_erasure as _ce

    def boom(*a, **k):
        raise RuntimeError("db down")

    monkeypatch.setattr(_ce, "erase", boom)
    appmod.app.config["PROPAGATE_EXCEPTIONS"] = False
    r = appmod.app.test_client().post("/api/console/client-erasure/erase",
                                      json={"email": CLIENT, "confirm": CLIENT},
                                      headers={"X-Console-Key": KEY})
    assert r.status_code == 500
    assert not (assets / a).exists()
    with sqlite3.connect(appmod.LOG_DB) as cx:
        prior = _ce.erasures_for(cx, CLIENT)
    assert prior and "appointment_voice_clips" in str(prior)


# ---- erasure deletes the message text too (Glen, 2026-10-27, sales tab) ---------------

def _msg(cx, pid, text, sender="client"):
    cx.execute("INSERT INTO appointment_proposal_messages "
               "(proposal_id,sender,text,audio_filename,created_at) VALUES (?,?,?,?,?)",
               (pid, sender, text, "", NOW.isoformat()))
    cx.commit()


def test_the_console_erasure_deletes_the_message_text(monkeypatch, tmp_path):
    appmod = _reload(monkeypatch, tmp_path)
    assets = appmod._PORTAL_ASSETS_DIR
    assets.mkdir(parents=True, exist_ok=True)
    from dashboard import person_aliases as pal
    with sqlite3.connect(appmod.LOG_DB) as cx:
        ap.init_tables(cx)
        pal.init_tables(cx)
        pal.add_alias(cx, "old@x.com", CLIENT, 1)
        cx.commit()
        mine, alias, theirs = _proposal(cx), _proposal(cx, "old@x.com"), _proposal(cx, OTHER)
        _msg(cx, mine, "my knee hurts")
        _msg(cx, mine, "Rae here", sender="rae")
        _msg(cx, alias, "from my old address")
        _msg(cx, theirs, "someone else")
        clip = _clip(cx, assets, mine, days_ago=1)
    client = appmod.app.test_client()
    h = {"X-Console-Key": KEY}
    pv = client.post("/api/console/client-erasure/preview", json={"email": CLIENT},
                     headers=h).get_json()
    assert pv["tables"]["appointment_messages"] == 4
    r = client.post("/api/console/client-erasure/erase",
                    json={"email": CLIENT, "confirm": CLIENT}, headers=h)
    assert r.status_code == 200
    assert r.get_json()["removed"]["appointment_messages"] == 4
    assert r.get_json()["removed"]["appointment_voice_clips"] == 1
    assert not (assets / clip).exists()
    with sqlite3.connect(appmod.LOG_DB) as cx:
        texts = [t for (t,) in cx.execute("SELECT text FROM appointment_proposal_messages")]
        assert texts == ["someone else"]
        # The appointments themselves stay.
        assert cx.execute("SELECT COUNT(*) FROM appointment_proposals").fetchone()[0] == 3
        from dashboard import client_erasure as _ce
        assert "appointment_messages" in str(_ce.erasures_for(cx, CLIENT))


def test_a_failed_erasure_keeps_the_message_text(monkeypatch, tmp_path):
    appmod = _reload(monkeypatch, tmp_path)
    with sqlite3.connect(appmod.LOG_DB) as cx:
        ap.init_tables(cx)
        _msg(cx, _proposal(cx), "keep me")
    from dashboard import client_erasure as _ce

    def boom(*a, **k):
        raise RuntimeError("db down")

    monkeypatch.setattr(_ce, "erase", boom)
    appmod.app.config["PROPAGATE_EXCEPTIONS"] = False
    r = appmod.app.test_client().post("/api/console/client-erasure/erase",
                                      json={"email": CLIENT, "confirm": CLIENT},
                                      headers={"X-Console-Key": KEY})
    assert r.status_code == 500
    with sqlite3.connect(appmod.LOG_DB) as cx:
        assert [t for (t,) in cx.execute("SELECT text FROM appointment_proposal_messages")] \
            == ["keep me"]


def test_a_clip_that_will_not_delete_keeps_the_message_text(monkeypatch, tmp_path):
    appmod = _reload(monkeypatch, tmp_path)
    assets = appmod._PORTAL_ASSETS_DIR
    assets.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(appmod.LOG_DB) as cx:
        ap.init_tables(cx)
        pid = _proposal(cx)
        _msg(cx, pid, "keep me")
        _clip(cx, assets, pid, days_ago=1)

    def refuse(self, *a, **k):
        raise PermissionError("read-only")

    monkeypatch.setattr(Path, "unlink", refuse)
    r = appmod.app.test_client().post("/api/console/client-erasure/erase",
                                      json={"email": CLIENT, "confirm": CLIENT},
                                      headers={"X-Console-Key": KEY})
    assert r.status_code == 500
    with sqlite3.connect(appmod.LOG_DB) as cx:
        assert cx.execute("SELECT COUNT(*) FROM appointment_proposal_messages").fetchone()[0] == 2


def test_message_erasure_with_no_addresses_touches_nothing(tmp_path):
    cx = _cx(tmp_path)
    _msg(cx, _proposal(cx), "x")
    assert ap.erase_client_messages(cx, ["", None]) == 0
    assert ap.count_client_messages(cx, []) == 0
    assert cx.execute("SELECT COUNT(*) FROM appointment_proposal_messages").fetchone()[0] == 1
