"""The person merge engine: preview, apply, undo and the sweep.
Spec: docs/superpowers/specs/2026-09-26-merge-two-people-design.md"""
import json
import os
import sqlite3

import pytest

from dashboard import db
from dashboard import person_aliases as pa
from dashboard import person_merge as pm

MERGE_TABLES = ("person_merges", "email_aliases", "portal_token_aliases", "person_merge_changes")
DDL = [
    "CREATE TABLE people (id INTEGER PRIMARY KEY, email TEXT UNIQUE, name TEXT, tags TEXT)",
    "CREATE TABLE orders (id INTEGER PRIMARY KEY, email TEXT, person_id INTEGER "
    "REFERENCES people(id), total REAL)",
    "CREATE TABLE carts (email TEXT PRIMARY KEY, items TEXT, updated_at TEXT)",
    "CREATE TABLE biofield_reveals (id INTEGER PRIMARY KEY, email TEXT, scan_date TEXT, "
    "updated_at TEXT, first_approved INTEGER DEFAULT 0, UNIQUE(email, scan_date))",
    "CREATE TABLE evox_session_credits (email TEXT PRIMARY KEY, credits INTEGER)",
    "CREATE TABLE coach_subscriptions (member_email TEXT PRIMARY KEY, status TEXT)",
    "CREATE TABLE coach_threads (id INTEGER PRIMARY KEY, coach_email TEXT, member_email TEXT)",
    "CREATE TABLE ghl_write_queue (id INTEGER PRIMARY KEY, email TEXT)",
    "CREATE TABLE tags_nokey (email TEXT, tag TEXT)",
    "CREATE TABLE codes (id INTEGER PRIMARY KEY, email TEXT, code TEXT, UNIQUE(email, code))",
    "CREATE TABLE client_portals (id INTEGER PRIMARY KEY, token_hash TEXT UNIQUE, email TEXT, "
    "name TEXT, content_json TEXT, created_at TEXT, updated_at TEXT)",
    "CREATE TABLE portal_notify_state (email TEXT PRIMARY KEY, portal_token TEXT, phone TEXT, "
    "opt_status TEXT DEFAULT 'default')",
    "CREATE TABLE portal_card_state (person_id TEXT NOT NULL, card TEXT, "
    "PRIMARY KEY (person_id, card))",
]
DATA_TABLES = ("people", "orders", "carts", "biofield_reveals", "evox_session_credits",
               "coach_subscriptions", "coach_threads", "ghl_write_queue", "tags_nokey", "codes",
               "client_portals", "portal_notify_state", "portal_card_state")
AOL, GMAIL = "mel@aol.com", "mel@gmail.com"


def _seed(cx):
    cx.execute("INSERT INTO people VALUES (1, ?, 'Mel Palmer', '[\"a\"]')", (AOL,))
    cx.execute("INSERT INTO people VALUES (2, ?, '', '[\"b\"]')", (GMAIL,))
    cx.execute("INSERT INTO orders VALUES (10, ?, 1, 50.0)", (AOL,))
    cx.execute("INSERT INTO orders VALUES (11, ' Mel@AOL.com', NULL, 20.0)")
    cx.execute("INSERT INTO orders VALUES (12, ?, 2, 5.0)", (GMAIL,))
    cx.execute("INSERT INTO carts VALUES (?, 'aol-cart', '2026-01-01')", (AOL,))
    cx.execute("INSERT INTO carts VALUES (?, 'gmail-cart', '2026-02-01')", (GMAIL,))
    cx.execute("INSERT INTO biofield_reveals VALUES (20, ?, '2026-07-25', '2026-09-01', 0)", (AOL,))
    cx.execute("INSERT INTO biofield_reveals VALUES (21, ?, '2026-09-19', '2026-09-19', 0)", (AOL,))
    cx.execute("INSERT INTO biofield_reveals VALUES (22, ?, '2026-07-25', '2026-07-25', 0)", (GMAIL,))
    cx.execute("INSERT INTO evox_session_credits VALUES (?, 5)", (AOL,))
    cx.execute("INSERT INTO evox_session_credits VALUES (?, 1)", (GMAIL,))
    cx.execute("INSERT INTO coach_threads VALUES (30, ?, 'kai@x.com')", (AOL,))
    cx.execute("INSERT INTO ghl_write_queue VALUES (40, ?)", (AOL,))
    cx.execute("INSERT INTO tags_nokey VALUES (?, 'vip')", (AOL,))
    cx.execute("INSERT INTO tags_nokey VALUES (?, 'vip')", (GMAIL,))
    cx.execute("INSERT INTO tags_nokey VALUES (?, 'vip')", (GMAIL,))
    cx.execute("INSERT INTO codes VALUES (60, ?, NULL)", (AOL,))
    cx.execute("INSERT INTO codes VALUES (61, ?, NULL)", (GMAIL,))
    cx.execute("INSERT INTO client_portals VALUES (50, 'hash-aol', ?, 'Mel', ?, 't', "
               "'2026-09-19T00:00:00')",
               (AOL, json.dumps({"greeting": "Sept", "layers": [1, 2], "phase": "fire"})))
    cx.execute("INSERT INTO client_portals VALUES (51, 'hash-gmail', ?, 'Mel', ?, 't', "
               "'2026-07-25T00:00:00')",
               (GMAIL, json.dumps({"greeting": "July", "layers": [9], "schedule": "Mon"})))
    cx.execute("INSERT INTO portal_notify_state VALUES (?, 'raw-aol', '8085550100', 'out')", (AOL,))
    cx.execute("INSERT INTO portal_notify_state VALUES (?, 'raw-gmail', '', 'default')", (GMAIL,))
    cx.execute("INSERT INTO portal_card_state VALUES ('1', 'welcome')")   # person number stored as text
    cx.commit()


