"""After a merge, the old address reaches the survivor at every entry point.
Spec: docs/superpowers/specs/2026-09-26-merge-two-people-design.md"""
import json
import sqlite3

import pytest

SECRET = "test-secret"
AOL, GMAIL = "mel@aol.com", "mel@gmail.com"


@pytest.fixture
def client(monkeypatch, tmp_path):
    import app as appmod
    monkeypatch.setattr(appmod, "LOG_DB", str(tmp_path / "chat_log.db"))
    appmod._init_auth_tables()
    monkeypatch.setattr(appmod, "CONSOLE_SECRET", SECRET)
    monkeypatch.setattr(appmod, "_client_login_enabled", lambda: True)
    appmod.app.config["TESTING"] = True
    from dashboard import person_aliases as pa
    appmod._init_people_table()
    cx = sqlite3.connect(appmod.LOG_DB)
    cx.execute("INSERT INTO people (id, email, name, roles, tags, created_at, updated_at) "
               "VALUES (2, ?, 'Mel Palmer', '[\"client\"]', '[\"b\"]', 't', 't')", (GMAIL,))
    pa.init_tables(cx)
    pa.add_alias(cx, AOL, GMAIL, 1)
    cx.commit()
    cx.close()
    return appmod.app.test_client(), appmod


def _cx(appmod):
    return sqlite3.connect(appmod.LOG_DB)


def test_old_portal_link_opens_the_survivors_page(client):
    c, appmod = client
    from dashboard import client_portal as cp
    from dashboard import person_aliases as pa
    cx = _cx(appmod)
    cp.init_client_portal_table(cx)
    cp.upsert_portal(cx, GMAIL, "Mel Palmer", {"greeting": "Kept page"})
    pa.add_token_alias(cx, cp._hash("old-aol-token"), GMAIL, 1)
    cx.commit()
    got = cp.get_portal_by_token(cx, "old-aol-token")
    assert got["email"] == GMAIL and got["content"]["greeting"] == "Kept page"
    r = c.get("/api/portal/old-aol-token")
    assert r.status_code == 200


def test_sign_in_link_for_old_address_signs_in_the_survivor(client, monkeypatch):
    c, appmod = client
    from dashboard import portal_identity as pi
    made, sent = [], []
    monkeypatch.setattr(pi, "create_client_magic_link",
                        lambda cx, pid, email: made.append(pid) or "magic")
    monkeypatch.setattr(appmod, "_send_full_report_email",
                        lambda to, *a, **k: sent.append(to) or ("test", None))
    monkeypatch.setattr(appmod, "_equalise_response_time", lambda *a: None)
    c.post("/portal/login-request", json={"email": "Mel@AOL.com"})
    assert made == [2]
    assert sent == [AOL]


def test_verified_email_and_password_lookups_resolve(client):
    pytest.importorskip("argon2")          # installed in CI (requirements.txt), not always locally
    _, appmod = client
    from dashboard import portal_auth as pa_
    cx = _cx(appmod)
    assert pa_.person_by_verified_email(cx, AOL) == 2


def test_member_check_follows_the_alias(client, monkeypatch):
    _, appmod = client
    monkeypatch.setattr(appmod, "_active_membership_for_email", lambda e: e == GMAIL)
    monkeypatch.setattr(appmod, "membership_category", lambda e: "full")
    assert appmod._is_paid_member(AOL) is True


def test_sync_adds_only_tags_to_the_survivor(client):
    _, appmod = client
    cx = _cx(appmod)
    cx.row_factory = sqlite3.Row
    before = dict(cx.execute("SELECT * FROM people WHERE id=2").fetchone())
    appmod._upsert_person_additive(cx, {"email": AOL, "tags": ["x", "merged-into-2"],
                                        "dnd": True, "email_dnd": "active",
                                        "name": "Someone Else", "phone": "555"})
    cx.commit()
    assert cx.execute("SELECT COUNT(*) FROM people").fetchone()[0] == 1
    after = dict(cx.execute("SELECT * FROM people WHERE id=2").fetchone())
    assert sorted(json.loads(after["tags"])) == ["b", "x"]
    for k in before:
        if k not in ("tags", "updated_at"):
            assert after[k] == before[k], k


