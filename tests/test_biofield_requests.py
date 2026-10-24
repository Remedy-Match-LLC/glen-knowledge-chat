"""Biofield test requests become "Requested" lines in "Your authored tests".

Glen, 2026-10-23. Brief: 00 System/pillar-inboxes/platform/
2026-10-23-biofield-request-capture.md. Covers the classifier's three verdicts, the free
scan rule, dedupe by source_ref, one open line per person, the caregiver line, Start
test, Yes and No, the list rendering, and the prod portal flag and its owner-only GET.
No test calls a model: the classifier is injected.
"""
import json
import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from biofield_local_app import create_app  # noqa: E402
from dashboard import biofield_requests as br  # noqa: E402
from dashboard.biofield_authoring import create_test, init_auth_tables  # noqa: E402
from dashboard.biofield_report_html import render_list_html  # noqa: E402


@pytest.fixture(autouse=True)
def _no_gate(monkeypatch):
    monkeypatch.delenv("CONSOLE_SECRET", raising=False)
    import dashboard as _d
    monkeypatch.setattr(_d, "CONSOLE_SECRET", "", raising=False)


def model(certainty, quote="q", for_name="", for_email=""):
    calls = []

    def complete(system, user):
        calls.append((system, user))
        return json.dumps({"certainty": certainty, "quote": quote, "for_name": for_name,
                           "for_email": for_email})
    complete.calls = calls
    return complete


def req(ref="m1", certainty="clear", email="jane@x.com", name="Jane Doe", source="email",
        payer="", quote="Can I book a Biofield Analysis?"):
    return {"source": source, "source_ref": ref, "email": email, "name": name,
            "payer_email": payer, "requested_at": "2026-10-23T20:00:00Z", "quote": quote,
            "certainty": certainty}


@pytest.fixture
def cx(tmp_path):
    c = sqlite3.connect(str(tmp_path / "local.db"))
    yield c
    c.close()


# ── Classifier ───────────────────────────────────────────────────────────────

def test_clear_possible_and_none():
    clear = br.classify(model("clear", "Can I book a Biofield Analysis?"),
                        "Aloha, can I book a Biofield Analysis next week?", "Jane", "j@x.com")
    assert clear["certainty"] == "clear"
    assert clear["quote"] == "Can I book a Biofield Analysis?"
    possible = br.classify(model("possible", "I'd like to get checked again sometime"),
                           "I'd like to get checked again sometime.")
    assert possible["certainty"] == "possible"
    assert br.classify(model("none"), "Thanks for the remedies!")["certainty"] == "none"


def test_the_classifier_is_told_the_rules_and_the_sender():
    m = model("none")
    br.classify(m, "Hello", "Jane", "jane@x.com")
    system, user = m.calls[0]
    assert system == br.SYSTEM and "$300" in system and "never \"clear\"" in system
    assert "jane@x.com" in user and "Hello" in user


@pytest.mark.parametrize("text", [
    "Can I get a free scan? I want a Biofield.",
    "I want to do Remedy Match again, the biofield thing.",
    "Please book my voice scan, biofield please",
    "I'd like to book a consultation with Dr Glen",
    "Can I get a free scan?",
    "Can I book a Biofield scan?",
    "I'd like my Biofield scan reading please",
    "Is the biofield test free?",
    "Can I sign up for the $100 biofield program?",
    "A free Biofield assessment, please",
    "Book me a Biofield reading, I did the E4L scan",
])
def test_a_free_scan_or_remedy_match_is_never_clear(text):
    assert br.classify(model("clear"), text)["certainty"] == "possible"


@pytest.mark.parametrize("text", [
    "I did the free scan and now I'd like to book a Biofield Analysis.",
    "After my Biofield scan I'd like a consultation, please.",
    "Remedy Match was great. Can I book a biofield consult?",
])
def test_an_explicit_biofield_analysis_or_consult_stays_clear_beside_a_scan(text):
    assert br.classify(model("clear"), text)["certainty"] == "clear"


def test_a_plain_biofield_test_request_stays_clear():
    assert br.classify(model("clear"), "Can I book a Biofield test?")["certainty"] == "clear"


@pytest.mark.parametrize("reply", ["", "not json", '{"certainty": "maybe"}', "[1]"])
def test_garbage_or_a_model_error_is_an_error_verdict(reply):
    assert br.classify(lambda s, u: reply, "Biofield please")["certainty"] == "error"

    def boom(s, u):
        raise RuntimeError("down")
    assert br.classify(boom, "Biofield please")["certainty"] == "error"
    assert br.classify(model("none"), "Biofield please")["certainty"] == "none"


def test_an_error_is_not_remembered_so_the_next_run_retries(cx):
    out = br.record(cx, req("m5", certainty="error"))
    assert out["kind"] == "error"
    assert not br.is_seen(cx, "email", "m5")
    assert br.record(cx, req("m5"))["kind"] == "added"


def test_the_quote_is_one_line():
    v = br.classify(model("possible", "line one\nline two  " + "x" * 400), "check me again")
    assert "\n" not in v["quote"] and len(v["quote"]) <= 200


# ── Caregiver ────────────────────────────────────────────────────────────────

