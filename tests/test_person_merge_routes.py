"""Console routes for merging two people: preview, apply, undo.
Spec: docs/superpowers/specs/2026-09-26-merge-two-people-design.md"""
import json
import sqlite3

import pytest

SECRET = "test-secret"
VA = "va-token-merge"
AOL, GMAIL = "mel@aol.com", "mel@gmail.com"
OWNER = {"X-Console-Key": SECRET}


@pytest.fixture
def client(monkeypatch, tmp_path):
    import app as appmod
    monkeypatch.setattr(appmod, "LOG_DB", str(tmp_path / "chat_log.db"))
    appmod._init_auth_tables()
    appmod._init_people_table()
    appmod._init_person_merge_tables()
    monkeypatch.setattr(appmod, "CONSOLE_SECRET", SECRET)
    appmod.app.config["TESTING"] = True
    from dashboard import person_merge_evidence as ev
    monkeypatch.setattr(ev, "gmail_last_reply", lambda email, service=None: None)
    ghl = []
    monkeypatch.setattr(appmod, "ghl_mark_merged",
                        lambda e, s, stop: ghl.append(("mark", e, s, stop)) or ("c1", None))
    monkeypatch.setattr(appmod, "ghl_unmark_merged",
                        lambda e, s: ghl.append(("unmark", e, s)) or ("c1", None))
    cx = sqlite3.connect(appmod.LOG_DB)
    cx.execute("INSERT INTO people (id, email, name, tags, created_at, updated_at) "
               "VALUES (1, ?, 'Mel Palmer', '[\"a\"]', 't', 't')", (AOL,))
    cx.execute("INSERT INTO people (id, email, name, tags, created_at, updated_at) "
               "VALUES (2, ?, '', '[\"b\"]', 't', 't')", (GMAIL,))
    cx.commit()
    cx.close()
    appmod._init_workspace_schema()
    with appmod.db.connect(appmod.LOG_DB) as c:
        c.execute("INSERT INTO workspace_users (name, display_name, scope) VALUES (?,?,?)",
                  ("shaira", "shaira", "workspace:shaira"))
        uid = c.execute("SELECT id FROM workspace_users WHERE name='shaira'").fetchone()[0]
        c.execute("INSERT INTO access_tokens (token, user_id) VALUES (?,?)", (VA, uid))
        c.commit()
    return appmod.app.test_client(), appmod, ghl


def _people(appmod):
    with sqlite3.connect(appmod.LOG_DB) as cx:
        return [r[0] for r in cx.execute("SELECT id FROM people ORDER BY id")]


def test_preview_writes_nothing_and_suggests(client):
    c, appmod, _ = client
    r = c.get("/api/console/people/merge/preview?survivor=2&merged=1", headers=OWNER)
    assert r.status_code == 200
    d = r.get_json()
    assert d["survivor"]["email"] == GMAIL and d["merged"]["email"] == AOL
    assert set(d["evidence"]) == {GMAIL, AOL}
    assert d["suggestion"]["survivor"] in (GMAIL, AOL)
    assert d["suggestion"]["survivor_id"] in (1, 2)
    assert _people(appmod) == [1, 2]


def test_preview_needs_a_console_key(client):
    c, _, _ = client
    assert c.get("/api/console/people/merge/preview?survivor=2&merged=1").status_code == 401


def test_a_va_cannot_apply_or_undo(client):
    c, appmod, _ = client
    r = c.post("/api/console/people/merge", json={"survivor_id": 2, "merged_id": 1,
                                                  "mail_old": "stop"},
               headers={"X-Console-Key": VA})
    assert r.status_code == 403
    assert _people(appmod) == [1, 2]
    assert c.post("/api/console/people/merge/1/undo",
                  headers={"X-Console-Key": VA}).status_code == 403


def test_owner_applies_then_a_second_apply_is_refused_then_undo(client):
    c, appmod, ghl = client
    r = c.post("/api/console/people/merge", json={"survivor_id": 2, "merged_id": 1,
                                                  "mail_old": "stop"}, headers=OWNER)
    assert r.status_code == 200, r.get_data(as_text=True)
    mid = r.get_json()["merge_id"]
    assert _people(appmod) == [2]
    assert ghl == [("mark", AOL, 2, True)]
    with sqlite3.connect(appmod.LOG_DB) as cx:
        cx.execute("INSERT INTO people (id, email, name, tags, created_at, updated_at) "
                   "VALUES (1, ?, 'x', '[]', 't', 't')", (AOL,))
    r2 = c.post("/api/console/people/merge", json={"survivor_id": 2, "merged_id": 1,
                                                   "mail_old": "stop"}, headers=OWNER)
    assert r2.status_code == 409
    with sqlite3.connect(appmod.LOG_DB) as cx:
        cx.execute("DELETE FROM people WHERE id=1")
    u = c.post(f"/api/console/people/merge/{mid}/undo", headers=OWNER)
    assert u.status_code == 200, u.get_data(as_text=True)
    assert _people(appmod) == [1, 2]
    assert ghl[-1] == ("unmark", AOL, 2)
    rec = c.get(f"/api/console/people/merges/{mid}", headers=OWNER).get_json()
    assert rec["merge"]["undone_at"]