def _fields(cx, survivor_id, merged_id):
    """Stand-in for app._merge_two_people: fill empty fields, union tags, delete."""
    s = cx.execute("SELECT name, tags FROM people WHERE id=?", (survivor_id,)).fetchone()
    m = cx.execute("SELECT name, tags FROM people WHERE id=?", (merged_id,)).fetchone()
    tags = sorted(set(json.loads(s[1] or "[]")) | set(json.loads(m[1] or "[]")))
    cx.execute("UPDATE people SET name=?, tags=? WHERE id=?", (s[0] or m[0], json.dumps(tags),
                                                             survivor_id))
    cx.execute("DELETE FROM people WHERE id=?", (merged_id,))


@pytest.fixture(autouse=True)
def _test_tables_are_reviewed(monkeypatch):
    """The fixture's own table names stand in for reviewed production tables."""
    from dashboard import person_merge_discover as pd
    monkeypatch.setattr(pd, "MOVE_TABLES", pd.MOVE_TABLES | {"tags_nokey", "codes", "subs",
                                                             "body_map_photos"})


@pytest.fixture
def cx(tmp_path):
    c = sqlite3.connect(str(tmp_path / "t.db"))
    c.execute("PRAGMA foreign_keys=ON")
    for d in DDL:
        c.execute(d)
    pa.init_tables(c)
    _seed(c)
    return c


def _dump(cx, tables=DATA_TABLES):
    return {t: sorted(repr(tuple(r)) for r in cx.execute(f'SELECT * FROM "{t}"').fetchall())
            for t in tables}


def _apply(cx, **kw):
    args = dict(survivor_id=2, merged_id=1, mail_old="stop", evidence={}, suggestion={},
                applied_by="test", merge_people_fields=_fields)
    args.update(kw)
    mid = pm.apply(cx, **args)
    cx.commit()
    return mid


def test_preview_writes_nothing_and_reports_rules(cx):
    before = _dump(cx, DATA_TABLES + MERGE_TABLES)
    p = pm.preview(cx, 2, 1)
    assert p["counts"]["orders"] == [2, 1]
    assert p["clashes"]["evox_session_credits"] == {"count": 1, "rule": "sum:credits"}
    assert p["blocked"] == []
    assert p["portal"]["keep"] == "merged" and p["portal"]["from_other"] == ["schedule"]
    assert _dump(cx, DATA_TABLES + MERGE_TABLES) == before