def test_a_caregiver_request_is_the_members_line_with_the_caregiver_as_payer(cx):
    v = br.classify(model("clear", "Can I book a Biofield Analysis for my son Sam?",
                          for_name="Sam Doe"),
                    "Can I book a Biofield Analysis for my son Sam?", "Jane Doe", "jane@x.com")
    [(name, email, payer)] = br.requests_for(v, "Jane Doe", "jane@x.com")
    assert (name, email, payer) == ("Sam Doe", "jane@x.com", "jane@x.com")
    v2 = {"people": [{"name": "Sam Doe", "email": "sam@x.com"}]}
    assert br.requests_for(v2, "Jane Doe", "jane@x.com") == [("Sam Doe", "sam@x.com", "jane@x.com")]
    assert br.requests_for({"people": []}, "Jane", "JANE@x.com") == [("Jane", "jane@x.com", "")]
    out = br.record(cx, req(name=name, email=email, payer=payer))
    row = br.get(cx, out["id"])
    assert row["name"] == "Sam Doe" and row["payer_email"] == "jane@x.com"


def test_a_members_line_is_not_hidden_by_the_caregivers_own_open_line(cx):
    br.record(cx, req("m1", name="Jane Doe"))
    out = br.record(cx, req("m2", name="Sam Doe", payer="jane@x.com"))
    assert out["kind"] == "added"
    again = br.record(cx, req("m3", name="Sam Doe", payer="jane@x.com"))
    assert again["kind"] == "duplicate"


def test_the_caregivers_own_request_is_not_hidden_by_a_members_line(cx):
    br.record(cx, req("m1", name="Sam Doe", payer="jane@x.com"))
    out = br.record(cx, req("m2", name="Jane Doe"))
    assert out["kind"] == "added"
    assert br.record(cx, req("m3", name="Jane Doe"))["kind"] == "duplicate"


def test_names_on_a_shared_address_must_be_exactly_equal():
    member = {"email": "jane@x.com", "name": "Sam", "payer_email": "jane@x.com"}
    assert not br.same_person(member, {"email": "jane@x.com", "name": "Samantha Doe"})
    assert not br.same_person({"email": "jane@x.com", "name": "Samantha Doe"}, member)
    assert br.same_person(member, {"email": "JANE@x.com", "name": " sam "})
    # Not a shared address: the address alone decides.
    assert br.same_person({"email": "a@x.com", "name": "Al"}, {"email": "a@x.com", "name": "B"})


def test_unfinished_tests_on_a_household_address_match_by_name(cx):
    member_test = [{"test_id": "a7", "email": "jane@x.com", "name": "Sam Doe",
                    "household": True, "note": ""}]
    assert br.record(cx, req("m1", name="Jane Doe"), member_test)["kind"] == "added"
    assert br.record(cx, req("m2", name="Sam Doe", payer="jane@x.com"),
                     member_test)["kind"] == "duplicate"
    plain = [{"test_id": "a8", "email": "kim@x.com", "name": "Kim", "note": ""}]
    assert br.record(cx, req("m3", email="kim@x.com", name="Sam Doe", payer="kim@x.com"),
                     plain)["kind"] == "added"


def test_unfinished_tests_flag_a_household_address(cx):
    init_auth_tables(cx)
    create_test(cx, "Jane Doe", "jane@x.com", "2026-09-01")
    create_test(cx, "Sam Doe", "jane@x.com", "2026-09-02")
    create_test(cx, "Kim", "kim@x.com", "2026-09-03")
    got = {t["test_id"]: t["household"] for t in br.unfinished_tests(cx)}
    assert sorted(got.values()) == [False, True, True]


def test_a_members_started_test_does_not_block_the_caregivers_own_request(cx):
    """Tom's test was started from Sue's request and filed at sue@x.com. Sue's own
    request is hers, even with only one test on the address."""
    tom = br.record(cx, req("m1", email="sue@x.com", name="Tom Poe", payer="sue@x.com"))
    tid, _ = br.start(cx, tom["id"], date="2026-10-23")
    got = br.unfinished_tests(cx)
    assert [(t["test_id"], t["payer_email"], t["household"]) for t in got] == \
        [(tid, "sue@x.com", False)]
    out = br.record(cx, req("m2", email="sue@x.com", name="Sue Poe"), got)
    assert out["kind"] == "added"
    again = br.record(cx, req("m3", email="sue@x.com", name="Tom Poe", payer="sue@x.com"),
                      got)
    assert again["kind"] == "duplicate" and tid in again["note"]


# ── Store: dedupe and one open line per person ──────────────────────────────

def test_clear_adds_a_line_and_possible_waits(cx):
    a = br.record(cx, req("m1"))
    b = br.record(cx, req("m2", certainty="possible", email="bob@x.com", name="Bob"))
    assert (a["kind"], b["kind"]) == ("added", "pending")
    assert [r["status"] for r in br.list_open(cx)] in (["requested", "pending_yes"],
                                                      ["pending_yes", "requested"])


def test_dedupe_by_source_ref(cx):
    assert br.record(cx, req("m1"))["kind"] == "added"
    assert br.record(cx, req("m1", email="other@x.com"))["kind"] == "seen"
    assert br.record(cx, req("m1", source="ghl", email="z@x.com"))["kind"] == "added"
    n = cx.execute("SELECT COUNT(*) FROM biofield_test_requests").fetchone()[0]
    assert n == 2
    with pytest.raises(sqlite3.IntegrityError):
        cx.execute("INSERT INTO biofield_test_requests(email, source, source_ref, certainty, "
                   "status, created_at) VALUES('a','email','m1','clear','requested','t')")


def test_none_is_remembered_so_it_is_not_classified_again(cx):
    assert br.record(cx, req("m9", certainty="none"))["kind"] == "none"
    assert br.is_seen(cx, "email", "m9")
    assert br.list_open(cx) == []
    cols = [c[1] for c in cx.execute("PRAGMA table_info(biofield_request_seen)")]
    assert cols == ["source", "source_ref", "certainty", "seen_at"]   # no address kept


