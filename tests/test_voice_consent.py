"""The Five Element Voice Analysis opt-in. Glen, 2026-10-27, sales tab: the analysis runs
only after the client ticks the box, unticking deletes what was kept, and a pattern kept
before the box existed stays hidden. Brief: sales/plans/2026-10-27-five-element-voice-opt-in-brief.md

Two sentences under the recorder are promises, and each has a test here:
  "we never use them to identify you"  -> test_kept_pattern_holds_only_the_five_numbers
  "we delete what we kept"             -> test_untick_deletes_the_kept_pattern_*
"""
import importlib
import io
import json
import sqlite3
import sys
from pathlib import Path

import pytest

EMAIL = "voice@example.com"
SCORES = {"Wood": 20, "Fire": 10, "Earth": 40, "Metal": 20, "Water": 10}


def _app(monkeypatch, tmp_db):
    repo_root = Path(__file__).resolve().parent.parent
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
    try:
        app = importlib.import_module("app")
    except Exception as exc:
        pytest.skip(f"app module not importable: {exc}")
    monkeypatch.setattr(app, "LOG_DB", str(tmp_db))
    app._init_workspace_schema()
    return app


def _seed_portal(tmp_db, email=EMAIL):
    from dashboard import client_portal
    with sqlite3.connect(str(tmp_db)) as cx:
        client_portal.init_client_portal_table(cx)
        token, _ = client_portal.upsert_portal(cx, email, "Voice Member",
                                               {"biofield_status": "none"})
    return token


def _fake_analysis(monkeypatch, calls=None):
    import journal_blueprint
    from dashboard import tcm_analysis

    def whisper(path):
        if calls is not None:
            calls.append(path)
        return {"text": "I have felt heavy lately and I am ready to move.",
                "words": [{"word": "I", "start": 0.0, "end": 0.2}]}

    monkeypatch.setattr(journal_blueprint, "_whisper_transcribe", whisper)
    monkeypatch.setattr(tcm_analysis, "_haiku_analyze", lambda t, lex: {
        "elements": dict(SCORES), "emotions": {}, "top_themes": []})


def _record(app, token):
    return app.app.test_client().post(
        f"/api/portal/{token}/five-element-voice",
        data={"duration_seconds": "30", "audio": (io.BytesIO(b"webm"), "voice.webm")},
        content_type="multipart/form-data")


def _consent(app, token, agree):
    return app.app.test_client().post(
        f"/api/portal/{token}/five-element-voice/consent", json={"agree": agree})


def _row(tmp_db, email=EMAIL):
    from dashboard import member_element_state
    with sqlite3.connect(str(tmp_db)) as cx:
        return member_element_state.get(cx, email)


def _has_consent(tmp_db, email=EMAIL):
    from dashboard import voice_consent
    with sqlite3.connect(str(tmp_db)) as cx:
        return voice_consent.has_consent(cx, email)


# ---- the server refuses without the tick --------------------------------------------

def test_recording_without_the_tick_is_refused_before_any_audio_is_heard(monkeypatch, tmp_db):
    app = _app(monkeypatch, tmp_db)
    token = _seed_portal(tmp_db)
    calls = []
    _fake_analysis(monkeypatch, calls)
    r = _record(app, token)
    assert r.status_code == 403
    assert calls == []
    assert _row(tmp_db) is None


def test_recording_after_the_tick_runs_and_keeps_the_pattern(monkeypatch, tmp_db):
    app = _app(monkeypatch, tmp_db)
    token = _seed_portal(tmp_db)
    _fake_analysis(monkeypatch)
    assert _consent(app, token, True).status_code == 200
    assert _has_consent(tmp_db)
    r = _record(app, token)
    assert r.status_code == 200
    assert _row(tmp_db)["source"] == "portal_voice"


def test_untick_during_analysis_keeps_nothing(monkeypatch, tmp_db):
    """The client unticks while the recording is being analysed: the late result is
    not saved."""
    app = _app(monkeypatch, tmp_db)
    token = _seed_portal(tmp_db)
    _fake_analysis(monkeypatch)
    _consent(app, token, True)
    from dashboard import tcm_analysis, voice_consent

    def analyse_then_untick(t, lex):
        with sqlite3.connect(str(tmp_db)) as cx:
            voice_consent.withdraw(cx, EMAIL)
        return {"elements": dict(SCORES), "emotions": {}, "top_themes": []}

    monkeypatch.setattr(tcm_analysis, "_haiku_analyze", analyse_then_untick)
    assert _record(app, token).status_code == 403
    assert _row(tmp_db) is None