def test_rules_settle_clashes(cx):
    _apply(cx)
    assert {r[0] for r in cx.execute("SELECT email FROM orders")} == {GMAIL}
    assert cx.execute("SELECT person_id FROM orders WHERE id=10").fetchone()[0] == 2
    # newest cart wins: gmail's is newer
    assert cx.execute("SELECT items FROM carts").fetchall() == [("gmail-cart",)]
    # newest reveal for the same scan date wins: aol's was updated later
    assert cx.execute("SELECT id FROM biofield_reveals ORDER BY id").fetchall() == [(20,), (21,)]
    # credits add up
    assert cx.execute("SELECT email, credits FROM evox_session_credits").fetchall() == [(GMAIL, 6)]


def test_a_clash_with_no_rule_blocks_the_merge(cx):
    cx.execute("INSERT INTO coach_subscriptions VALUES (?, 'active')", (AOL,))
    cx.execute("INSERT INTO coach_subscriptions VALUES (?, 'cancelled')", (GMAIL,))
    cx.commit()
    assert pm.preview(cx, 2, 1)["blocked"] == ["coach_subscriptions"]
    before = _dump(cx, DATA_TABLES + MERGE_TABLES)
    with pytest.raises(pm.MergeBlocked) as e:
        pm.apply(cx, survivor_id=2, merged_id=1, mail_old="stop", evidence={}, suggestion={},
                 applied_by="t", merge_people_fields=_fields)
    cx.rollback()
    assert "coach_subscriptions" in str(e.value)
    assert _dump(cx, DATA_TABLES + MERGE_TABLES) == before


def test_a_null_never_clashes(cx):
    _apply(cx)
    assert cx.execute("SELECT id, email FROM codes ORDER BY id").fetchall() == [(60, GMAIL), (61, GMAIL)]


def test_a_partial_unique_index_refusal_blocks(cx):
    cx.execute("CREATE TABLE subs (id INTEGER PRIMARY KEY, email TEXT, status TEXT)")
    cx.execute("CREATE UNIQUE INDEX subs_active ON subs(email) WHERE status='active'")
    cx.execute("INSERT INTO subs VALUES (1, ?, 'active')", (AOL,))
    cx.execute("INSERT INTO subs VALUES (2, ?, 'active')", (GMAIL,))
    cx.commit()
    with pytest.raises(pm.MergeBlocked):
        pm.apply(cx, survivor_id=2, merged_id=1, mail_old="stop", evidence={}, suggestion={},
                 applied_by="t", merge_people_fields=_fields)
    cx.rollback()
    assert cx.execute("SELECT COUNT(*) FROM person_merges").fetchone()[0] == 0


def test_history_tables_untouched_and_variants_move(cx):
    _apply(cx)
    assert cx.execute("SELECT email FROM ghl_write_queue").fetchone()[0] == AOL
    assert cx.execute("SELECT email FROM orders WHERE id=11").fetchone()[0] == GMAIL
    assert cx.execute("SELECT coach_email, member_email FROM coach_threads").fetchall() == [
        (GMAIL, "kai@x.com")]


def test_newer_portal_kept_combined_and_both_links_work(cx):
    _apply(cx)
    rows = cx.execute("SELECT id, email, token_hash, content_json FROM client_portals").fetchall()
    assert [(r[0], r[1], r[2]) for r in rows] == [(50, GMAIL, "hash-aol")]
    assert json.loads(rows[0][3]) == {"greeting": "Sept", "layers": [1, 2], "phase": "fire",
                                      "schedule": "Mon"}
    assert pa.token_alias(cx, "hash-gmail") == GMAIL
    assert cx.execute("SELECT portal_token FROM portal_notify_state").fetchall() == [("raw-aol",)]


def test_a_text_opt_out_and_phone_carry_to_the_survivor(cx):
    """Glen, 2026-09-27: a text opt-out is the person's choice and carries (round 3)."""
    _apply(cx)
    assert cx.execute("SELECT email, phone, opt_status FROM portal_notify_state").fetchall() == [
        (GMAIL, "8085550100", "out")]


def test_the_survivors_own_phone_is_kept(cx):
    cx.execute("UPDATE portal_notify_state SET phone='8085559999' WHERE email=?", (GMAIL,))
    _apply(cx)
    assert cx.execute("SELECT phone, opt_status FROM portal_notify_state").fetchall() == [
        ("8085559999", "out")]