def test_one_open_line_per_person_open_request(cx):
    br.record(cx, req("m1"))
    out = br.record(cx, req("m2", email="JANE@x.com"))
    assert out["kind"] == "duplicate" and "open Requested line" in out["note"]
    assert len(br.list_open(cx)) == 1


def test_a_clear_request_upgrades_a_waiting_line(cx):
    a = br.record(cx, req("m1", certainty="possible", quote="check me sometime"))
    br.mark_alerted(cx, [a["id"]])
    up = br.record(cx, dict(req("m2", quote="Can I book a Biofield Analysis?"),
                            requested_at="2026-10-24T01:00:00Z"))
    assert up == {"kind": "upgraded", "id": a["id"], "note": ""}
    r = br.get(cx, a["id"])
    assert (r["status"], r["certainty"], r["quote"], r["requested_at"]) == \
        ("requested", "clear", "Can I book a Biofield Analysis?", "2026-10-24T01:00:00Z")
    assert r["alerted_at"] is None                      # Glen hears it is now Requested
    assert br.is_seen(cx, "email", "m2")
    assert br.record(cx, req("m2"))["kind"] == "seen"
    assert len(br.list_open(cx)) == 1


def test_a_possible_request_beside_a_waiting_line_is_not_a_second_line(cx):
    br.record(cx, req("m1", certainty="possible"))
    out = br.record(cx, req("m2", certainty="possible"))
    assert out["kind"] == "duplicate" and "waiting for your yes" in out["note"]
    assert "open Requested line" not in out["note"]


def test_a_waiting_line_is_not_an_open_requested_line_for_an_unfinished_check(cx):
    br.record(cx, req("m1", certainty="possible"))
    unfinished = [{"test_id": "a7", "email": "jane@x.com", "name": "Jane Doe", "note": ""}]
    assert br.record(cx, req("m2"), unfinished)["kind"] == "upgraded"


def test_one_open_line_per_person_unfinished_test(cx):
    unfinished = [{"test_id": "a7", "email": "jane@x.com", "name": "Jane Doe", "note": ""}]
    out = br.record(cx, req("m1"), unfinished)
    assert out["kind"] == "duplicate" and "a7" in out["note"]
    assert br.list_open(cx) == []


def _test_at(cx, name, email, created):
    tid = create_test(cx, name, email, "2026-09-01")
    cx.execute("UPDATE biofield_auth_tests SET created_at=? WHERE id=?", (created, int(tid[1:])))
    cx.commit()
    return tid


def test_a_test_blocks_only_while_unreported_and_recent(cx):
    import datetime as dt
    from dashboard import biofield_test_notified as btn
    init_auth_tables(cx)
    now = dt.datetime(2026, 10, 23, 20, 0, tzinfo=dt.timezone.utc)
    recent = _test_at(cx, "Jane", "jane@x.com", "2026-10-13T20:00:00Z")
    old = _test_at(cx, "Bob", "bob@x.com", "2026-08-20T20:00:00Z")      # 64 days
    sent = _test_at(cx, "Kim", "kim@x.com", "2026-10-20T20:00:00Z")
    btn.mark(cx, sent)
    assert [t["test_id"] for t in br.unfinished_tests(cx, now)] == [recent]
    assert all("may be finished" not in t["note"] for t in br.unfinished_tests(cx, now))


def test_unfinished_tests_need_no_orders_and_write_nothing(tmp_path):
    db = str(tmp_path / "ro.db")
    with sqlite3.connect(db) as c:
        init_auth_tables(c)
        create_test(c, "Jane", "jane@x.com", "2026-10-01")
    ro = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    assert [t["email"] for t in br.unfinished_tests(ro)] == ["jane@x.com"]
    import inspect
    assert list(inspect.signature(br.unfinished_tests).parameters) == ["cx", "now"]


# ── Yes, No and Start test ──────────────────────────────────────────────────

def test_yes_makes_a_requested_line_and_no_declines(cx):
    a = br.record(cx, req("m1", certainty="possible"))
    b = br.record(cx, req("m2", certainty="possible", email="bob@x.com"))
    assert br.approve(cx, a["id"]) == (True, "")
    assert br.get(cx, a["id"])["status"] == "requested"
    assert br.decline(cx, b["id"]) == (True, "")
    assert br.get(cx, b["id"])["status"] == "declined"
    assert br.approve(cx, b["id"])[0] is False          # a No is final
    assert br.decline(cx, a["id"])[0] is False          # only a waiting line takes No
    assert [r["id"] for r in br.list_open(cx)] == [a["id"]]


def test_start_creates_and_links_the_test(cx):
    a = br.record(cx, req("m1", name="Jane Doe"))
    tid, err = br.start(cx, a["id"], date="2026-10-24")
    assert err is None and tid.startswith("a")
    row = cx.execute("SELECT name, email, date_test FROM biofield_auth_tests WHERE id=?",
                     (int(tid[1:]),)).fetchone()
    assert row == ("Jane Doe", "jane@x.com", "2026-10-24")
    r = br.get(cx, a["id"])
    assert r["status"] == "started" and r["test_id"] == tid
    assert br.list_open(cx) == []
    # A second click opens the same test, never a second one.
    assert br.start(cx, a["id"]) == (tid, None)
    assert cx.execute("SELECT COUNT(*) FROM biofield_auth_tests").fetchone()[0] == 1


