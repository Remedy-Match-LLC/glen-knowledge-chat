"""The intake by conversation in the portal chat (Glen, 2026-11-14).

No real model is called: app._cl.messages.create is replaced with a fake that returns
the guide's JSON reply.
"""
import importlib
import json
import sqlite3
import types

import pytest

from dashboard import intake as _intake
from dashboard import portal_intake_chat as pic


# ---- the pure rules ------------------------------------------------------------------

@pytest.mark.parametrize("q", [
    "Can we do my intake?", "I would like to do my intake by conversation.",
    "let's continue my intake", "help me with the intake form", "start my health profile"])
def test_wants_intake_on_her_own_words(q):
    assert pic.wants_intake(q)


@pytest.mark.parametrize("q", [
    "What does my scan say about my liver?", "I took my remedies today",
    "stop the intake", "pause my intake please",
    # Questions about the intake, a "later" and a "not" are ordinary chat (rounds 1, 3).
    "how do I submit my intake form?", "Did Dr Glen finish reviewing my intake?",
    "Where do I find my health profile?", "I will do my intake later",
    "Do you have my intake form?", "What do I do after the intake form?",
    "Did you finish my intake?", "I already did my intake", "I don't want to do my intake"])
def test_wants_intake_not_on_other_words(q):
    assert not pic.wants_intake(q)


@pytest.mark.parametrize("q", ["stop", "pause my intake", "let's stop for now", "not now",
                               "finish later", "stop the intake please"])
def test_wants_stop_on_a_short_stop(q):
    assert pic.wants_stop(q)


@pytest.mark.parametrize("q", ["I had to quit smoking before my intake",
                               "I stop eating after 7pm", "my periods ended at 50"])
def test_an_answer_that_says_stop_is_still_an_answer(q):
    assert not pic.wants_stop(q)


def test_should_run_never_on_a_submitted_intake():
    assert not pic.should_run(True, True, "let's do my intake")
    assert pic.should_run(False, True, "I sleep about six hours")
    assert pic.should_run(False, False, "let's do my intake")
    assert not pic.should_run(False, False, "how do I reorder?")


def test_a_stop_inside_the_mode_is_answered_by_the_guide():
    assert pic.should_run(False, True, "pause my intake")
    say, merged, updates, done, stopped = pic.run_turn(
        {"city": "Hilo"}, [], "pause my intake", "", lambda s, m: pytest.fail("no model"))
    assert stopped and not done and updates == {} and merged == {"city": "Hilo"}
    assert say == pic.STOP_LINE


def _reply(say="Thank you.", updates=None, done=False):
    return json.dumps({"say": say, "updates": updates or {}, "done": done})


def test_run_turn_keeps_known_fields_and_drops_consent_and_email():
    seen = {}

    def model(system, messages):
        seen["system"], seen["messages"] = system, messages
        return _reply(updates={"city": "Hilo", "terms": {"agreed": True, "signature": "A"},
                               "email": "evil@x.com", "not_a_field": 1})
    say, merged, updates, done, stopped = pic.run_turn(
        {"first_name": "Alison"}, [], "I live in Hilo", "VOICE", model)
    assert updates == {"city": "Hilo", "email": "evil@x.com", "not_a_field": 1}
    assert merged == {"first_name": "Alison", "city": "Hilo"}
    assert "terms" not in merged and "email" not in merged
    assert seen["system"].startswith("VOICE")
    assert "FORM TO COLLECT" in seen["system"]
    assert seen["messages"][-1] == {"role": "user", "content": "I live in Hilo"}


def test_terms_is_the_consent_field_the_filter_drops():
    # Guards the filter above against the form renaming its consent field.
    assert [f["id"] for f in _intake._fields() if f.get("type") == "consent"] == ["terms"]


def test_run_turn_leaves_the_done_line_to_the_route():
    say, *_rest = pic.run_turn({}, [], "that's all", "", lambda s, m: _reply("Lovely.", done=True))
    assert say == "Lovely." and pic.START_LINE_DONE not in say