def test_apply_twice_and_bad_input_are_refused(cx):
    _apply(cx)
    cx.execute("INSERT INTO people VALUES (1, ?, 'again', '[]')", (AOL,))
    for kw in ({}, {"mail_old": "maybe"}, {"merged_id": 2}):
        with pytest.raises(pm.MergeRefused):
            pm.apply(cx, **{**dict(survivor_id=2, merged_id=1, mail_old="stop", evidence={},
                                   suggestion={}, applied_by="t", merge_people_fields=_fields),
                            **kw})


def test_undo_restores_the_database_exactly(cx):
    # No opt-out to carry: a carried opt-out is kept by undo on purpose (tested below).
    cx.execute("UPDATE portal_notify_state SET opt_status='default' WHERE email=?", (AOL,))
    cx.commit()
    before = _dump(cx)
    mid = _apply(cx)
    report = pm.undo(cx, mid, "test")
    cx.commit()
    assert report["skipped"] == []
    assert _dump(cx) == before
    assert pa.canonical_email(cx, AOL) == AOL and pa.token_alias(cx, "hash-gmail") is None
    with pytest.raises(pm.MergeRefused):
        pm.undo(cx, mid, "t")


def test_undo_skips_a_changed_row_whole(cx):
    mid = _apply(cx)
    cx.execute("UPDATE orders SET email='someone@else.com' WHERE id=10")
    report = pm.undo(cx, mid, "t")
    assert any(s["table"] == "orders" for s in report["skipped"])
    # neither of order 10's columns went back: it is not split between two people
    assert cx.execute("SELECT email, person_id FROM orders WHERE id=10").fetchone() == (
        "someone@else.com", 2)
    assert cx.execute("SELECT email FROM orders WHERE id=11").fetchone()[0] == " Mel@AOL.com"


def test_undo_touches_only_its_own_row_in_a_table_with_no_key(cx):
    before = sorted(cx.execute("SELECT email, tag FROM tags_nokey").fetchall())
    mid = _apply(cx)
    pm.undo(cx, mid, "t")
    assert sorted(cx.execute("SELECT email, tag FROM tags_nokey").fetchall()) == before


def test_undo_refused_while_a_later_merge_depends(cx):
    first = _apply(cx)
    cx.execute("INSERT INTO people VALUES (3, 'mel@proton.me', 'Mel', '[]')")
    second = _apply(cx, survivor_id=3, merged_id=2)
    with pytest.raises(pm.MergeRefused) as e:
        pm.undo(cx, first, "t")
    assert str(second) in str(e.value)


def test_failure_mid_apply_leaves_nothing(cx):
    before = _dump(cx, DATA_TABLES + MERGE_TABLES)

    def boom(*a):
        raise RuntimeError("injected")

    with pytest.raises(RuntimeError):
        pm.apply(cx, survivor_id=2, merged_id=1, mail_old="stop", evidence={}, suggestion={},
                 applied_by="t", merge_people_fields=boom)
    cx.rollback()
    assert _dump(cx, DATA_TABLES + MERGE_TABLES) == before


def test_sweep_moves_late_rows_once_and_newer_wins(cx):
    mid = _apply(cx)
    cx.execute("INSERT INTO orders VALUES (13, ?, NULL, 9.0)", (AOL,))
    cx.execute("INSERT INTO carts VALUES (?, 'late-cart', '2026-09-27')", (AOL,))
    got = pm.sweep(cx)
    assert got["moved"] == 2 and got["blocked"] == []
    assert cx.execute("SELECT email FROM orders WHERE id=13").fetchone()[0] == GMAIL
    assert cx.execute("SELECT items FROM carts").fetchall() == [("late-cart",)]   # the newer one
    src = cx.execute("SELECT DISTINCT source, merge_id FROM person_merge_changes WHERE "
                     "table_name='orders' AND key_json LIKE '%13%'").fetchall()
    assert src == [("sweep", mid)]
    assert pm.sweep(cx)["moved"] == 0


def test_sweep_leaves_an_unruled_clash_in_place(cx):
    _apply(cx)
    cx.execute("INSERT INTO coach_subscriptions VALUES (?, 'active')", (GMAIL,))
    cx.execute("INSERT INTO coach_subscriptions VALUES (?, 'active')", (AOL,))
    got = pm.sweep(cx)
    assert [b["table"] for b in got["blocked"]] == ["coach_subscriptions"]
    assert cx.execute("SELECT COUNT(*) FROM coach_subscriptions").fetchone()[0] == 2