def test_start_refuses_a_line_waiting_for_yes(cx):
    a = br.record(cx, req("m1", certainty="possible"))
    tid, err = br.start(cx, a["id"])
    assert tid is None and err
    init_auth_tables(cx)
    assert cx.execute("SELECT COUNT(*) FROM biofield_auth_tests").fetchone()[0] == 0


def test_a_failed_create_puts_the_request_back(cx, monkeypatch):
    a = br.record(cx, req("m1"))
    import dashboard.biofield_authoring as ba
    monkeypatch.setattr(ba, "create_test", lambda *a, **k: (_ for _ in ()).throw(
        RuntimeError("disk")))
    with pytest.raises(RuntimeError):
        br.start(cx, a["id"])
    assert br.get(cx, a["id"])["status"] == "requested"


def test_start_creates_the_test_whole_in_one_call(cx, monkeypatch):
    import dashboard.biofield_authoring as ba
    calls, real = [], ba.create_test
    monkeypatch.setattr(ba, "create_test", lambda c, n, e, d: calls.append((n, e, d)) or real(c, n, e, d))
    monkeypatch.setattr(ba, "update_header", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("no second write")))
    a = br.record(cx, req("m1", name="Jane Doe"))
    tid, err = br.start(cx, a["id"], date="2026-10-24")
    assert err is None and calls == [("Jane Doe", "jane@x.com", "2026-10-24")]
    assert cx.execute("SELECT COUNT(*) FROM biofield_auth_tests WHERE COALESCE(name,'')=''"
                      ).fetchone()[0] == 0


def test_a_stuck_start_is_reclaimed_after_a_few_minutes(cx):
    import datetime as dt
    a = br.record(cx, req("m1"))
    now = dt.datetime(2026, 10, 23, 20, 0, tzinfo=dt.timezone.utc)
    cx.execute("UPDATE biofield_test_requests SET status='started', decided_at=? WHERE id=?",
               ("2026-10-23T19:58:00Z", a["id"]))
    cx.commit()
    tid, err = br.start(cx, a["id"], now=now)
    assert tid is None and "being created" in err
    cx.execute("UPDATE biofield_test_requests SET decided_at=? WHERE id=?",
               ("2026-10-23T19:50:00Z", a["id"]))
    cx.commit()
    tid, err = br.start(cx, a["id"], now=now)
    assert err is None and br.get(cx, a["id"])["test_id"] == tid


# ── The list and its routes ─────────────────────────────────────────────────

def _client(tmp_path):
    db = str(tmp_path / "app.db")
    app = create_app(db_path=db, progress_orders=lambda ids: ([], None))
    return app.test_client(), db


def test_the_list_shows_requested_lines_with_their_steps(tmp_path):
    client, db = _client(tmp_path)
    with sqlite3.connect(db) as cx:
        a = br.record(cx, req("m1", name="Jane Doe", source="ghl"))
        b = br.record(cx, req("m2", certainty="possible", email="bob@x.com", name="Bob",
                              quote="I'd like to get checked again sometime"))
    html = client.get("/").get_data(as_text=True)
    assert f"id=req-{a['id']}" in html and f"id=req-{b['id']}" in html
    assert "Jane Doe" in html and "jane@x.com" in html and "by GoHighLevel" in html
    assert f'data-url="/request/{a["id"]}/start"' in html and "Start test" in html
    assert f'data-url="/request/{b["id"]}/yes"' in html
    assert f'data-url="/request/{b["id"]}/no"' in html
    assert f'data-url="/request/{b["id"]}/start"' not in html
    assert "<span class=pill>Possible</span> Bob" in html
    assert "<span class=pill>Requested</span> Jane Doe" in html
    assert "function reqAct" in html


def test_the_routes_start_yes_and_no(tmp_path):
    client, db = _client(tmp_path)
    with sqlite3.connect(db) as cx:
        a = br.record(cx, req("m1"))
        b = br.record(cx, req("m2", certainty="possible", email="bob@x.com"))
        c = br.record(cx, req("m3", certainty="possible", email="cy@x.com"))
    r = client.post(f"/request/{b['id']}/start", json={})
    assert r.status_code == 409
    assert client.post(f"/request/{b['id']}/yes", json={}).get_json()["ok"]
    assert client.post(f"/request/{c['id']}/no", json={}).get_json()["ok"]
    j = client.post(f"/request/{a['id']}/start", json={}).get_json()
    assert j["ok"] and j["url"] == f"/author/{j['test_id']}"
    with sqlite3.connect(db) as cx:
        assert br.get(cx, a["id"])["test_id"] == j["test_id"]
        assert br.get(cx, c["id"])["status"] == "declined"
        assert br.get(cx, b["id"])["status"] == "requested"


def test_render_without_requests_is_unchanged():
    assert "reqAct" not in render_list_html([], "", [])


# ── Alert text (sent by the vault job) ──────────────────────────────────────

def test_the_alert_names_quotes_and_links_each_line(cx):
    a = br.record(cx, req("m1", name="Jane Doe"))
    b = br.record(cx, req("m2", certainty="possible", email="bob@x.com", name="Bob",
                          quote="I'd like to get checked again sometime"))
    d = br.record(cx, req("m3", email="jane@x.com", name="Jane Doe"))
    subject, body = br.compose_alert(br.unalerted(cx), "http://127.0.0.1:8011")
    assert "1 added" in subject and "1 waiting" in subject and "1 not added" in subject
    assert "Jane Doe <jane@x.com>" in body and "\"Can I book a Biofield Analysis?\"" in body
    assert f"http://127.0.0.1:8011/#req-{a['id']}" in body
    assert f"http://127.0.0.1:8011/#req-{b['id']}" in body and "Yes or No" in body
    assert f"#req-{d['id']}" not in body and "open Requested line" in body
    assert br.mark_alerted(cx, [a["id"], b["id"], d["id"]]) == 3
    assert br.unalerted(cx) == [] and br.compose_alert([], "x") == (None, None)