def test_messages_start_with_the_client():
    msgs = pic.build_messages([{"role": "assistant", "content": "Aloha"},
                               {"role": "user", "content": "hi"},
                               {"role": "assistant", "content": ""}], "next")
    assert msgs[0]["role"] == "user" and msgs[-1]["content"] == "next"


def _db(tmp_path):
    cx = sqlite3.connect(tmp_path / "t.db")
    cx.row_factory = sqlite3.Row
    _intake.init_intake_table(cx)
    return cx


def test_save_merges_into_the_current_draft(tmp_path):
    cx = _db(tmp_path)
    _intake.save_draft(cx, "a@x.com", {"street": "1 Main", "city": "Old"}, "t0")
    out = pic.save(cx, "A@x.com", {"city": "Hilo", "bogus": 1}, "t1")
    assert out["street"] == "1 Main" and out["city"] == "Hilo"
    stored = _intake.get_response(cx, "a@x.com")["answers"]
    assert stored["city"] == "Hilo" and stored[pic.CHAT_SAVED] == ["city"]


def test_save_never_reopens_an_intake_submitted_after_the_check(tmp_path, monkeypatch):
    cx = _db(tmp_path)
    _intake.save_draft(cx, "a@x.com", {"city": "Old"}, "t0")
    real = _intake.get_response

    def submit_in_between(c, e):
        row = real(c, e)
        _intake.submit(c, "a@x.com", {"city": "Final"}, "t0")   # another process
        return row
    monkeypatch.setattr(_intake, "get_response", submit_in_between)
    assert pic.save(cx, "a@x.com", {"city": "Hilo"}, "t1") is None
    monkeypatch.setattr(_intake, "get_response", real)
    row = _intake.get_response(cx, "a@x.com")
    assert row["status"] == "submitted" and row["answers"]["city"] == "Final"


def test_protect_keeps_chat_answers_a_stale_form_sends_blank():
    stored = {"city": "Hilo", "street": "1 Main", pic.CHAT_SAVED: ["city", "street"]}
    out = pic.protect(stored, {"city": "", "street": "1 Main", "dob": "1950-01-01"})
    assert out["city"] == "Hilo" and out["street"] == "1 Main" and out["dob"] == "1950-01-01"
    assert out[pic.CHAT_SAVED] == ["city"]     # street was shown by the form, it is the form's now


def test_protect_lets_the_form_change_or_later_clear_a_field():
    stored = {"city": "Hilo", pic.CHAT_SAVED: ["city"]}
    assert pic.protect(stored, {"city": "Kona"}) == {"city": "Kona"}
    shown = pic.protect(stored, {"city": "Hilo"})
    assert shown == {"city": "Hilo"}
    assert pic.protect(shown, {"city": ""}) == {"city": ""}


def test_save_learned_lists_only_fields_this_save_changed(tmp_path):
    cx = _db(tmp_path)
    _intake.save_draft(cx, "a@x.com", {"city": "Hilo"}, "t0")
    answers, learned = pic.save_learned(
        cx, "a@x.com", {"city": "Hilo", "street": "1 Main", "email": "b@y.com",
                        "bogus": 1}, "t1")
    assert learned == ["street"]
    assert answers["email"] == "a@x.com"
    assert _intake.get_response(cx, "a@x.com")["answers"][pic.CHAT_SAVED] == ["street"]


def test_lock_row_reports_whether_the_row_exists(tmp_path):
    cx = _db(tmp_path)
    assert _intake.lock_row(cx, "a@x.com") is False
    _intake.save_draft(cx, "a@x.com", {"city": "Hilo"}, "t0")
    assert _intake.lock_row(cx, "A@x.com") is True


L = pic.LOADED


