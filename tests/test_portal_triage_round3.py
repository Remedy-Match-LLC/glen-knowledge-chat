"""Round 3 proposed tests for the portal triage branch (sess/776c479e-triage).

Part 1 kills the mutation survivors (M02, M03, M04, M07, M17, M26, M29, M38, M42).
Each passes on the branch and fails under its mutation. M24 is equivalent (the
quote is always inside the sentence it is checked against), so it has no test.

Part 2 pins the backstop misses found by the round 3 probe. They failed on the
branch before the round 3 fixes.

Adopted into tests/ in round 3; the Part 2 xfail markers were removed once fixed. Fakes only: no model, no network, no mail. The chat-route
tests reuse the fixtures of test_portal_warning_sign_line.py and
test_portal_healing_crisis_watch.py, which stub _send_full_report_email.
"""
import json
import os
import re
import sqlite3
from datetime import datetime, timedelta, timezone

os.environ.setdefault("OPENAI_API_KEY", "sk-dummy")
os.environ.setdefault("PINECONE_API_KEY", "pc-dummy")

import pytest

from dashboard import portal_triage as pt
from dashboard import health_suggestions as hs

NOW = datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc)
LATER = NOW + timedelta(hours=1)


def _cx():
    cx = sqlite3.connect(":memory:")
    pt.init_table(cx)
    return cx


# --- Part 1: survivors ------------------------------------------------------------

def test_claim_of_a_never_tried_item_is_exclusive():
    """M02: two workers that both saw email_last_try NULL must not both send."""
    cx = _cx()
    item, _ = pt.add_warning_item(cx, "c@x.com", "C", ["swelling"], "lips swollen", now=NOW)
    assert pt.claim_resend(cx, item, None) is True
    assert pt.claim_resend(cx, item, None) is False


@pytest.mark.parametrize("attempts,due", [(11, True), (12, False), (40, False)])
def test_resend_stops_after_twelve_attempts(attempts, due):
    """M03, M04: the attempt cap is 12, inclusive of the first send."""
    cx = _cx()
    item, _ = pt.add_warning_item(cx, "c@x.com", "C", ["swelling"], "lips swollen", now=NOW)
    cx.execute("UPDATE portal_triage SET email_status='unsent:console-log', email_attempts=?, "
               "email_last_try='2026-10-20T12:00:00Z' WHERE id=?", (attempts, item))
    cx.commit()
    ids = [r["id"] for r in pt.unsent_high(cx, now=LATER)]
    assert (item in ids) is due


def test_resend_ignores_items_that_are_not_high():
    """M07: only high urgency is resent; medium and low wait for the digest."""
    cx = _cx()
    mid = pt.add_item(cx, "c@x.com", "C", "complaint", "medium", "s", "r", "msg")
    cx.execute("UPDATE portal_triage SET email_status='unsent:console-log', created_at=? "
               "WHERE id=?", ("2026-10-20T12:00:00Z", mid))
    cx.commit()
    assert pt.unsent_high(cx, now=LATER) == []


def test_resend_job_runs_every_five_minutes(monkeypatch):
    """M38: registered, as an interval job, every 5 minutes."""
    import app as appmod
    import apscheduler.schedulers.background as bg
    jobs = {}

    class _FakeScheduler:
        def add_job(self, fn, trigger, **kw):
            jobs[kw.get("id")] = (fn, trigger, kw)

        def start(self):
            pass
    monkeypatch.setattr(bg, "BackgroundScheduler", _FakeScheduler)
    appmod._start_scheduler()
    fn, trigger, kw = jobs["triage_email_resend"]
    assert fn is appmod._resend_unsent_triage_emails
    assert trigger == "interval" and kw.get("minutes") == 5


# Chat-route tests, reusing the branch's own fixtures.
from tests.test_portal_warning_sign_line import env, _triage_rows, _FakeClient  # noqa: E402,F401


def test_model_only_high_item_is_pending_before_its_send(env, monkeypatch):
    """M42: a model-only high item is marked pending before the send, so a worker that
    dies mid-send leaves it for the resend job."""
    appmod, chat, sent = env
    monkeypatch.setattr(appmod, "_send_triage_item_email", lambda *a, **k: False)  # died mid-send
    chat("I want a refund, this is unacceptable.",
         triage_json='{"needs_attention": true, "category": "complaint", "urgency": "high", '
                     '"summary": "refund", "recommendation": "call"}')
    rows = _triage_rows(appmod)
    assert len(rows) == 1 and rows[0]["email_status"] == "pending"
    with sqlite3.connect(appmod.LOG_DB) as cx:
        assert [r["id"] for r in pt.unsent_high(cx, now=datetime.now(timezone.utc)
                                                + timedelta(minutes=20))] == [rows[0]["id"]]