def test_sweep_moves_a_late_portal_and_person_number(cx):
    _apply(cx)
    cx.execute("INSERT INTO client_portals VALUES (52, 'hash-late', ?, 'Mel', '{}', 't', "
               "'2026-01-01')", (AOL,))
    cx.commit()
    cx.execute("PRAGMA foreign_keys=OFF")        # a late row pointing at the removed person
    cx.execute("INSERT INTO orders VALUES (14, 'x@x.com', 1, 1.0)")
    cx.commit()
    cx.execute("PRAGMA foreign_keys=ON")
    pm.sweep(cx)
    assert cx.execute("SELECT COUNT(*) FROM client_portals").fetchone()[0] == 1
    assert pa.token_alias(cx, "hash-late") == GMAIL
    assert cx.execute("SELECT person_id FROM orders WHERE id=14").fetchone()[0] == 2


def test_sweep_after_a_chain_logs_against_the_last_merge(cx):
    _apply(cx)
    cx.execute("INSERT INTO people VALUES (3, 'mel@proton.me', 'Mel', '[]')")
    second = _apply(cx, survivor_id=3, merged_id=2)
    cx.execute("INSERT INTO orders VALUES (15, ?, NULL, 1.0)", (AOL,))
    pm.sweep(cx)
    assert cx.execute("SELECT email FROM orders WHERE id=15").fetchone()[0] == "mel@proton.me"
    got = cx.execute("SELECT merge_id FROM person_merge_changes WHERE table_name='orders' "
                     "AND key_json LIKE '%15%'").fetchall()
    assert {g[0] for g in got} == {second}


def test_undo_after_a_sweep_set_aside_does_not_abort(cx):
    mid = _apply(cx)
    cx.execute("INSERT INTO carts VALUES (?, 'late-cart', '2026-09-27')", (AOL,))
    pm.sweep(cx)
    report = pm.undo(cx, mid, "t")
    cx.commit()
    assert cx.execute("SELECT COUNT(*) FROM people").fetchone()[0] == 2
    assert isinstance(report["skipped"], list)


def _pg_ok():
    dsn = os.environ.get("PG_DSN", "")
    return bool(dsn) and "test" in dsn          # never a real database


@pytest.mark.skipif(not _pg_ok(), reason="PG_DSN for a test database not set")
def test_round_trip_on_postgres_with_binary_and_json(monkeypatch):
    monkeypatch.setenv("DB_BACKEND", "postgres")
    c = db.connect("/data/pm_engine_test.db")
    extra = ("body_map_photos",)
    try:
        for t in DATA_TABLES + MERGE_TABLES + extra + ("subs",):
            c.execute(f'DROP TABLE IF EXISTS "{t}" CASCADE')
        for d in DDL:
            c.execute(d)
        c.execute("CREATE TABLE body_map_photos (email TEXT, side TEXT, img BYTEA, meta JSONB, "
                  "PRIMARY KEY (email, side))")
        pa.init_tables(c)
        _seed(c)
        c.execute("UPDATE portal_notify_state SET opt_status='default' WHERE email=?", (AOL,))
        c.execute("INSERT INTO body_map_photos VALUES (?, 'front', ?, ?)",
                  (AOL, b"\x00\x01aol", json.dumps({"a": 1})))
        c.execute("INSERT INTO body_map_photos VALUES (?, 'front', ?, ?)",
                  (GMAIL, b"\x00\x02gm", json.dumps({"g": 2})))
        c.commit()
        monkeypatch.setitem(pm.CLASH_RULES, "body_map_photos", "survivor")
        before = _dump(c, DATA_TABLES + extra)
        mid = _apply(c)
        assert pa.canonical_email(c, AOL) == GMAIL
        report = pm.undo(c, mid, "t")
        c.commit()
        assert report["skipped"] == []
        assert _dump(c, DATA_TABLES + extra) == before
    finally:
        c.rollback()
        for t in DATA_TABLES + MERGE_TABLES + extra:
            c.execute(f'DROP TABLE IF EXISTS "{t}" CASCADE')
        c.commit()
        c.close()


def test_a_person_number_stored_as_text_moves(cx):
    _apply(cx)
    assert cx.execute("SELECT person_id FROM portal_card_state").fetchall() == [("2",)]


