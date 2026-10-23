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
])
def test_a_free_scan_or_remedy_match_is_never_clear(text):
    assert br.classify(model("clear"), text)["certainty"] == "possible"


def test_an_explicit_biofield_analysis_stays_clear_beside_a_scan():
    text = "I did the free scan and now I'd like to book a Biofield Analysis."
    assert br.classify(model("clear"), text)["certainty"] == "clear"


@pytest.mark.parametrize("reply", ["", "not json", '{"certainty": "maybe"}', "[1]"])
def test_garbage_or_an_error_is_none(reply):
    assert br.classify(lambda s, u: reply, "Biofield please")["certainty"] == "none"

    def boom(s, u):
        raise RuntimeError("down")
    assert br.classify(boom, "Biofield please")["certainty"] == "none"


def test_the_quote_is_one_line():
    v = br.classify(model("possible", "line one\nline two  " + "x" * 400), "check me again")
    assert "\n" not in v["quote"] and len(v["quote"]) <= 200


# ── Caregiver ────────────────────────────────────────────────────────────────

def test_a_caregiver_request_is_the_members_line_with_the_caregiver_as_payer(cx):
    v = br.classify(model("clear", "Can I book a Biofield Analysis for my son Sam?",
                          for_name="Sam Doe"),
                    "Can I book a Biofield Analysis for my son Sam?", "Jane Doe", "jane@x.com")
    name, email, payer = br.request_for(v, "Jane Doe", "jane@x.com")
    assert (name, email, payer) == ("Sam Doe", "jane@x.com", "jane@x.com")
    v2 = dict(v, for_email="sam@x.com")
    assert br.request_for(v2, "Jane Doe", "jane@x.com") == ("Sam Doe", "sam@x.com", "jane@x.com")
    assert br.request_for({"for_name": ""}, "Jane", "JANE@x.com") == ("Jane", "jane@x.com", "")
    out = br.record(cx, req(name=name, email=email, payer=payer))
    row = br.get(cx, out["id"])
    assert row["name"] == "Sam Doe" and row["payer_email"] == "jane@x.com"


def test_a_members_line_is_not_hidden_by_the_caregivers_own_open_line(cx):
    br.record(cx, req("m1", name="Jane Doe"))
    out = br.record(cx, req("m2", name="Sam Doe", payer="jane@x.com"))
    assert out["kind"] == "added"
    again = br.record(cx, req("m3", name="Sam Doe", payer="jane@x.com"))
    assert again["kind"] == "duplicate"


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
    assert out["kind"] == "duplicate" and "open request" in out["note"]
    assert len(br.list_open(cx)) == 1


def test_one_open_line_per_person_unfinished_test(cx):
    unfinished = [{"test_id": "a7", "email": "jane@x.com", "name": "Jane Doe", "note": ""}]
    out = br.record(cx, req("m1"), unfinished)
    assert out["kind"] == "duplicate" and "a7" in out["note"]
    assert br.list_open(cx) == []


def test_a_finished_test_does_not_block_a_new_line(cx, monkeypatch):
    init_auth_tables(cx)
    create_test(cx, "Jane Doe", "jane@x.com", "2026-09-01")
    from dashboard import biofield_progress as bp
    monkeypatch.setattr(bp, "is_done", lambda p: True)
    assert br.unfinished_tests(cx, lambda ids: ([], None)) == []
    monkeypatch.setattr(bp, "is_done", lambda p: False)
    got = br.unfinished_tests(cx, lambda ids: ([], "down"))
    assert [t["email"] for t in got] == ["jane@x.com"] and "down" in got[0]["note"]


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
    monkeypatch.setattr(ba, "update_header", lambda *a, **k: (_ for _ in ()).throw(
        RuntimeError("disk")))
    with pytest.raises(RuntimeError):
        br.start(cx, a["id"])
    assert br.get(cx, a["id"])["status"] == "requested"


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
    assert f"#req-{d['id']}" not in body and "open request" in body
    assert br.mark_alerted(cx, [a["id"], b["id"], d["id"]]) == 3
    assert br.unalerted(cx) == [] and br.compose_alert([], "x") == (None, None)


# ── Prod: the portal flag and its owner-only GET ────────────────────────────

class _Msg:
    def __init__(self, text):
        self.content = [SimpleNamespace(text=text)]


class _FakeCl:
    def __init__(self, verdict):
        self.verdict, self.systems = verdict, []

    @property
    def messages(self):
        return self

    def create(self, **kw):
        self.systems.append(kw["system"])
        assert kw["model"] == "claude-haiku-4-5-20251001"
        if kw["system"] == br.SYSTEM:
            return _Msg(json.dumps(self.verdict))
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


def test_no_flag_for_an_ordinary_message_and_a_model_error_is_harmless(prod, monkeypatch):
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
        n = cx.execute("SELECT COUNT(*) FROM biofield_request_flags").fetchone()[0] if has else 0
    assert n == 0


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


def test_new_tables_are_listed_for_erasure_and_merge():
    from dashboard import client_erasure as ce, person_merge_discover as pmd
    for t in ("biofield_test_requests", "biofield_request_flags"):
        assert ce.HEALTH_TABLES[t] == "email"
        assert t in pmd.MOVE_TABLES