from tests import test_portal_healing_crisis_watch as _hw  # noqa: E402


def test_healing_flag_is_handled_once_even_when_the_model_says_high(env):
    """M17: a healing-crisis flag is delivered before the stream; the general model
    triage must not also run and store and email a second item."""
    appmod, chat, sent = env
    cx = _hw._db(appmod.LOG_DB)
    two_days_ago = _hw._iso(datetime.now(timezone.utc) - timedelta(days=2))
    _hw._order(cx, "c@x.com", "biofield", _hw._ship(cx, two_days_ago, "T2"))
    chat("I have a headache today.",
         triage_json='{"needs_attention": true, "category": "health-concern", '
                     '"urgency": "high", "summary": "s", "recommendation": "r"}')
    assert len(_triage_rows(appmod)) == 1
    assert len(sent) == 1


def _extract(item, client_msg):
    return hs.llm_extractor(lambda s, u: json.dumps({"items": [item]}))(client_msg)


def test_quote_not_in_the_clients_message_is_dropped():
    """M26: a quote the model made up (or took from the AI's answer) records nothing."""
    item = {"field_id": "medications", "value": {"medication": "warfarin"},
            "quote": "I take warfarin daily", "about": "self"}
    assert _extract(item, "Thanks for the info, talk soon.") == []
    assert _extract(item, "I take warfarin daily.") != []      # the same item, really said


def test_item_not_about_self_is_dropped():
    """M29: the model's own "about" must be self."""
    item = {"field_id": "medications", "value": {"medication": "warfarin"},
            "quote": "I take warfarin daily", "about": "partner"}
    assert _extract(item, "I take warfarin daily.") == []


# --- Part 2: backstop misses from the round 3 probe (fail today) ---------------------

@pytest.mark.parametrize("msg", [
    "After taking the capsules my lips swelled up.",
    "My tongue swelled after the first dose.",
    "Hives all over since I started the new formula.",
    "Since I started the Liver formula I've had a high fever.",
])
def test_reaction_after_a_product_still_flags(msg):
    assert pt.warning_signs(msg)


def test_is_it_normal_question_with_a_present_sign_flags():
    assert pt.warning_signs("Is it normal for my lips to swell like this?")


def test_not_able_to_breathe_flags():
    assert pt.warning_signs("I haven't been able to breathe well since last night.")


# --- Round 3 fixes: H1, H2, H3, M1, M2 ---------------------------------------------

@pytest.mark.parametrize("msg", [
    "Since my last dose I have hives everywhere.",
    "My eyes turned yellow after doubling the dose.",
    "The new tablets gave me a fever of 103.",
    "Severe dizziness since I ordered the new drops last week, I fell twice.",
    "Hives from the formula, help!",
    "I get hives from the new formula",
    "My lips are swelling, do you have anything for that?",
    "I take the capsules and my tongue is swelling",
    "My lips are swelling with the new capsules.",
    "Fever of 103 with the formula.",
])
def test_more_reactions_to_a_product_flag(msg):
    assert pt.warning_signs(msg), msg


@pytest.mark.parametrize("msg", [
    "I can't get a full breath.",
    "I couldn't catch my breath all night.",
    "I'm not getting enough air.",
    "She has not been able to breathe properly since lunch.",
])
def test_cannot_breathe_variants_flag(msg):
    assert "hives-breathing" in pt.warning_signs(msg), msg


def test_acute_report_about_someone_else_still_flags():
    assert pt.warning_signs("my husband's lips are swelling") == ["swelling"]


@pytest.mark.parametrize("msg", [
    "Do you have anything for hives?",
    "Anything for a swollen throat? Asking for my class.",
    "I'm almost out of the drops I use when I get hives.",
    "My husband gets hives from shellfish.",
    "I break out in hives whenever I eat strawberries.",
    "What should I watch for with jaundice?",
    "My blood test shows high bilirubin, should I worry about yellow skin?",
    "I get short of breath when I run, is that normal for my age?",
    "My temperature is back to normal.",
    "My high fever is back to normal now.",
    "I've never been able to breathe through my nose well.",
    "My breathing is not hard.",
])
def test_round3_routine_messages_do_not_flag(msg):
    assert pt.warning_signs(msg) == [], msg