# ── Prod: the portal flag and its owner-only GET ────────────────────────────

class _Msg:
    def __init__(self, text):
        self.content = [SimpleNamespace(text=text)]


class _FakeCl:
    def __init__(self, verdict):
        self.verdict, self.systems = verdict, []
        self.raw = verdict if isinstance(verdict, str) else None

    @property
    def messages(self):
        return self

    def create(self, **kw):
        self.systems.append(kw["system"])
        assert kw["model"] == "claude-haiku-4-5-20251001"
        if kw["system"] == br.SYSTEM:
            return _Msg(self.raw if self.raw is not None else json.dumps(self.verdict))
        return _Msg('{"needs_attention": false}')


@pytest.fixture
def prod(tmp_path, monkeypatch):
    import app as appmod
    monkeypatch.setattr(appmod, "LOG_DB", str(tmp_path / "chat_log.db"))
    return appmod


def test_a_portal_request_is_flagged_once_on_prod(prod, monkeypatch):
    fake = _FakeCl({"certainty": "clear", "quote": "Can I book a Biofield Analysis?"})
    monkeypatch.setattr(prod, "_cl", fake)
    prod._triage_portal_message("c@x.com", "Cee", "Can I book a Biofield Analysis?", "ok")
    prod._triage_portal_message("c@x.com", "Cee", "Can I book a Biofield Analysis?", "ok")
    assert br.SYSTEM in fake.systems
    with sqlite3.connect(prod.LOG_DB) as cx:
        rows = cx.execute("SELECT email, certainty, quote FROM biofield_request_flags").fetchall()
    assert rows == [("c@x.com", "clear", "Can I book a Biofield Analysis?")]


def test_no_flag_for_an_ordinary_message_and_a_model_error_is_kept_for_a_yes(prod,
                                                                            monkeypatch):
    monkeypatch.setattr(prod, "_cl", _FakeCl({"certainty": "none"}))
    prod._triage_portal_message("c@x.com", "Cee", "When do I take the drops?", "ok")

    class Boom:
        class messages:
            @staticmethod
            def create(**kw):
                raise RuntimeError("down")
    monkeypatch.setattr(prod, "_cl", Boom())
    prod._triage_portal_message("c@x.com", "Cee", "Biofield please", "ok")
    with sqlite3.connect(prod.LOG_DB) as cx:
        has = cx.execute("SELECT 1 FROM sqlite_master WHERE name='biofield_request_flags'"
                         ).fetchone()
        rows = cx.execute("SELECT quote, certainty, note FROM biofield_request_flags"
                          ).fetchall() if has else []
    # The ordinary message is not flagged; the one the model failed on is kept for a yes.
    assert rows == [("Biofield please", "possible", "classifier failed")]


def test_the_flags_endpoint_is_owner_only(prod, monkeypatch):
    with sqlite3.connect(prod.LOG_DB) as cx:
        br.add_flag(cx, "c@x.com", "Cee", {"certainty": "possible", "quote": "check me"}, "check me")
    c = prod.app.test_client()
    for actor in (None, SimpleNamespace(role="staff", name="s")):
        monkeypatch.setattr(prod, "_bos_actor", lambda a=actor: a)
        assert c.get("/api/console/biofield-request-flags").status_code == 401
    monkeypatch.setattr(prod, "_bos_actor", lambda: SimpleNamespace(role="owner", name="o"))
    j = c.get("/api/console/biofield-request-flags?days=7").get_json()
    assert j["ok"] and [f["quote"] for f in j["flags"]] == ["check me"]
    assert "message" not in j["flags"][0]               # the full message stays on prod
    assert c.get("/api/console/biofield-request-flags?days=x").status_code == 400


def test_a_flag_becomes_a_portal_request():
    f = {"id": 12, "email": "jane@x.com", "name": "Jane", "certainty": "clear",
         "quote": "q", "for_name": "Sam", "for_email": "", "created_at": "2026-10-23T01:00:00Z"}
    r = br.flag_to_request(f)
    assert (r["source"], r["source_ref"], r["name"], r["email"], r["payer_email"]) == \
        ("portal", "12", "Sam", "jane@x.com", "jane@x.com")


def test_a_classifier_error_on_prod_is_flagged_possible_for_glens_yes(prod, monkeypatch,
                                                                       capsys):
    monkeypatch.setattr(prod, "_cl", _FakeCl("not json at all"))
    # No Biofield keyword: logged and dropped, so an outage does not flag every chat.
    assert prod._flag_biofield_request("c@x.com", "Cee", "Hello\nthere") is None
    assert "no Biofield keyword, not flagged" in capsys.readouterr().out
    assert prod._flag_biofield_request("c@x.com", "Cee", "My biofield\nagain?") is not None
    assert "classifier failed for c@x.com; flagged" in capsys.readouterr().out
    with sqlite3.connect(prod.LOG_DB) as cx:
        rows = cx.execute("SELECT certainty, note, quote FROM biofield_request_flags").fetchall()
    assert rows == [("possible", "classifier failed", "My biofield again?")]
    with sqlite3.connect(prod.LOG_DB) as cx:
        [f] = br.list_flags(cx, "2000-01-01")
    r = br.flag_to_request(f)
    assert r["certainty"] == "possible" and r["note"] == "classifier failed"