def test_protect_keeps_the_chat_answer_over_a_stale_forms_old_value():
    stored = {"city": "Hilo", pic.CHAT_SAVED: ["city"]}
    out = pic.protect(stored, {"city": "Old", L: {"city": "Old"}})
    assert out["city"] == "Hilo" and out[pic.CHAT_SAVED] == ["city"] and L not in out


def test_protect_takes_her_edit_on_a_stale_form():
    stored = {"city": "Hilo", pic.CHAT_SAVED: ["city"]}
    assert pic.protect(stored, {"city": "Kona", L: {"city": "Old"}}) == {"city": "Kona"}


def test_protect_lets_her_clear_a_field_the_form_has_shown():
    stored = {"city": "Hilo", pic.CHAT_SAVED: ["city"]}
    assert pic.protect(stored, {"city": "", L: {"city": "Hilo"}}) == {"city": ""}
    assert pic.protect(stored, {"city": "Hilo", L: {"city": "Hilo"}}) == {"city": "Hilo"}


def test_protect_never_takes_the_marker_from_the_request():
    out = pic.protect({"city": "Hilo"}, {"city": "", pic.CHAT_SAVED: ["city"], L: {}})
    assert out == {"city": ""}


def test_protect_without_chat_answers_changes_nothing():
    assert pic.protect({"city": "Hilo"}, {"city": ""}) == {"city": ""}


def test_save_refuses_a_submitted_intake(tmp_path):
    cx = _db(tmp_path)
    _intake.submit(cx, "a@x.com", {"city": "Old"}, "t0")
    assert pic.save(cx, "a@x.com", {"city": "Hilo"}, "t1") is None
    assert _intake.get_response(cx, "a@x.com")["answers"]["city"] == "Old"


def test_save_with_nothing_learned_writes_no_row(tmp_path):
    cx = _db(tmp_path)
    assert pic.save(cx, "a@x.com", {}, "t1") == {}
    assert _intake.get_response(cx, "a@x.com") is None


# ---- the route -----------------------------------------------------------------------

@pytest.fixture()
def app_client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    import app
    importlib.reload(app)
    app.app.config["TESTING"] = True
    calls = []

    def fake_create(**kw):
        calls.append(kw)
        text = fake_create.reply
        return types.SimpleNamespace(content=[types.SimpleNamespace(type="text", text=text)])
    fake_create.reply = _reply("Where do you live?", {"city": "Hilo"})
    monkeypatch.setattr(app._cl.messages, "create", fake_create, raising=False)
    app.triaged = []
    monkeypatch.setattr(app, "_triage_portal_message",
                        lambda *a, **k: app.triaged.append(a))
    with app.app.test_client() as client:
        yield app, client, fake_create, calls


def _portal(app_mod, email="alison@x.com", name="Alison Domecq"):
    from dashboard import client_portal
    with sqlite3.connect(app_mod.LOG_DB) as cx:
        client_portal.init_client_portal_table(cx)
        token, _ = client_portal.upsert_portal(cx, email=email, name=name, content={})
    return token


def _events(resp):
    return [json.loads(l[6:]) for l in resp.data.decode().splitlines() if l.startswith("data: ")]


def _stored(app_mod, email="alison@x.com"):
    with sqlite3.connect(app_mod.LOG_DB) as cx:
        cx.row_factory = sqlite3.Row
        _intake.init_intake_table(cx)
        return _intake.get_response(cx, email)


def test_route_asking_for_the_intake_runs_the_guide_and_saves(app_client):
    app_mod, client, fake, calls = app_client
    token = _portal(app_mod)
    r = client.post(f"/api/portal/{token}/chat", json={"query": "I live in Hilo, let's do my intake"})
    ev = _events(r)
    assert {"token": "Where do you live?"} in ev
    assert {"intake": {"mode": True, "done": False, "saved": ["city"]}} in ev
    assert ev[-1] == {"done": True}
    row = _stored(app_mod)
    assert row["status"] == "draft" and row["answers"]["city"] == "Hilo"
    assert row["answers"]["email"] == "alison@x.com"
    assert "Alison" in calls[0]["system"]