def test_a_gohighlevel_failure_does_not_undo_the_merge(client, monkeypatch):
    c, appmod, _ = client
    monkeypatch.setattr(appmod, "ghl_mark_merged", lambda e, s, stop: (None, "timeout"))
    r = c.post("/api/console/people/merge", json={"survivor_id": 2, "merged_id": 1,
                                                  "mail_old": "keep"}, headers=OWNER)
    assert r.status_code == 200
    assert r.get_json()["ghl_error"] == "timeout"
    assert _people(appmod) == [2]


def test_an_engine_failure_changes_nothing(client, monkeypatch):
    c, appmod, ghl = client

    def boom(cx, s, m):
        raise RuntimeError("injected")

    monkeypatch.setattr(appmod, "_merge_two_people", boom)
    r = c.post("/api/console/people/merge", json={"survivor_id": 2, "merged_id": 1,
                                                  "mail_old": "stop"}, headers=OWNER)
    assert r.status_code == 409
    assert "Nothing was changed" in r.get_json()["error"]
    assert _people(appmod) == [1, 2]
    assert ghl == []
    with sqlite3.connect(appmod.LOG_DB) as cx:
        assert cx.execute("SELECT COUNT(*) FROM person_merges").fetchone()[0] == 0
        assert cx.execute("SELECT COUNT(*) FROM email_aliases").fetchone()[0] == 0


# ── Review rounds 1 and 2, 2026-09-27 ───────────────────────────────────────────────

def test_a_blocked_merge_is_refused_and_named(client):
    c, appmod, ghl = client
    with sqlite3.connect(appmod.LOG_DB) as cx:
        cx.execute("CREATE TABLE coach_subscriptions (member_email TEXT PRIMARY KEY, status TEXT)")
        cx.execute("INSERT INTO coach_subscriptions VALUES (?, 'active')", (AOL,))
        cx.execute("INSERT INTO coach_subscriptions VALUES (?, 'cancelled')", (GMAIL,))
    p = c.get("/api/console/people/merge/preview?survivor=2&merged=1", headers=OWNER).get_json()
    assert p["blocked"] == ["coach_subscriptions"]
    r = c.post("/api/console/people/merge", json={"survivor_id": 2, "merged_id": 1,
                                                  "mail_old": "stop"}, headers=OWNER)
    assert r.status_code == 409
    assert r.get_json()["blocked"] == ["coach_subscriptions"]
    assert _people(appmod) == [1, 2] and ghl == []


def test_a_failed_gohighlevel_mark_is_retried_by_the_hourly_job(client, monkeypatch):
    c, appmod, ghl = client
    monkeypatch.setattr(appmod, "ghl_mark_merged", lambda e, s, stop: (None, "timeout"))
    mid = c.post("/api/console/people/merge", json={"survivor_id": 2, "merged_id": 1,
                                                    "mail_old": "stop"}, headers=OWNER).get_json()["merge_id"]
    with sqlite3.connect(appmod.LOG_DB) as cx:
        assert cx.execute("SELECT ghl_status FROM person_merges WHERE id=?", (mid,)).fetchone()[0] == "pending"
    calls = []
    monkeypatch.setattr(appmod, "ghl_mark_merged",
                        lambda e, s, stop: calls.append((e, s, stop)) or ("c1", None))
    appmod._run_person_merge_sweep()
    assert calls == [(AOL, 2, True)]
    with sqlite3.connect(appmod.LOG_DB) as cx:
        assert cx.execute("SELECT ghl_status FROM person_merges WHERE id=?", (mid,)).fetchone()[0] == "ok"
    appmod._run_person_merge_sweep()
    assert len(calls) == 1


def test_email_unsubscribe_and_bounce_tags_stay_with_the_old_address(client):
    """Glen, 2026-09-27: they describe the address, and the old address stops getting
    mail anyway. The survivor's own tags are untouched."""
    c, appmod, _ = client
    with sqlite3.connect(appmod.LOG_DB) as cx:
        cx.execute("UPDATE people SET tags=? WHERE id=1",
                   ('["vip","consent:unsubscribed","email bounced","Do Not Email"]',))
        cx.execute("UPDATE people SET tags=? WHERE id=2", ('["b","consent:opted-in"]',))
    r = c.post("/api/console/people/merge", json={"survivor_id": 2, "merged_id": 1,
                                                  "mail_old": "stop"}, headers=OWNER)
    assert r.status_code == 200, r.get_data(as_text=True)
    with sqlite3.connect(appmod.LOG_DB) as cx:
        tags = set(json.loads(cx.execute("SELECT tags FROM people WHERE id=2").fetchone()[0]))
    assert tags == {"b", "consent:opted-in", "vip"}


