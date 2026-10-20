"""Healing-crisis watch (Glen, 20 Oct 2026): inside 10 days of the delivery of a
Biofield order, healing-crisis signs in the portal chat flag to Glen at high
urgency. Outside the window nothing changes. Fakes only; no model, no mail."""
import os
os.environ.setdefault("OPENAI_API_KEY", "sk-dummy")
os.environ.setdefault("PINECONE_API_KEY", "pc-dummy")

import inspect
import json
import sqlite3
import threading
from datetime import datetime, timedelta, timezone

import pytest

from dashboard import portal_triage as pt

NOW = datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc)


def _db(path=":memory:"):
    from dashboard import orders, tracking
    cx = sqlite3.connect(path)
    cx.row_factory = sqlite3.Row
    orders.init_orders_table(cx)
    tracking.init_tracking_schema(cx)
    return cx


def _ship(cx, delivered, tracking_no, order_uuid=None):
    cur = cx.execute("INSERT INTO shipments(tracking_number, order_uuid, delivered_at) "
                     "VALUES(?,?,?) RETURNING id", (tracking_no, order_uuid, delivered))
    sid = cur.fetchone()[0]
    cx.commit()
    return sid


def _order(cx, email, source, shipment_id=None, ref=None, status="delivered"):
    ref = ref or f"{email}-{source}-{shipment_id}"
    cx.execute("INSERT INTO orders(created_at, source, external_ref, email, status, shipment_id) "
               "VALUES(?,?,?,?,?,?)", ("2026-10-01", source, ref, email, status, shipment_id))
    cx.commit()


def _iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


# --- the window ----------------------------------------------------------------

def test_inside_the_window_reports_the_day():
    cx = _db()
    sid = _ship(cx, _iso(NOW - timedelta(days=4, hours=2)), "T1")
    _order(cx, "a@x.com", "biofield", sid)
    w = pt.healing_window(cx, "A@x.com", now=NOW)
    assert w["day"] == 4 and w["shipment_id"] == sid


@pytest.mark.parametrize("age,inside", [
    (timedelta(hours=1), True), (timedelta(days=9, hours=23), True),
    (timedelta(days=10), False), (timedelta(days=11), False), (timedelta(days=-1), False),
])
def test_window_is_ten_days_from_delivery(age, inside):
    cx = _db()
    sid = _ship(cx, _iso(NOW - age), "T1")
    _order(cx, "a@x.com", "biofield", sid)
    assert (pt.healing_window(cx, "a@x.com", now=NOW) is not None) is inside


def test_undelivered_shipment_opens_nothing():
    cx = _db()
    sid = _ship(cx, None, "T1")
    _order(cx, "a@x.com", "biofield", sid)
    assert pt.healing_window(cx, "a@x.com", now=NOW) is None


@pytest.mark.parametrize("source", ["reorder", "portal-reorder", "funnel", "groovekart",
                                    "membership", "wholesale"])
def test_other_sources_do_not_open_it(source):
    cx = _db()
    sid = _ship(cx, _iso(NOW - timedelta(days=2)), "T1")
    _order(cx, "a@x.com", source, sid)
    assert pt.healing_window(cx, "a@x.com", now=NOW) is None


def test_household_shipment_opens_only_for_the_biofield_member():
    cx = _db()
    sid = _ship(cx, _iso(NOW - timedelta(days=3)), "T1")
    _order(cx, "mum@x.com", "biofield", sid)
    _order(cx, "dad@x.com", "reorder", sid)
    _order(cx, "kid@x.com", "biofield", sid, status="cancelled")
    assert pt.healing_window(cx, "mum@x.com", now=NOW)["day"] == 3
    assert pt.healing_window(cx, "dad@x.com", now=NOW) is None
    assert pt.healing_window(cx, "kid@x.com", now=NOW) is None
    assert pt.healing_window(cx, "stranger@x.com", now=NOW) is None


def test_legacy_shipment_linked_by_order_uuid():
    cx = _db()
    _ship(cx, _iso(NOW - timedelta(days=1)), "T1", order_uuid="uuid-1")
    _order(cx, "a@x.com", "biofield", None, ref="uuid-1")
    assert pt.healing_window(cx, "a@x.com", now=NOW)["day"] == 1