@pytest.mark.parametrize("msg", [
    "I'm tired of waiting, when will my order ship?",
    "I returned the extra bottle yesterday.",
    "Thanks, I'll come back to this later.",
    "My energy is back!",
    "Do the drops cause headaches?",
    # 22 everyday messages probed inside the window in round 3
    "Got the package today, thank you!",
    "How many drops do I take each morning?",
    "Can I take these with food?",
    "What time is my call on Thursday?",
    "I'm so excited to start.",
    "My husband is tired after work, can he take some too?",
    "The bottle arrived with a cracked cap.",
    "I forgot to take them yesterday, should I double up?",
    "Is it okay to take them with coffee?",
    "Where do I find my report?",
    "My sleep has been better this week.",
    "I'm sick of the taste, any tips?",
    "Can my kids take the drops?",
    "I have a question about the mineral powder.",
    "My headache went away after two days.",
    "I'm back from my trip and restarting the drops.",
    "Thanks, I feel so much lighter!",
    "Do I need to keep these in the fridge?",
    "My skin looks clearer already.",
    "I'm running late for our appointment, sorry.",
    "I'll be back next week to order more.",
    "The spots on the label are smudged.",
])
def test_round3_everyday_messages_are_not_healing_signs(msg):
    assert pt.healing_signs(msg) == [], msg


@pytest.mark.parametrize("msg,key", [
    ("Is a rash normal after starting the program?", "rash"),
    ("My joint pain is worse since I started the capsules.", "tired-aches"),
    ("My old back pain came back.", "old-symptom"),
    ("The itching is back again.", "old-symptom"),
    ("I have red spots on my arms", "rash"),
    ("I'm tired today.", "tired-aches"),
])
def test_round3_healing_signs_still_flag(msg, key):
    assert key in pt.healing_signs(msg), msg


# --- Round 3 low items --------------------------------------------------------------

def test_column_add_survives_a_worker_that_added_it_first(monkeypatch):
    """L2: both workers see a column missing; the loser's ALTER fails with a duplicate
    column. It rolls back, re-checks and carries on, so the message keeps its line."""
    from dashboard import db as _db
    cx = _cx()                                   # every column already exists
    real = _db.column_exists
    seen = set()

    def stale(cx_, table, col):
        if col not in seen:                      # first look: "missing" (stale)
            seen.add(col)
            return False
        return real(cx_, table, col)
    monkeypatch.setattr(_db, "column_exists", stale)
    pt.init_table(cx)                            # every ALTER fails, none raises
    item, created = pt.add_warning_item(cx, "c@x.com", "C", ["swelling"], "lips swollen", now=NOW)
    assert created and item


def test_column_add_failure_that_left_no_column_raises(monkeypatch):
    from dashboard import db as _db
    cx = sqlite3.connect(":memory:")
    cx.execute("CREATE TABLE portal_triage(id INTEGER PRIMARY KEY, resolved INTEGER)")

    class _Cx:
        def __init__(self, inner):
            self.inner = inner

        def execute(self, sql, *a):
            if sql.startswith("ALTER TABLE"):
                raise sqlite3.OperationalError("disk I/O error")
            return self.inner.execute(sql, *a)

        def commit(self):
            self.inner.commit()

        def rollback(self):
            self.inner.rollback()
    with pytest.raises(sqlite3.OperationalError):
        pt._add_column(_Cx(cx), "email_status", "TEXT")


def test_double_submit_on_two_workers_stores_one_item():
    """L3: the second worker's retry check ran before the first one's insert, so it
    misses it; the unique dedupe key stops the second insert."""
    cx = _cx()
    first, created = pt.add_warning_item(cx, "c@x.com", "C", ["swelling"], "My lips swell!",
                                         now=NOW)
    assert created
    # Hide the first row from the retry check, as a racing worker would not see it.
    cx.execute("UPDATE portal_triage SET message_key='hidden' WHERE id=?", (first,))
    cx.commit()
    second, created = pt.add_warning_item(cx, "c@x.com", "C", ["swelling"], "my lips swell",
                                          now=NOW + timedelta(seconds=2))
    assert (second, created) == (first, False)
    assert cx.execute("SELECT COUNT(*) FROM portal_triage").fetchone()[0] == 1