# ---- "we delete what we kept" --------------------------------------------------------

def test_untick_deletes_the_kept_pattern_and_the_opt_in(monkeypatch, tmp_db):
    app = _app(monkeypatch, tmp_db)
    token = _seed_portal(tmp_db)
    _fake_analysis(monkeypatch)
    _consent(app, token, True)
    _record(app, token)
    r = _consent(app, token, False)
    assert r.status_code == 200
    assert r.get_json()["patterns_deleted"] == 1
    assert _row(tmp_db) is None
    assert not _has_consent(tmp_db)
    with sqlite3.connect(str(tmp_db)) as cx:
        assert cx.execute("SELECT COUNT(*) FROM voice_analysis_consent").fetchone()[0] == 0


def test_untick_deletes_the_kept_pattern_but_keeps_a_backdrop_choice(monkeypatch, tmp_db):
    app = _app(monkeypatch, tmp_db)
    token = _seed_portal(tmp_db)
    _fake_analysis(monkeypatch)
    _consent(app, token, True)
    _record(app, token)
    from dashboard import member_element_state
    with sqlite3.connect(str(tmp_db)) as cx:
        member_element_state.set_override(cx, EMAIL, "water")
    _consent(app, token, False)
    row = _row(tmp_db)
    assert row["scene_override"] == "water"
    assert row["element_scores"] == {}
    assert row["dominant_element"] is None and row["deficient_element"] is None
    assert row["source"] is None


def test_untick_leaves_a_chat_pattern_alone(monkeypatch, tmp_db):
    """Glen, 2026-10-27: "no for now" to covering the portal chat."""
    app = _app(monkeypatch, tmp_db)
    token = _seed_portal(tmp_db)
    from dashboard import member_element_state
    with sqlite3.connect(str(tmp_db)) as cx:
        member_element_state.upsert(cx, EMAIL, SCORES, source="portal_chat")
    _consent(app, token, True)
    assert _consent(app, token, False).get_json()["patterns_deleted"] == 0
    assert _row(tmp_db)["element_scores"]["Earth"] == 40


def test_untick_touches_only_this_client(monkeypatch, tmp_db):
    app = _app(monkeypatch, tmp_db)
    token = _seed_portal(tmp_db)
    from dashboard import member_element_state, voice_consent
    with sqlite3.connect(str(tmp_db)) as cx:
        member_element_state.upsert(cx, "other@example.com", SCORES, source="portal_voice")
        voice_consent.agree(cx, "other@example.com")
    _consent(app, token, False)
    assert _row(tmp_db, "other@example.com")["element_scores"]["Earth"] == 40
    assert _has_consent(tmp_db, "other@example.com")


# ---- "we never use them to identify you" ---------------------------------------------

def test_kept_pattern_holds_only_the_five_numbers(monkeypatch, tmp_db):
    """What is kept from a recording is the five element scores and the two element
    names derived from them. No transcript, no audio, no voice measurement that could
    match a speaker is stored anywhere in the row or the opt-in.

    This proves what is STORED, which is the part a test can prove. Nothing here can
    match a speaker because nothing kept describes the voice itself."""
    app = _app(monkeypatch, tmp_db)
    token = _seed_portal(tmp_db)
    _fake_analysis(monkeypatch)
    _consent(app, token, True)
    assert _record(app, token).status_code == 200
    with sqlite3.connect(str(tmp_db)) as cx:
        cx.row_factory = sqlite3.Row
        row = dict(cx.execute("SELECT * FROM member_element_state WHERE email=?",
                              (EMAIL,)).fetchone())
        consent = dict(cx.execute("SELECT * FROM voice_analysis_consent WHERE email=?",
                                  (EMAIL,)).fetchone())
    assert set(json.loads(row["element_scores"])) == set(SCORES)
    assert all(isinstance(v, (int, float)) for v in json.loads(row["element_scores"]).values())
    dump = json.dumps(row) + json.dumps(consent)
    assert "heavy" not in dump and "webm" not in dump
    assert set(consent) == {"email", "agreed_at"}


# ---- hidden until the tick ------------------------------------------------------------