def test_route_keeps_going_in_intake_mode(app_client):
    app_mod, client, fake, calls = app_client
    token = _portal(app_mod)
    fake.reply = _reply("Thanks.", {"street": "1 Main"})
    r = client.post(f"/api/portal/{token}/chat",
                    json={"query": "1 Main Street", "intake_mode": True})
    assert {"intake": {"mode": True, "done": False, "saved": ["street"]}} in _events(r)
    assert _stored(app_mod)["answers"]["street"] == "1 Main"


def test_route_ordinary_question_is_not_an_intake_turn(app_client):
    app_mod, client, fake, calls = app_client
    assert app_mod._portal_intake_chat_response(
        "alison@x.com", {"name": "Alison"}, "how do I reorder?", [], False) is None
    assert calls == [] and _stored(app_mod) is None


def test_route_never_runs_on_a_submitted_intake(app_client):
    app_mod, client, fake, calls = app_client
    with sqlite3.connect(app_mod.LOG_DB) as cx:
        cx.row_factory = sqlite3.Row
        _intake.init_intake_table(cx)
        _intake.submit(cx, "alison@x.com", {"city": "Old"}, "t0")
    assert app_mod._portal_intake_chat_response(
        "alison@x.com", {"name": "Alison"}, "1 Main", [], True) is None
    assert calls == [] and _stored(app_mod)["answers"]["city"] == "Old"


def test_route_done_ends_the_mode(app_client):
    app_mod, client, fake, calls = app_client
    token = _portal(app_mod)
    fake.reply = _reply("All done.", {}, done=True)
    ev = _events(client.post(f"/api/portal/{token}/chat",
                             json={"query": "that's everything", "intake_mode": True}))
    assert {"intake": {"mode": False, "done": True, "saved": []}} in ev
    assert any(pic.START_LINE_DONE in (e.get("token") or "") for e in ev)


def test_route_failed_save_keeps_her_in_the_intake(app_client, monkeypatch):
    app_mod, client, fake, calls = app_client
    token = _portal(app_mod)
    fake.reply = _reply("All done.", {"city": "Hilo"}, done=True)
    monkeypatch.setattr(pic, "save_learned", lambda *a: None)
    ev = _events(client.post(f"/api/portal/{token}/chat",
                             json={"query": "Hilo", "intake_mode": True}))
    assert {"intake": {"mode": True, "done": False, "saved": []}} in ev
    text = "".join(e.get("token") or "" for e in ev)
    assert "Please say it again" in text and pic.START_LINE_DONE not in text


def test_route_intake_store_failure_leaves_ordinary_chat(app_client, monkeypatch):
    app_mod, client, fake, calls = app_client
    monkeypatch.setattr(_intake, "is_submitted", lambda *a: (_ for _ in ()).throw(RuntimeError("x")))
    assert app_mod._portal_intake_chat_response(
        "alison@x.com", {"name": "A"}, "1 Main", [], True) is None


def test_form_save_keeps_the_chat_answer_a_stale_form_blanked(app_client):
    app_mod, client, fake, calls = app_client
    token = _portal(app_mod)
    client.post(f"/api/portal/{token}/chat", json={"query": "Hilo. let's do my intake"})
    assert _stored(app_mod)["answers"]["city"] == "Hilo"
    r = client.post(f"/api/intake/save-draft?token={token}",
                    json={"answers": {"city": "", "street": "1 Main"}})
    assert r.status_code == 200
    a = _stored(app_mod)["answers"]
    assert a["city"] == "Hilo" and a["street"] == "1 Main"


def test_route_model_failure_is_a_retryable_error_and_saves_nothing(app_client):
    app_mod, client, fake, calls = app_client
    token = _portal(app_mod)

    def boom(**kw):
        raise RuntimeError("provider down")
    app_mod._cl.messages.create = boom
    ev = _events(client.post(f"/api/portal/{token}/chat",
                             json={"query": "1 Main", "intake_mode": True}))
    assert ev == [{"error": "assistant temporarily unavailable"}]
    assert _stored(app_mod) is None