def test_a_portal_request_for_two_children_is_two_flags(prod, monkeypatch):
    fake = _FakeCl({"certainty": "clear", "quote": "q", "people": [
        {"name": "Tom Poe", "email": ""}, {"name": "Amy Poe", "email": "amy@x.com"}]})
    monkeypatch.setattr(prod, "_cl", fake)
    prod._flag_biofield_request("sue@x.com", "Sue Poe", "Biofield Analysis for Tom and Amy?")
    with sqlite3.connect(prod.LOG_DB) as cx:
        flags = br.list_flags(cx, "2000-01-01")
    got = [(br.flag_to_request(f)["name"], br.flag_to_request(f)["email"]) for f in flags]
    assert got == [("Tom Poe", "sue@x.com"), ("Amy Poe", "amy@x.com")]


def _erasure_db(tmp_path):
    c = sqlite3.connect(str(tmp_path / "e.db"))
    br.ensure_schema(c)
    br.record(c, req("m1", email="sam@x.com", name="Sam", payer="jane@x.com"))
    br.record(c, req("m2", email="kim@x.com", name="Kim"))
    br.record(c, req("m3", email="jane@x.com", name="Tom", payer="jane@x.com"))
    br.add_flag(c, "jane@x.com", "Jane", {"certainty": "clear", "quote": "q"}, "m",
                person={"name": "Sam", "email": "sam@x.com"})
    br.add_flag(c, "kim@x.com", "Kim", {"certainty": "clear", "quote": "q"}, "m")
    return c


def test_erasing_a_member_keeps_the_caregivers_flag_without_the_members_address(tmp_path):
    from dashboard import client_erasure as ce
    c = _erasure_db(tmp_path)
    assert ce.plan(c, "sam@x.com") == {"biofield_test_requests": 1, "biofield_request_flags": 1}
    ce.erase(c, "sam@x.com", confirm="sam@x.com")
    assert [r[0] for r in c.execute("SELECT email FROM biofield_test_requests ORDER BY id")] \
        == ["kim@x.com", "jane@x.com"]
    assert c.execute("SELECT email, for_email FROM biofield_request_flags ORDER BY id"
                     ).fetchall() == [("jane@x.com", None), ("kim@x.com", "")]


def test_erasing_a_caregiver_keeps_a_members_own_row_and_drops_the_payer(tmp_path):
    from dashboard import client_erasure as ce
    c = _erasure_db(tmp_path)
    ce.erase(c, "jane@x.com", confirm="jane@x.com")
    rows = c.execute("SELECT email, payer_email FROM biofield_test_requests ORDER BY id"
                     ).fetchall()
    assert rows == [("sam@x.com", None), ("kim@x.com", None)]   # Tom, filed at jane@, is gone
    assert c.execute("SELECT email FROM biofield_request_flags").fetchall() == [("kim@x.com",)]


def test_two_children_make_two_lines_each_deduped(cx):
    v = br.classify(model("clear", "for my son Tom and daughter Amy"),
                    "Can I book a Biofield Analysis for my son Tom and daughter Amy?",
                    "Sue Poe", "sue@x.com")
    v = dict(v, people=[{"name": "Tom Poe", "email": ""}, {"name": "Amy Poe", "email": ""}])
    lines = br.requests_for(v, "Sue Poe", "sue@x.com")
    assert lines == [("Tom Poe", "sue@x.com", "sue@x.com"), ("Amy Poe", "sue@x.com", "sue@x.com")]
    refs = br.source_refs("m1", len(lines))
    assert refs == ["m1", "m1#2"]
    kinds = [br.record(cx, req(r, email=e, name=n, payer=p))["kind"]
             for r, (n, e, p) in zip(refs, lines)]
    assert kinds == ["added", "added"]
    # Tom asked again: his line is open, Amy's is too; no new lines.
    assert br.record(cx, req("m2", email="sue@x.com", name="Tom Poe", payer="sue@x.com")
                     )["kind"] == "duplicate"


def test_the_classifier_returns_each_person_and_the_sender_among_them():
    v = br.classify(model("clear", "q"), "Biofield Analysis for me and my son Tom",
                    "Sue Poe", "sue@x.com")
    v["people"] = br._people({"people": [{"name": "Sue Poe"}, {"name": "Tom Poe",
                                                              "email": "not-an-address"}]})
    assert br.requests_for(v, "Sue Poe", "sue@x.com") == [
        ("Sue Poe", "sue@x.com", ""), ("Tom Poe", "sue@x.com", "sue@x.com")]
    assert "\"people\"" in br.SYSTEM


def test_a_not_added_alert_is_not_repeated_within_seven_days(cx):
    br.record(cx, req("m1"))                                  # Jane's open line
    first = br.record(cx, req("m2"))
    br.mark_alerted(cx, [r["id"] for r in br.unalerted(cx)])  # Glen was told about m2
    second = br.record(cx, req("m3"))
    other_source = br.record(cx, req("g1", source="ghl"))
    assert [first["kind"], second["kind"], other_source["kind"]] == ["duplicate"] * 3
    alerted = {r["source_ref"] for r in br.unalerted(cx)}
    assert "g1" in alerted and "m3" not in alerted
    assert "not repeated" in br.get(cx, second["id"])["note"]
    assert "m3" not in {r["source_ref"] for r in br.unnoted(cx)}


def test_the_alert_link_names_the_client(cx):
    a = br.record(cx, req("m1", name="Jane Doe"))
    _, body = br.compose_alert(br.unalerted(cx), "http://127.0.0.1:8011")
    assert f"Line: Jane Doe in Your authored tests, http://127.0.0.1:8011/#req-{a['id']}" in body