def test_latest_delivery_wins():
    cx = _db()
    s1 = _ship(cx, _iso(NOW - timedelta(days=8)), "T1")
    s2 = _ship(cx, _iso(NOW - timedelta(days=2)), "T2")
    _order(cx, "a@x.com", "biofield", s1)
    _order(cx, "a@x.com", "biofield", s2)
    assert pt.healing_window(cx, "a@x.com", now=NOW)["day"] == 2


def test_window_sql_is_postgres_safe():
    src = inspect.getsource(pt.healing_window)
    for bad in ("datetime('", "julianday", "strftime('%", "lastrowid", "date('now"):
        assert bad not in src


# --- the signs -----------------------------------------------------------------

SIGNS = {
    "old-symptom": ["My old knee pain came back yesterday.", "My eczema is flaring up again.",
                    "I think my old back problem is returning."],
    "tired-aches": ["I'm so tired since the remedies arrived.", "My whole body aches today.",
                    "I feel exhausted."],
    "headache": ["I've had a headache since yesterday.", "I have a migraine today."],
    "rash": ["I have a rash on my arms.", "My skin is breaking out.", "I broke out in spots."],
    "loose-stools": ["I have loose stools.", "I've had diarrhea since Monday."],
    "flu-like": ["I feel flu-like today.", "I have a mild fever.", "I have chills tonight."],
    "mucus": ["I'm coughing up more mucus.", "I have a lot of phlegm today."],
}


@pytest.mark.parametrize("key,msg", [(k, m) for k, ms in SIGNS.items() for m in ms])
def test_each_healing_sign_is_recognised(key, msg):
    assert key in pt.healing_signs(msg), msg


@pytest.mark.parametrize("msg", [
    "My headache is gone, thanks.",
    "I don't have a headache.",
    "What if I get a rash?",
    "Does this help with headaches?",
    "When will my order come back in stock?",
    "I'm tired of waiting for my order.",
    "I had acne as a teenager.",
    "Thanks, I feel great.",
])
def test_routine_messages_are_not_healing_signs(msg):
    assert pt.healing_signs(msg) == [], msg


def test_healing_flag_names_the_window():
    f = pt.healing_flag(["headache", "loose-stools"], {"day": 4})
    assert f["urgency"] == "high" and f["category"] == "health-concern"
    assert f["summary"].startswith("Possible healing crisis, day 4 after delivery")
    assert "a headache; loose stools" in f["summary"]
    assert pt.healing_flag(["headache"], {"day": 0})["summary"].startswith(
        "Possible healing crisis, delivery day")


# --- the chat route ---------------------------------------------------------------

LINE = ("I have passed this to Dr. Glen. This chat is for supporting your "
        "self-healing as a supplement to your local health care.")


class _T:
    def __init__(self, t):
        self.text = t


class _M:
    def __init__(self, t):
        self.content = [_T(t)]


class _S:
    def __init__(self):
        self.text_stream = iter(["Thank you."])

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _Msgs:
    def stream(self, **kw):
        return _S()

    def create(self, **kw):
        return _M('{"needs_attention": false}')


class _Client:
    messages = _Msgs()


class _Inline:
    def __init__(self, target=None, args=(), kwargs=None, daemon=None, **_):
        self.t, self.a, self.k = target, args, kwargs or {}

    def start(self):
        self.t(*self.a, **self.k)