def test_route_warning_sign_still_reaches_glen(app_client, monkeypatch):
    app_mod, client, fake, calls = app_client
    token = _portal(app_mod)
    from dashboard import portal_triage
    monkeypatch.setattr(portal_triage, "warning_signs", lambda q: ["chest-pain"])
    started = []
    monkeypatch.setattr(app_mod, "_start_warning_sign_delivery",
                        lambda *a, **k: started.append(a) or 7)
    monkeypatch.setattr(portal_triage, "set_answer", lambda *a: None)
    ev = _events(client.post(f"/api/portal/{token}/chat",
                             json={"query": "I have chest pain", "intake_mode": True}))
    assert started and started[0][0] == "alison@x.com"
    assert any(app_mod.PORTAL_WARNING_SIGN_LINE in (e.get("token") or "") for e in ev)


def test_route_intake_turn_is_triaged_like_an_ordinary_one(app_client):
    app_mod, client, fake, calls = app_client
    token = _portal(app_mod)
    _events(client.post(f"/api/portal/{token}/chat",
                        json={"query": "1 Main Street", "intake_mode": True}))
    import time
    for _ in range(50):
        if app_mod.triaged:
            break
        time.sleep(0.02)
    assert app_mod.triaged and app_mod.triaged[0][2] == "1 Main Street"


def test_route_intake_turn_runs_the_healing_crisis_watch(app_client, monkeypatch):
    app_mod, client, fake, calls = app_client
    token = _portal(app_mod)
    seen = []
    monkeypatch.setattr(app_mod, "_healing_crisis_check",
                        lambda e, q: seen.append(q) or (["rash"], {"kind": "healing"}))
    started = []
    monkeypatch.setattr(app_mod, "_start_warning_sign_delivery",
                        lambda *a, **k: started.append(k.get("flag")) or 9)
    monkeypatch.setattr(app_mod, "_start_biofield_request_flag", lambda *a: None)
    from dashboard import portal_triage
    monkeypatch.setattr(portal_triage, "set_answer", lambda *a: None)
    ev = _events(client.post(f"/api/portal/{token}/chat",
                             json={"query": "the rash got worse", "intake_mode": True}))
    assert seen == ["the rash got worse"] and started == [{"kind": "healing"}]
    assert any(app_mod.PORTAL_WARNING_SIGN_LINE in (e.get("token") or "") for e in ev)
    assert app_mod.triaged == []      # a flagged message is not triaged twice


def test_form_save_sends_what_it_loaded_and_the_server_never_stores_it(app_client):
    app_mod, client, fake, calls = app_client
    token = _portal(app_mod)
    client.post(f"/api/portal/{token}/chat", json={"query": "Hilo. let's do my intake"})
    client.post(f"/api/intake/save-draft?token={token}",
                json={"answers": {"city": "Old", "_loaded": {"city": "Old"}}})
    a = _stored(app_mod)["answers"]
    assert a["city"] == "Hilo" and "_loaded" not in a
    page = open("static/client-portal.html", encoding="utf-8").read()
    assert "if(loadedSnapshot) out._loaded = loadedSnapshot;" in page


def test_page_wiring_sends_the_mode_and_handles_the_event():
    page = open("static/client-portal.html", encoding="utf-8").read()
    mentor = open("static/portal-mentor.js", encoding="utf-8").read()
    assert "intake_mode: !!window.__intakeChatMode" in page
    assert "intake_mode:!!window.__intakeChatMode" in mentor
    assert page.count("window.onPortalIntakeChatEvent(evt.intake)") == 1
    assert "window.onPortalIntakeChatEvent(evt.intake)" in mentor
    assert "Answer by conversation instead" in page