def test_new_tables_are_listed_for_erasure_and_merge():
    from dashboard import client_erasure as ce, person_merge_discover as pmd
    for t in ("biofield_test_requests", "biofield_request_flags"):
        assert ce.HEALTH_TABLES[t] == "email"
        assert t in pmd.MOVE_TABLES


# ── Verify round (2026-10-23) ───────────────────────────────────────────────

def _cols(cx, table):
    return [r[1] for r in cx.execute(f"PRAGMA table_info({table})")]


def test_an_older_table_gains_every_later_column(cx):
    # The first versions of both tables, before any later column.
    cx.execute("""CREATE TABLE biofield_test_requests(
        id INTEGER PRIMARY KEY AUTOINCREMENT, email TEXT NOT NULL, name TEXT,
        source TEXT NOT NULL, source_ref TEXT NOT NULL, requested_at TEXT, quote TEXT,
        certainty TEXT NOT NULL, payer_email TEXT, status TEXT NOT NULL, test_id TEXT,
        created_at TEXT NOT NULL)""")
    cx.execute("""CREATE TABLE biofield_request_flags(
        id INTEGER PRIMARY KEY AUTOINCREMENT, email TEXT, name TEXT, certainty TEXT,
        quote TEXT, for_name TEXT, for_email TEXT, message TEXT, created_at TEXT,
        dedupe_key TEXT)""")
    cx.execute("INSERT INTO biofield_test_requests(email, source, source_ref, certainty, "
               "status, created_at) VALUES('old@x.com','email','m0','clear','requested','t')")
    cx.commit()
    for _ in range(2):                                   # idempotent
        br.ensure_schema(cx)
        br.init_flags_table(cx)
    for table, later in br._LATER_COLUMNS.items():
        assert {c for c, _ in later} <= set(_cols(cx, table)), table
    assert cx.execute("SELECT quiet FROM biofield_test_requests").fetchone() == (0,)
    # The full write path works on the migrated tables.
    assert br.record(cx, req("m1", email="new@x.com"))["kind"] == "added"
    br.add_flag(cx, "c@x.com", "Cee", {"certainty": "possible", "quote": "q"}, "q",
                note="classifier failed")
    assert br.list_flags(cx, "2000-01-01")[0]["note"] == "classifier failed"


def test_every_later_column_is_in_the_create_statement_too(tmp_path):
    c = sqlite3.connect(str(tmp_path / "fresh.db"))
    br.ensure_schema(c)
    br.init_flags_table(c)
    for table, later in br._LATER_COLUMNS.items():
        assert {col for col, _ in later} <= set(_cols(c, table)), table
    c.close()


def test_a_weekly_writer_is_alerted_at_most_once_every_seven_days(cx):
    """Jane, with an open line, writes again every 4 days. Glen hears on day 0, 8 and
    16: never twice inside 7 days, and never silenced for good by the quiet ones."""
    import datetime as dt
    br.record(cx, req("m1"))                                  # Jane's open line
    quiet = []
    for n in range(5):                                        # days 0, 4, 8, 12, 16
        out = br.record(cx, req(f"m{n + 2}"))
        quiet.append(br.get(cx, out["id"])["quiet"])
        br.mark_alerted(cx, [r["id"] for r in br.unalerted(cx)])
        for rid, at in cx.execute("SELECT id, alerted_at FROM biofield_test_requests "
                                  "WHERE alerted_at IS NOT NULL").fetchall():
            t = dt.datetime.strptime(at, "%Y-%m-%dT%H:%M:%SZ") - dt.timedelta(days=4)
            cx.execute("UPDATE biofield_test_requests SET alerted_at=? WHERE id=?",
                       (t.strftime("%Y-%m-%dT%H:%M:%SZ"), rid))
        cx.commit()
    assert quiet == [0, 1, 0, 1, 0]


def test_quiet_rows_and_unalerted_duplicates_do_not_silence(cx):
    br.record(cx, req("m1"))                                  # Jane's open line
    first = br.record(cx, req("m2"))                          # not alerted yet
    second = br.record(cx, req("m3"))
    assert br.get(cx, second["id"])["quiet"] == 0             # an unalerted one counts not
    br.mark_alerted(cx, [r["id"] for r in br.unalerted(cx)])
    third = br.record(cx, req("m4"))
    assert br.get(cx, third["id"])["quiet"] == 1              # within 7 days of an alert
    # The alerted ones age out; only the quiet one is recent. It must not silence.
    cx.execute("UPDATE biofield_test_requests SET alerted_at='2000-01-01T00:00:00Z' "
               "WHERE id IN (?,?)", (first["id"], second["id"]))
    cx.commit()
    fourth = br.record(cx, req("m5"))
    assert br.get(cx, fourth["id"])["quiet"] == 0


# ── Paid Biofield Analysis orders (Glen, 2026-10-23: "yes, paid analysis alerts",
#    then "bundled as possible") ───────────────────────────────────────────────

def _order(oid=271, items=("biofield-analysis",), pay="paid",
           paid_at="2026-10-23T19:01:34.444813+00:00", total=20000, **kw):
    o = {"id": oid, "pay_status": pay, "paid_at": paid_at, "total_cents": total,
         "email": " Dana@X.com ", "name": "Dana Tamraz",
         "items": [{"slug": s, "name": s, "qty": 1} for s in items]}
    o.update(kw)
    return o