def test_the_hourly_sync_carries_no_unsubscribe_from_the_old_address(client):
    _, appmod, _ = client
    from dashboard import person_aliases as pa
    with sqlite3.connect(appmod.LOG_DB) as cx:
        pa.add_alias(cx, AOL, GMAIL, 1)
        cx.execute("DELETE FROM people WHERE id=1")
        cx.commit()
        appmod._upsert_person_additive(cx, {"email": AOL, "tags": ["consent:unsubscribed",
                                                                   "email bounced", "x"]})
        cx.commit()
        tags = set(json.loads(cx.execute("SELECT tags FROM people WHERE id=2").fetchone()[0]))
    assert tags == {"b", "x"}


def test_the_text_opt_out_tag_carries(client):
    """people-22, 2026-09-27: consent:sms-unsubscribed is the person's text opt-out and
    carries with opt_status. Glen ("no"): an email unsubscribe does not carry."""
    c, appmod, _ = client
    with sqlite3.connect(appmod.LOG_DB) as cx:
        cx.execute("UPDATE people SET tags=? WHERE id=1",
                   ('["consent:sms-unsubscribed","consent:unsubscribed"]',))
    r = c.post("/api/console/people/merge", json={"survivor_id": 2, "merged_id": 1,
                                                  "mail_old": "stop"}, headers=OWNER)
    assert r.status_code == 200
    with sqlite3.connect(appmod.LOG_DB) as cx:
        tags = set(json.loads(cx.execute("SELECT tags FROM people WHERE id=2").fetchone()[0]))
    assert "consent:sms-unsubscribed" in tags
    assert "consent:unsubscribed" not in tags


@pytest.mark.parametrize("survivor_tags,merged_tags,want", [
    ('["b"]', '["consent:opted-in"]', {"b", "consent:opted-in"}),
    ('["consent:unsubscribed"]', '["consent:opted-in"]', {"consent:unsubscribed"}),
    ('["consent:cold-no-consent"]', '["consent:opted-in"]', {"consent:opted-in"}),
])
def test_an_email_opt_in_carries_and_the_collapse_rule_applies(client, survivor_tags,
                                                               merged_tags, want):
    """Glen, 2026-09-27 via people-22: "yes", an opt-in carries. The survivor's own
    unsubscribe still beats it, and opted-in beats cold, as in the hourly sync."""
    c, appmod, _ = client
    with sqlite3.connect(appmod.LOG_DB) as cx:
        cx.execute("UPDATE people SET tags=? WHERE id=1", (merged_tags,))
        cx.execute("UPDATE people SET tags=? WHERE id=2", (survivor_tags,))
    r = c.post("/api/console/people/merge", json={"survivor_id": 2, "merged_id": 1,
                                                  "mail_old": "stop"}, headers=OWNER)
    assert r.status_code == 200
    with sqlite3.connect(appmod.LOG_DB) as cx:
        tags = set(json.loads(cx.execute("SELECT tags FROM people WHERE id=2").fetchone()[0]))
    assert tags == want


def test_the_survivor_does_not_take_the_old_gohighlevel_contact(client):
    c, appmod, _ = client
    with sqlite3.connect(appmod.LOG_DB) as cx:
        cx.execute("UPDATE people SET ghl_id='old-contact' WHERE id=1")
        cx.execute("UPDATE people SET ghl_id='' WHERE id=2")
    assert c.post("/api/console/people/merge", json={"survivor_id": 2, "merged_id": 1,
                                                     "mail_old": "stop"}, headers=OWNER).status_code == 200
    with sqlite3.connect(appmod.LOG_DB) as cx:
        assert cx.execute("SELECT ghl_id FROM people WHERE id=2").fetchone()[0] in ("", None)


def test_a_choice_unblocks_preview_and_apply(client):
    c, appmod, _ = client
    with sqlite3.connect(appmod.LOG_DB) as cx:
        cx.execute("DROP TABLE IF EXISTS affiliate_signups")
        cx.execute("CREATE TABLE affiliate_signups (id INTEGER PRIMARY KEY, email TEXT UNIQUE, slug TEXT)")
        cx.execute("INSERT INTO affiliate_signups VALUES (1, ?, 'mel-aol')", (AOL,))
        cx.execute("INSERT INTO affiliate_signups VALUES (2, ?, 'mel-gm')", (GMAIL,))
    p = c.get("/api/console/people/merge/preview?survivor=2&merged=1", headers=OWNER).get_json()
    assert p["needs_choice"] == ["affiliate_signups"] and "affiliate_signups" in p["blocked"]
    p2 = c.get("/api/console/people/merge/preview?survivor=2&merged=1"
               "&resolve=affiliate_signups:survivor", headers=OWNER).get_json()
    assert "affiliate_signups" not in p2["blocked"]
    assert c.post("/api/console/people/merge", json={"survivor_id": 2, "merged_id": 1,
                                                     "mail_old": "stop"}, headers=OWNER).status_code == 409
    r = c.post("/api/console/people/merge", json={"survivor_id": 2, "merged_id": 1, "mail_old": "stop",
                                                  "resolutions": {"affiliate_signups": "survivor"}},
               headers=OWNER)
    assert r.status_code == 200, r.get_data(as_text=True)
    with sqlite3.connect(appmod.LOG_DB) as cx:
        assert cx.execute("SELECT slug FROM affiliate_signups").fetchall() == [("mel-gm",)]
