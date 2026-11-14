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
    "stop the intake", "pause my intake please"])
def test_wants_intake_not_on_other_words(q):
    assert not pic.wants_intake(q)


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


def test_done_tells_her_to_tick_the_consent_herself():
    say, *_rest = pic.run_turn({}, [], "that's all", "", lambda s, m: _reply("Lovely.", done=True))
    assert say.startswith("Lovely.") and pic.START_LINE_DONE in say


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
    out = pic.save(cx, "A@x.com", {"city": "Hilo"}, "t1")
    assert out["street"] == "1 Main" and out["city"] == "Hilo"
    assert _intake.get_response(cx, "a@x.com")["answers"]["city"] == "Hilo"


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


def test_page_wiring_sends_the_mode_and_handles_the_event():
    page = open("static/client-portal.html", encoding="utf-8").read()
    mentor = open("static/portal-mentor.js", encoding="utf-8").read()
    assert "intake_mode: !!window.__intakeChatMode" in page
    assert "intake_mode:!!window.__intakeChatMode" in mentor
    assert page.count("window.onPortalIntakeChatEvent(evt.intake)") == 1
    assert "window.onPortalIntakeChatEvent(evt.intake)" in mentor
    assert "Answer by conversation instead" in page