def test_an_approved_reveal_beats_a_newer_draft(cx):
    cx.execute("UPDATE biofield_reveals SET first_approved=1 WHERE id=22")
    _apply(cx)
    assert cx.execute("SELECT id FROM biofield_reveals ORDER BY id").fetchall() == [(21,), (22,)]


def test_an_unreviewed_table_blocks_instead_of_moving(cx):
    cx.execute("CREATE TABLE practitioners (id INTEGER PRIMARY KEY, email TEXT)")
    cx.execute("INSERT INTO practitioners VALUES (1, ?)", (AOL,))
    cx.commit()
    assert "practitioners" in pm.preview(cx, 2, 1)["blocked"]
    with pytest.raises(pm.MergeBlocked):
        pm.apply(cx, survivor_id=2, merged_id=1, mail_old="stop", evidence={}, suggestion={},
                 applied_by="t", merge_people_fields=_fields)
    cx.rollback()
    assert cx.execute("SELECT email FROM practitioners").fetchone()[0] == AOL


def test_an_unreviewed_table_is_left_by_the_sweep(cx):
    _apply(cx)
    cx.execute("CREATE TABLE practitioners (id INTEGER PRIMARY KEY, email TEXT)")
    cx.execute("INSERT INTO practitioners VALUES (1, ?)", (AOL,))
    got = pm.sweep(cx)
    assert "practitioners" in [b["table"] for b in got["blocked"]]
    assert cx.execute("SELECT email FROM practitioners").fetchone()[0] == AOL


def test_an_affiliate_clash_blocks(cx):
    cx.execute("CREATE TABLE affiliate_signups (id INTEGER PRIMARY KEY, email TEXT UNIQUE, slug TEXT)")
    cx.execute("INSERT INTO affiliate_signups VALUES (1, ?, 'mel-aol')", (AOL,))
    cx.execute("INSERT INTO affiliate_signups VALUES (2, ?, 'mel-gm')", (GMAIL,))
    cx.commit()
    assert "affiliate_signups" in pm.preview(cx, 2, 1)["blocked"]


def test_sweep_after_a_chain_logs_person_numbers_against_the_last_merge(cx):
    _apply(cx)
    cx.execute("INSERT INTO people VALUES (3, 'mel@proton.me', 'Mel', '[]')")
    second = _apply(cx, survivor_id=3, merged_id=2)
    cx.commit()
    cx.execute("PRAGMA foreign_keys=OFF")
    cx.execute("INSERT INTO orders VALUES (16, 'z@z.com', 1, 1.0)")
    cx.commit()
    cx.execute("PRAGMA foreign_keys=ON")
    pm.sweep(cx)
    assert cx.execute("SELECT person_id FROM orders WHERE id=16").fetchone()[0] == 3
    got = {r[0] for r in cx.execute("SELECT merge_id FROM person_merge_changes WHERE "
                                    "table_name='orders' AND key_json LIKE '%16%'")}
    assert got == {second}