def test_a_voice_pattern_without_the_tick_is_hidden_from_the_portal(tmp_db):
    from dashboard import member_element_state, portal_element_view, voice_consent
    with sqlite3.connect(str(tmp_db)) as cx:
        member_element_state.upsert(cx, EMAIL, SCORES, source="portal_voice")
        view = portal_element_view.element_view(cx, EMAIL)
        assert view["element_scores"] == {}
        assert view["deficient_element"] is None and view["setting"] is None
        assert view["dominant_element"] is None and view["source"] is None
        voice_consent.agree(cx, EMAIL)
        view = portal_element_view.element_view(cx, EMAIL)
        assert view["element_scores"]["Earth"] == 40
        assert view["setting"] is not None


def test_a_chat_pattern_shows_without_the_tick(tmp_db):
    from dashboard import member_element_state, portal_element_view
    with sqlite3.connect(str(tmp_db)) as cx:
        member_element_state.upsert(cx, EMAIL, SCORES, source="portal_chat")
        assert portal_element_view.element_view(cx, EMAIL)["element_scores"]["Earth"] == 40


def test_hidden_pattern_keeps_the_backdrop_choice(tmp_db):
    from dashboard import member_element_state, portal_element_view
    with sqlite3.connect(str(tmp_db)) as cx:
        member_element_state.upsert(cx, EMAIL, SCORES, source="portal_voice")
        member_element_state.set_override(cx, EMAIL, "fire")
        view = portal_element_view.element_view(cx, EMAIL)
    assert view["override"] == "fire" and view["element_scores"] == {}


# ---- only the client chooses -----------------------------------------------------------

@pytest.mark.parametrize("how", ["header", "query"])
def test_a_console_opened_portal_cannot_tick_for_the_client(monkeypatch, tmp_db, how):
    """The real owner check, with the real console key: nothing stubbed."""
    app = _app(monkeypatch, tmp_db)
    token = _seed_portal(tmp_db)
    key = app.CONSOLE_SECRET
    assert key, "the test needs CONSOLE_SECRET set, as CI sets it"
    client = app.app.test_client()
    url = f"/api/portal/{token}/five-element-voice/consent"
    if how == "header":
        r = client.post(url, json={"agree": True}, headers={"X-Console-Key": key})
    else:
        r = client.post(url + "?key=" + key, json={"agree": True})
    assert r.status_code == 403
    assert not _has_consent(tmp_db)
    # The same request without the key is the client's own, and is accepted.
    assert client.post(url, json={"agree": True}).status_code == 200


def test_untick_then_tick_again_during_analysis_keeps_nothing(monkeypatch, tmp_db):
    """The old recording belongs to the withdrawn opt-in, not to the new one."""
    app = _app(monkeypatch, tmp_db)
    token = _seed_portal(tmp_db)
    _fake_analysis(monkeypatch)
    _consent(app, token, True)
    from dashboard import tcm_analysis, voice_consent

    def analyse_untick_retick(t, lex):
        with sqlite3.connect(str(tmp_db)) as cx:
            voice_consent.withdraw(cx, EMAIL)
            cx.execute("INSERT INTO voice_analysis_consent (email, agreed_at) VALUES (?, ?)",
                       (EMAIL, "2099-01-01T00:00:00+00:00"))
            cx.commit()
        return {"elements": dict(SCORES), "emotions": {}, "top_themes": []}

    monkeypatch.setattr(tcm_analysis, "_haiku_analyze", analyse_untick_retick)
    assert _record(app, token).status_code == 403
    assert _row(tmp_db) is None


def test_the_save_locks_the_opt_in_row_on_postgres():
    """On Postgres the save holds the opt-in row FOR UPDATE until it commits, so an
    untick from another worker waits for it. SQLite has one writer and no FOR UPDATE."""
    from dashboard import voice_consent
    seen = []

    class _Cur:
        def __init__(self, row):
            self._row = row

        def fetchone(self):
            return self._row

    class _PgLike:
        backend = "postgres"

        def execute(self, sql, args=()):
            seen.append(sql)
            return _Cur(None)

        def rollback(self):
            seen.append("ROLLBACK")

    assert voice_consent.save_if_still_agreed(_PgLike(), EMAIL, "t1", SCORES) is None
    assert seen[0].rstrip().endswith("FOR UPDATE")
    assert seen[-1] == "ROLLBACK"


def test_withdraw_deletes_the_opt_in_row_before_the_pattern(tmp_db):
    """The order matters on Postgres: deleting the opt-in row first is what waits on a
    save that holds it."""
    from dashboard import voice_consent
    seen = []
    with sqlite3.connect(str(tmp_db)) as cx:
        voice_consent.init_table(cx)
        cx.set_trace_callback(seen.append)
        voice_consent.withdraw(cx, EMAIL)
    deletes = [q for q in seen if q.lstrip().upper().startswith(("DELETE", "UPDATE"))]
    assert "voice_analysis_consent" in deletes[0]