@pytest.fixture
def env(monkeypatch, tmp_path):
    import app as appmod
    monkeypatch.setattr(appmod, "LOG_DB", str(tmp_path / "chat_log.db"))
    monkeypatch.setattr(appmod, "_PORTAL_HEALTH_PROFILE_ENABLED", False)
    monkeypatch.setattr(appmod, "embed", lambda q: (_ for _ in ()).throw(RuntimeError("no rag")))
    monkeypatch.setattr(appmod.ash_ally, "record_turn", lambda *a, **k: None)
    monkeypatch.setattr(appmod.ash_ally, "ally_overlay", lambda *a, **k: "")
    monkeypatch.setattr(appmod, "_active_membership_for_email", lambda em: None)
    monkeypatch.setattr(appmod, "_cl", _Client())
    monkeypatch.setattr(threading, "Thread", _Inline)
    sent = []
    monkeypatch.setattr(appmod, "_send_full_report_email",
                        lambda to, n, subj, body, **kw: sent.append(body) or ("gmail-api", None))
    appmod.app.config["TESTING"] = True
    from dashboard import client_portal as _cp
    with sqlite3.connect(appmod.LOG_DB) as cx:
        _cp.init_client_portal_table(cx)
        token, _ = _cp.upsert_portal(cx, "c@x.com", "Cee", {})
    cx = _db(appmod.LOG_DB)

    def deliver(days_ago, source="biofield"):
        sid = _ship(cx, _iso(datetime.now(timezone.utc) - timedelta(days=days_ago)), f"T{days_ago}{source}")
        _order(cx, "c@x.com", source, sid)

    def chat(q):
        r = appmod.app.test_client().post(f"/api/portal/{token}/chat", json={"query": q})
        text = ""
        for line in r.data.decode().splitlines():
            if line.startswith("data: "):
                text += json.loads(line[6:]).get("token") or ""
        return text

    def items():
        with sqlite3.connect(appmod.LOG_DB) as c2:
            c2.row_factory = sqlite3.Row
            try:
                return [dict(r) for r in c2.execute("SELECT * FROM portal_triage")]
            except sqlite3.OperationalError:
                return []
    return appmod, deliver, chat, items, sent


def test_inside_window_flags_with_the_line(env):
    appmod, deliver, chat, items, sent = env
    deliver(4.2)
    text = chat("I've had a headache since yesterday.")
    assert text.count(LINE) == 1
    rows = items()
    assert len(rows) == 1 and rows[0]["urgency"] == "high"
    assert rows[0]["summary"].startswith("Possible healing crisis, day 4 after delivery")
    assert len(sent) == 1 and "Possible healing crisis" in sent[0]


def test_outside_window_changes_nothing(env):
    appmod, deliver, chat, items, sent = env
    deliver(12)
    text = chat("I've had a headache since yesterday.")
    assert LINE not in text and items() == [] and sent == []


def test_reorder_delivery_does_not_open_it(env):
    appmod, deliver, chat, items, sent = env
    deliver(2, source="reorder")
    assert LINE not in chat("I've had a headache since yesterday.")
    assert items() == []


def test_lookup_error_is_outside_and_warning_backstop_still_runs(env, monkeypatch, capsys):
    appmod, deliver, chat, items, sent = env
    deliver(2)

    def boom(*a, **k):
        raise RuntimeError("db down")
    monkeypatch.setattr(pt, "healing_window", boom)
    assert LINE not in chat("I've had a headache since yesterday.")
    assert items() == []
    assert "treated as outside" in capsys.readouterr().out
    text = chat("My lips are swelling and I have a headache.")
    assert text.count(LINE) == 1
    assert items()[0]["summary"].startswith("Warning sign")


@pytest.mark.parametrize("key,msg", [(k, ms[0]) for k, ms in SIGNS.items()])
def test_each_sign_flags_inside_and_not_outside(env, monkeypatch, key, msg):
    appmod, deliver, chat, items, sent = env
    monkeypatch.setattr(pt, "healing_window", lambda cx, e, **k: None)
    keys, flag = appmod._healing_crisis_check("c@x.com", msg)
    assert flag is None
    monkeypatch.setattr(pt, "healing_window", lambda cx, e, **k: {"day": 3})
    keys, flag = appmod._healing_crisis_check("c@x.com", msg)
    assert key in keys and flag["urgency"] == "high"


def test_no_lookup_without_a_sign(env, monkeypatch):
    appmod, deliver, chat, items, sent = env
    calls = []
    monkeypatch.setattr(pt, "healing_window", lambda *a, **k: calls.append(1))
    assert appmod._healing_crisis_check("c@x.com", "When should I reorder?") == ([], None)
    assert calls == []