@pytest.mark.skipif(not _pg_ok(), reason="PG_DSN for a test database not set")
def test_postgres_refusal_does_not_poison_the_sweep_and_other_schemas_are_not_the_app(monkeypatch):
    monkeypatch.setenv("DB_BACKEND", "postgres")
    c = db.connect("/data/pm_engine_test.db")
    other = db.connect("/data/pm_engine_other.db")
    try:
        for t in DATA_TABLES + MERGE_TABLES + ("subs",):
            c.execute(f'DROP TABLE IF EXISTS "{t}" CASCADE')
        other.execute('DROP TABLE IF EXISTS "client_portals" CASCADE')
        for d in DDL:
            c.execute(d)
        pa.init_tables(c)
        _seed(c)
        # A newer page for the same address in ANOTHER schema must not be the one kept.
        other.execute("CREATE TABLE client_portals (id INTEGER PRIMARY KEY, token_hash TEXT, "
                      "email TEXT, name TEXT, content_json TEXT, created_at TEXT, updated_at TEXT)")
        other.execute("INSERT INTO client_portals VALUES (90, 'h-other', ?, 'x', '{}', 't', "
                      "'2099-01-01')", (GMAIL,))
        other.execute("INSERT INTO client_portals VALUES (91, 'h-other2', ?, 'x', '{}', 't', "
                      "'2099-01-01')", (AOL,))
        other.commit()
        c.commit()
        _apply(c)
        assert [tuple(r) for r in c.execute("SELECT id, email FROM client_portals").fetchall()] == [
            (50, GMAIL)]
        # The other schema's table is an ordinary table there: its rows move, none is kept
        # or dropped as the app's portal page.
        assert sorted(tuple(r) for r in other.execute("SELECT id, email FROM client_portals")) == [
            (90, GMAIL), (91, GMAIL)]
        # A partial unique index refuses one late row; the sweep must still move the next.
        c.execute("CREATE TABLE subs (id INTEGER PRIMARY KEY, email TEXT, status TEXT)")
        c.execute("CREATE UNIQUE INDEX subs_active ON subs(email) WHERE status='active'")
        c.execute("INSERT INTO subs VALUES (1, ?, 'active')", (GMAIL,))
        c.execute("INSERT INTO subs VALUES (2, ?, 'active')", (AOL,))
        c.execute("INSERT INTO orders VALUES (17, ?, NULL, 1.0)", (AOL,))
        got = pm.sweep(c)
        c.commit()
        assert "subs" in [b["table"] for b in got["blocked"]]
        assert c.execute("SELECT email FROM orders WHERE id=17").fetchone()[0] == GMAIL
    finally:
        c.rollback()
        other.rollback()
        for t in DATA_TABLES + MERGE_TABLES + ("subs",):
            c.execute(f'DROP TABLE IF EXISTS "{t}" CASCADE')
        other.execute('DROP TABLE IF EXISTS "client_portals" CASCADE')
        c.commit()
        other.commit()
        c.close()
        other.close()


# ── Final review round, 2026-09-27: undo must never lose an opt-out or create money ──

def test_undo_keeps_a_stop_sent_after_the_merge(cx):
    cx.execute("UPDATE portal_notify_state SET opt_status='default' WHERE email=?", (AOL,))
    mid = _apply(cx)                                   # the phone carries to gmail
    cx.execute("UPDATE portal_notify_state SET opt_status='out' WHERE email=?", (GMAIL,))  # STOP
    pm.undo(cx, mid, "t")
    rows = dict((r[0], (r[1], r[2])) for r in cx.execute(
        "SELECT email, phone, opt_status FROM portal_notify_state"))
    # every row holding that phone is opted out
    assert all(st == "out" for ph, st in rows.values() if ph == "8085550100")
    assert rows[GMAIL][1] == "out"


def test_undo_never_clears_a_carried_opt_out(cx):
    mid = _apply(cx)                                   # aol's 'out' carried to gmail
    pm.undo(cx, mid, "t")
    assert cx.execute("SELECT opt_status FROM portal_notify_state WHERE email=?",
                      (GMAIL,)).fetchone()[0] == "out"
    assert cx.execute("SELECT opt_status FROM portal_notify_state WHERE email=?",
                      (AOL,)).fetchone()[0] == "out"


def test_undo_never_creates_credits(cx):
    mid = _apply(cx)                                   # 1 + 5 = 6 on gmail
    cx.execute("UPDATE evox_session_credits SET credits=5 WHERE email=?", (GMAIL,))   # one spent
    pm.undo(cx, mid, "t")
    total = cx.execute("SELECT SUM(credits) FROM evox_session_credits").fetchone()[0]
    assert total == 5
    assert cx.execute("SELECT credits FROM evox_session_credits WHERE email=?",
                      (GMAIL,)).fetchone()[0] == 1


def test_undo_splits_unspent_credits_back_exactly(cx):
    mid = _apply(cx)
    pm.undo(cx, mid, "t")
    assert dict(cx.execute("SELECT email, credits FROM evox_session_credits").fetchall()) == {
        AOL: 5, GMAIL: 1}


def test_undo_after_spending_past_the_survivors_own_balance(cx):
    cx.execute("UPDATE evox_session_credits SET credits=3 WHERE email=?", (GMAIL,))
    cx.commit()
    mid = _apply(cx)                                   # 3 + 5 = 8
    cx.execute("UPDATE evox_session_credits SET credits=2 WHERE email=?", (GMAIL,))   # six spent
    pm.undo(cx, mid, "t")
    got = dict(cx.execute("SELECT email, credits FROM evox_session_credits").fetchall())
    assert got == {GMAIL: 2, AOL: 0}                   # never negative, never more than there is