def test_the_page_can_read_back_the_choice(monkeypatch, tmp_db):
    app = _app(monkeypatch, tmp_db)
    token = _seed_portal(tmp_db)
    client = app.app.test_client()
    url = f"/api/portal/{token}/five-element-voice/consent"
    assert client.get(url).get_json()["agreed"] is False
    _consent(app, token, True)
    assert client.get(url).get_json()["agreed"] is True
    assert client.get("/api/portal/not-a-token/five-element-voice/consent").status_code == 404


def test_a_lost_response_is_settled_by_asking_the_server():
    html, _ = _card()
    fn = html[html.index("function initFiveVoiceConsent"):][:2600]
    catch = fn[fn.index("}catch(error){"):]
    assert "five-element-voice/consent`, {credentials" in catch
    assert "box.checked = saved;" in catch


@pytest.mark.parametrize("body", [{}, {"agree": "yes"}, {"agree": 1}, {"agree": None}])
def test_consent_needs_a_plain_true_or_false(monkeypatch, tmp_db, body):
    app = _app(monkeypatch, tmp_db)
    token = _seed_portal(tmp_db)
    r = app.app.test_client().post(f"/api/portal/{token}/five-element-voice/consent", json=body)
    assert r.status_code == 400
    assert not _has_consent(tmp_db)


def test_consent_with_a_bad_token_is_not_found(monkeypatch, tmp_db):
    app = _app(monkeypatch, tmp_db)
    assert _consent(app, "not-a-token", True).status_code == 404