def test_an_order_of_the_analysis_alone_is_a_clear_request():
    r = br.order_to_request(_order())
    assert r == {"source": "order", "source_ref": "271", "email": "dana@x.com",
                 "name": "Dana Tamraz", "payer_email": "", "requested_at": "2026-10-23T19:01:34Z",
                 "quote": "Paid order 271, order total $200: Biofield Analysis only",
                 "certainty": "clear", "note": ""}


def test_an_analysis_billed_with_remedies_waits_for_glens_yes():
    r = br.order_to_request(_order(231, ("clarity", "biofield-analysis", "liver-support"),
                                   total=45950))
    assert r["certainty"] == "possible"
    assert r["quote"] == ("Paid order 231, order total $459.50: Biofield Analysis with 2 "
                          "other items")
    assert r["note"] == "billed with clarity, liver-support, so the analysis may already be done"
    one = br.order_to_request(_order(9, ("biofield-analysis", "clarity")))
    assert one["quote"].endswith("with 1 other item")
    many = br.order_to_request(_order(5, ("biofield-analysis",) + tuple("abcdefg")))
    assert many["note"] == "billed with a, b, c, d, e and 2 more, so the analysis may already be done"


@pytest.mark.parametrize("o", [
    _order(pay="unpaid"),
    _order(paid_at=None),
    _order(paid_at="not a date"),
    _order(items=("clarity",)),
    _order(items=()),
    _order(superseded_by_order_id=300),
])
def test_an_order_that_is_not_a_paid_analysis_is_no_request(o):
    assert br.order_to_request(o) is None


def test_a_paid_order_files_once_and_a_repeat_is_seen(cx):
    first = br.record(cx, br.order_to_request(_order()))
    assert first["kind"] == "added"
    assert br.record(cx, br.order_to_request(_order()))["kind"] == "seen"
    [row] = br.list_open(cx)
    assert (row["source"], row["status"], row["certainty"]) == ("order", "requested", "clear")


def test_a_bundled_order_lands_as_a_possible_line_with_yes_and_no(cx):
    out = br.record(cx, br.order_to_request(_order(231, ("biofield-analysis", "clarity"))))
    assert out["kind"] == "pending"
    subject, body = br.compose_alert(br.unalerted(cx), "http://127.0.0.1:8011")
    assert "1 waiting for your yes" in subject
    assert "by paid order on 2026-10-23" in body
    assert "  Order: Paid order 231, order total $200: Biofield Analysis with 1 other item" in body
    assert '"Paid order' not in body
    assert "Note: billed with clarity, so the analysis may already be done." in body


def test_a_paid_order_for_someone_with_an_open_line_is_not_a_second_line(cx):
    br.record(cx, req("m1", email="dana@x.com", name="Dana Tamraz"))
    out = br.record(cx, br.order_to_request(_order()))
    assert out["kind"] == "duplicate"
    assert len(br.list_open(cx)) == 1


def test_the_list_names_a_paid_order_as_its_source(cx):
    br.record(cx, br.order_to_request(_order()))
    html = render_list_html([], "", [], br.list_open(cx))
    assert "by paid order" in html
    assert "Paid order 271, order total $200: Biofield Analysis only</div>" in html
    assert "&ldquo;Paid order" not in html


def test_a_card_checkout_line_has_a_name_and_no_slug():
    o = _order()
    o["items"] = [{"name": " Causal Biofield Analysis ", "qty": 1, "desc": "x"}]
    assert br.order_to_request(o)["certainty"] == "clear"
    o["items"].append({"name": "Clarity", "qty": 1})
    assert br.order_to_request(o)["note"].startswith("billed with Clarity")
    # A slug decides on its own: a named line with another slug is not the analysis.
    o["items"] = [{"slug": "clarity", "name": "Causal Biofield Analysis"}]
    assert br.order_to_request(o) is None


def test_the_card_checkout_name_matches_the_app():
    import app as appmod
    assert br.ANALYSIS_NAME == appmod._BIOFIELD_ITEM_NAME
    for it in ({"slug": "biofield-analysis"}, {"slug": " biofield-analysis "},
               {"name": "Causal Biofield Analysis"}, {"slug": "clarity"}, {"name": "Clarity"},
               {"slug": 5, "name": "Causal Biofield Analysis"}):
        assert br.is_analysis_item(it) == appmod._is_biofield_line(it), it


def test_pay_status_case_and_a_non_utc_paid_at():
    r = br.order_to_request(_order(pay="PAID", paid_at="2026-10-23T09:01:34-10:00"))
    assert r["requested_at"] == "2026-10-23T19:01:34Z"
    assert br.order_to_request(_order(paid_at="2026-10-23T19:01:34"))["requested_at"] == \
        "2026-10-23T19:01:34Z"


def test_two_orders_with_no_address_are_two_people(cx):
    a = br.order_to_request(_order(1, email=""))
    b = br.order_to_request(_order(2, email="", name="Someone Else"))
    assert br.record(cx, a)["kind"] == "added"
    assert br.record(cx, b)["kind"] == "added"


def test_a_clear_paid_order_lifts_a_waiting_line(cx):
    br.record(cx, req("m1", certainty="possible", email="dana@x.com", name="Dana Tamraz"))
    assert br.record(cx, br.order_to_request(_order()))["kind"] == "upgraded"
    [row] = br.list_open(cx)
    assert row["status"] == "requested"


def test_a_name_with_a_newline_stays_on_one_line_in_the_email(cx):
    o = _order(name="Dana\nLine: http://evil")
    br.record(cx, br.order_to_request(o))
    _, body = br.compose_alert(br.unalerted(cx), "http://127.0.0.1:8011")
    assert "\nLine: http://evil" not in body