def test_person_creating_paths_return_the_survivor(client):
    _, appmod = client
    from dashboard import customers
    from dashboard import portal_identity as pi
    cx = _cx(appmod)
    assert customers.find_or_create_by_email(cx, email=AOL) == 2
    assert pi._get_or_create_person(cx, AOL)[0] == 2
    assert cx.execute("SELECT COUNT(*) FROM people").fetchone()[0] == 1


def test_console_search_by_old_address_finds_the_survivor(client):
    c, _ = client
    r = c.get(f"/api/people?q={AOL}", headers={"X-Console-Key": SECRET})
    ids = [p["id"] for p in r.get_json()["people"]]
    assert ids == [2]


def test_canonical_helper_survives_a_missing_table(client, tmp_path, monkeypatch):
    _, appmod = client
    monkeypatch.setattr(appmod, "LOG_DB", str(tmp_path / "empty.db"))
    assert appmod._canonical_email(" Mel@AOL.com ") == AOL


# ── Review round 1, 2026-09-27 ──────────────────────────────────────────────────────

def test_active_membership_follows_the_alias(client):
    _, appmod = client
    cx = _cx(appmod)
    appmod.init_membership_tables(cx)
    cx.execute("INSERT INTO memberships (id, email, granted_at, expires_at, source) "
               "VALUES ('m1', ?, '2026-01-01', '2999-01-01', 'test')", (GMAIL,))
    cx.commit()
    assert appmod._active_membership_for_email(AOL)["email"] == GMAIL


def test_practice_better_sync_never_creates_the_old_person(client, monkeypatch):
    _, appmod = client
    cx = _cx(appmod)
    before = cx.execute("SELECT COUNT(*) FROM people").fetchone()[0]
    rows = [{"email": AOL, "first": "Mel", "last": "Palmer", "phone": "", "pb_id": "pb1",
             "pb_tags": ["pb:client"]}]
    appmod._pb_upsert_people_rows(rows)
    after = cx.execute("SELECT COUNT(*) FROM people").fetchone()[0]
    assert after == before
    tags = json.loads(cx.execute("SELECT tags FROM people WHERE id=2").fetchone()[0])
    assert "pb:client" in tags


def test_old_link_opens_a_page_stored_with_other_case(client):
    _, appmod = client
    from dashboard import client_portal as cp
    from dashboard import person_aliases as pa
    cx = _cx(appmod)
    cp.init_client_portal_table(cx)
    cx.execute("INSERT INTO client_portals (token_hash, email, name, content_json, created_at, "
               "updated_at) VALUES ('h-kept', ' Mel@Gmail.com', 'Mel', '{\"greeting\":\"Hi\"}', 't', 't')")
    pa.add_token_alias(cx, cp._hash("old-token"), GMAIL, 1)
    cx.commit()
    got = cp.get_portal_by_token(cx, "old-token")
    assert got and got["content"]["greeting"] == "Hi"


def test_links_and_sessions_from_before_a_merge_reach_the_survivor(client):
    """auth_tokens.extra remembers the merged person's number (review round 3)."""
    _, appmod = client
    from dashboard import portal_identity as pi
    cx = _cx(appmod)
    cx.execute("INSERT INTO person_merges (survivor_person_id, merged_person_id, survivor_email, "
               "merged_email, applied_at) VALUES (2, 1, ?, ?, 't')", (GMAIL, AOL))
    cx.commit()
    session = pi.create_client_session(cx, 1, AOL)
    got = pi.identity_from_session(cx, session)
    assert got and got.person_id == 2 and got.email == GMAIL
    link = pi.create_client_magic_link(cx, 1, AOL)
    assert pi.consume_client_magic_link(cx, link) == 2


def test_a_session_after_an_undo_is_the_merged_person_again(client):
    _, appmod = client
    from dashboard import portal_identity as pi
    cx = _cx(appmod)
    cx.execute("INSERT INTO people (id, email, name, roles, tags, created_at, updated_at) "
               "VALUES (1, ?, 'Mel', '[\"client\"]', '[]', 't', 't')", (AOL,))
    cx.execute("INSERT INTO person_merges (survivor_person_id, merged_person_id, survivor_email, "
               "merged_email, applied_at, undone_at) VALUES (2, 1, ?, ?, 't', 'u')", (GMAIL, AOL))
    cx.commit()
    got = pi.identity_from_session(cx, pi.create_client_session(cx, 1, AOL))
    assert got and got.person_id == 1