def test_portal_payload_carries_the_consent(monkeypatch, tmp_db):
    app = _app(monkeypatch, tmp_db)
    assert app._five_voice_consent_for(EMAIL) is False
    from dashboard import voice_consent
    with sqlite3.connect(str(tmp_db)) as cx:
        voice_consent.agree(cx, EMAIL)
    assert app._five_voice_consent_for(EMAIL) is True
    monkeypatch.setattr(app.db, "connect", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down")))
    assert app._five_voice_consent_for(EMAIL) is False


# ---- erasure ---------------------------------------------------------------------------

def test_client_erasure_covers_the_opt_in():
    from dashboard import client_erasure
    assert client_erasure.HEALTH_TABLES.get("voice_analysis_consent") == "email"
    assert client_erasure.HEALTH_TABLES.get("member_element_state") == "email"


# ---- the page ----------------------------------------------------------------------------

def _card():
    html = (Path(__file__).resolve().parent.parent / "static" / "client-portal.html").read_text()
    return html, html[html.index('id="fiveElementVoiceCard"'):][:4000]


def test_card_shows_glens_approved_wording_and_tick_box():
    _, card = _card()
    for line in (
        "Your audio is deleted as soon as it is analysed.",
        "When you tick the box below, we keep your five element pattern and a few "
        "measurements of how your voice sounds, such as pitch and loudness.",
        "Your portal uses them to show how your voice changes over time.",
        "These numbers cannot be played back as your voice, and we never use them to identify you.",
        "You can untick the box at any time, and we delete what we kept.",
        "This reflective wellness analysis is not a medical diagnosis.",
        "I agree to the Five Element Voice Analysis and to keeping my results for future comparison.",
    ):
        assert line in card, line
    assert "deleted after transcription" not in card
    assert card.index("not a medical diagnosis") < card.index('id="fiveVoiceConsent"')
    assert card.index('id="fiveVoiceConsent"') < card.index('id="fiveVoiceRecord"')


def test_recorder_starts_disabled_and_follows_the_tick():
    html, card = _card()
    assert 'id="fiveVoiceRecord" disabled' in card
    assert "initFiveVoiceConsent(!!d.five_voice_consent)" in html
    consent_fn = html[html.index("function initFiveVoiceConsent"):][:3200]
    assert "if(start) start.disabled = !agreed;" in consent_fn
    assert "if(start) start.disabled = !box.checked;" in consent_fn
    assert "if(d.five_voice_consent && d.element_state" in html


def test_portal_api_tells_the_page_whether_the_client_agreed(monkeypatch, tmp_db):
    app = _app(monkeypatch, tmp_db)
    token = _seed_portal(tmp_db)
    client = app.app.test_client()
    r = client.get(f"/api/portal/{token}")
    assert r.status_code == 200
    assert r.get_json()["five_voice_consent"] is False
    _consent(app, token, True)
    assert client.get(f"/api/portal/{token}").get_json()["five_voice_consent"] is True


def test_record_again_stays_off_after_an_untick():
    html, _ = _card()
    reset = html[html.index("function fiveVoiceResetControls"):][:600]
    assert "start.disabled = !agreed" in reset


def test_a_staff_view_cookie_cannot_tick_or_record_for_the_client(monkeypatch, tmp_db):
    """A portal opened from a console staff link, or by a VA: _staff_touch() is the
    real check, only the cookie reader beneath it is stubbed."""
    app = _app(monkeypatch, tmp_db)
    token = _seed_portal(tmp_db)
    _fake_analysis(monkeypatch)
    from dashboard import voice_consent
    with sqlite3.connect(str(tmp_db)) as cx:
        voice_consent.agree(cx, EMAIL)
    monkeypatch.setattr(app, "_staff_view_cookie_ok", lambda: True)
    assert _consent(app, token, False).status_code == 403
    assert _has_consent(tmp_db)
    assert _record(app, token).status_code == 403
    assert _row(tmp_db) is None


def test_a_lapsed_staff_view_cannot_record(monkeypatch, tmp_db):
    app = _app(monkeypatch, tmp_db)
    token = _seed_portal(tmp_db)
    _fake_analysis(monkeypatch)
    _consent(app, token, True)
    monkeypatch.setattr(app, "_staff_lapsed", lambda: True)
    assert _record(app, token).status_code == 403
    assert _row(tmp_db) is None


def test_the_console_key_cannot_record_for_the_client(monkeypatch, tmp_db):
    app = _app(monkeypatch, tmp_db)
    token = _seed_portal(tmp_db)
    _fake_analysis(monkeypatch)
    _consent(app, token, True)
    r = app.app.test_client().post(
        f"/api/portal/{token}/five-element-voice",
        data={"duration_seconds": "30", "audio": (io.BytesIO(b"webm"), "voice.webm")},
        content_type="multipart/form-data", headers={"X-Console-Key": app.CONSOLE_SECRET})
    assert r.status_code == 403
    assert _row(tmp_db) is None


def test_a_lapsed_staff_view_cannot_tick_or_untick(monkeypatch, tmp_db):
    app = _app(monkeypatch, tmp_db)
    token = _seed_portal(tmp_db)
    monkeypatch.setattr(app, "_staff_lapsed", lambda: True)
    assert _consent(app, token, True).status_code == 403
    assert not _has_consent(tmp_db)
    from dashboard import voice_consent
    with sqlite3.connect(str(tmp_db)) as cx:
        voice_consent.agree(cx, EMAIL)
    assert _consent(app, token, False).status_code == 403
    assert _has_consent(tmp_db)


def test_a_portal_with_no_email_is_not_found(monkeypatch, tmp_db):
    app = _app(monkeypatch, tmp_db)
    token = _seed_portal(tmp_db)
    monkeypatch.setattr(app, "_portal_record_for", lambda cx, t: {"email": "", "name": "x"})
    assert _consent(app, token, True).status_code == 404


def test_the_opt_in_ignores_letter_case(tmp_db):
    from dashboard import voice_consent
    with sqlite3.connect(str(tmp_db)) as cx:
        voice_consent.agree(cx, "Voice@Example.COM ")
        assert voice_consent.has_consent(cx, "voice@example.com")
        assert voice_consent.agreed_at(cx, "VOICE@example.com")


def test_ticking_again_keeps_the_first_opt_in_time(tmp_db):
    """A second tab ticking again must not refuse a recording already being analysed."""
    from dashboard import voice_consent
    with sqlite3.connect(str(tmp_db)) as cx:
        voice_consent.agree(cx, EMAIL)
        first = voice_consent.agreed_at(cx, EMAIL)
        voice_consent.agree(cx, EMAIL)
        assert voice_consent.agreed_at(cx, EMAIL) == first


def test_the_page_script_details():
    html, _ = _card()
    fn = html[html.index("function initFiveVoiceConsent"):][:3200]
    assert "box.checked = agreed;" in fn
    assert "box.disabled = true;" in fn            # no second click while one is in flight
    body = fn[fn.index("try{"):fn.index("}catch(error){")]
    assert "if(!want){" in body and 'host.innerHTML = ""' in body
    catch = fn[fn.index("}catch(error){"):]
    assert "let saved = !want;" in catch
    assert "saved = !!(await check.json()).agreed;" in catch