def test_dedupe_key_is_per_client_message_and_bucket():
    t = NOW
    assert pt.dedupe_key("a", "k", t) == pt.dedupe_key("a", "k", t + timedelta(seconds=30))
    assert pt.dedupe_key("a", "k", t) != pt.dedupe_key("b", "k", t)
    assert pt.dedupe_key("a", "k", t) != pt.dedupe_key("a", "j", t)
    assert pt.dedupe_key("a", "k", t) != pt.dedupe_key("a", "k", t + timedelta(minutes=10))


def test_init_table_sql_is_postgres_safe():
    import inspect
    src = inspect.getsource(pt.init_table) + inspect.getsource(pt._add_column) + \
        inspect.getsource(pt.add_warning_item)
    for bad in ("INSERT OR IGNORE", "lastrowid", "PRAGMA", "datetime('"):
        assert bad not in src


def test_warning_item_stores_its_sign_keys_but_a_healing_item_does_not():
    cx = _cx()
    cx.row_factory = sqlite3.Row
    w, _ = pt.add_warning_item(cx, "c@x.com", "C", ["swelling", "fever"], "lips, fever", now=NOW)
    h, _ = pt.add_warning_item(cx, "c@x.com", "C", ["headache"], "headache", now=NOW,
                               flag=pt.healing_flag(["headache"], {"day": 2}))
    rows = {r["id"]: r["warning_signs"] for r in cx.execute("SELECT id, warning_signs FROM portal_triage")}
    assert rows == {w: "swelling,fever", h: None}


def test_resent_email_carries_the_warning_signs_line(env):
    """L5: the resend has the same "Warning signs reported:" line as the first send."""
    appmod, chat, sent = env
    appmod._test_send_result["value"] = ("console-log", "x")
    chat("My lips are swelling after the drops.")
    with sqlite3.connect(appmod.LOG_DB) as cx:
        cx.execute("UPDATE portal_triage SET email_last_try='2000-01-01T00:00:00Z'")
    appmod._test_send_result["value"] = ("gmail-api", None)
    assert appmod._resend_unsent_triage_emails() == 1
    line = [l for l in sent[0]["body"].splitlines() if l.startswith("Warning signs reported:")]
    assert line and line == [l for l in sent[1]["body"].splitlines()
                             if l.startswith("Warning signs reported:")]


def test_healing_lookup_reads_only_this_clients_shipments():
    """L6: shipments are read only through the client's own biofield orders."""
    cx = _hw._db()
    when = _hw._iso(NOW - timedelta(days=2))
    for i in range(30):                          # other clients' recent deliveries
        _hw._order(cx, f"o{i}@x.com", "biofield", _hw._ship(cx, when, f"O{i}"))
    mine = _hw._ship(cx, when, "MINE")
    _hw._order(cx, "a@x.com", "biofield", mine)
    seen = []
    cx.set_trace_callback(seen.append)
    win = pt.healing_window(cx, "a@x.com", now=NOW)
    cx.set_trace_callback(None)
    assert win["shipment_id"] == mine
    member_queries = [q for q in seen if "FROM orders WHERE shipment_id" in q]
    assert len(member_queries) == 1, member_queries
    assert pt.healing_window(cx, "nobody@x.com", now=NOW) is None


def test_healing_lookup_matches_email_case_insensitively():
    cx = _hw._db()
    _hw._order(cx, "A@X.com", "biofield", _hw._ship(cx, _hw._iso(NOW - timedelta(days=1)), "T"))
    assert pt.healing_window(cx, "a@x.com", now=NOW)["day"] == 1


def test_dedupe_index_race_does_not_break_init(capsys):
    cx = _cx()

    class _Cx:
        def __init__(self, inner):
            self.inner = inner

        def execute(self, sql, *a):
            if "idx_triage_dedupe" in sql:
                raise sqlite3.OperationalError("index already exists")
            return self.inner.execute(sql, *a)

        def commit(self):
            self.inner.commit()

        def rollback(self):
            self.inner.rollback()
    pt.init_table(_Cx(cx))
    assert "dedupe index not created here" in capsys.readouterr().out
