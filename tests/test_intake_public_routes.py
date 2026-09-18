import importlib, sqlite3, types, pytest


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    import app as appmod
    importlib.reload(appmod)
    from dashboard import intake, intake_public as ip

    # never send real email; capture what the portal-link sender was handed
    sent = {}
    def fake_send(email, name, url):
        sent["email"], sent["name"], sent["url"] = email, name, url
        return ("test", None)
    monkeypatch.setattr(appmod, "send_evox_setup_link", fake_send)
    appmod.app.config["TESTING"] = True

    with sqlite3.connect(appmod.LOG_DB) as cx:
        intake.init_intake_table(cx); ip.init_intake_sessions_table(cx)
        cx.execute("DELETE FROM intake_responses"); cx.execute("DELETE FROM intake_sessions")
        cx.commit()
    c = appmod.app.test_client()
    c._appmod, c._sent = appmod, sent
    return c


def _start(c, name="Pat Doe", email="pat@x.com", tos=True, company=""):
    return c.post("/api/intake/public/start",
                  json={"name": name, "email": email, "tos_agreed": tos, "company": company})


def test_start_requires_email_and_tos(client):
    assert client.post("/api/intake/public/start", json={"name": "X", "tos_agreed": True}).status_code == 400
    assert _start(client, tos=False).status_code == 400


def test_start_honeypot_silently_ok_but_no_session(client):
    r = _start(client, company="bot-filled")
    assert r.status_code == 200 and "token" not in r.get_json()


def test_start_returns_scoped_token_and_emails_portal_link_not_token(client):
    r = _start(client)
    body = r.get_json()
    assert r.status_code == 200 and body["ok"] and body["token"]
    # the emailed setup URL carries the MASTER portal token; it must NOT equal the
    # scoped token handed to the browser
    assert client._sent["email"] == "pat@x.com"
    assert "token=" in client._sent["url"]
    assert body["token"] not in client._sent["url"]


def test_form_prefill_with_token(client):
    tok = _start(client).get_json()["token"]
    r = client.get(f"/api/intake/public/form?token={tok}")
    body = r.get_json()
    assert body["form"]["version"]
    assert body["answers"]["email"] == "pat@x.com"
    assert body["answers"]["first_name"] == "Pat"
    assert body["submitted"] is False


def test_save_submit_require_valid_token(client):
    assert client.post("/api/intake/public/save-draft?token=nope", json={"answers": {}}).status_code == 401
    assert client.post("/api/intake/public/submit?token=nope", json={"answers": {}}).status_code == 401


def _valid(email="pat@x.com"):
    return {"first_name": "Pat", "last_name": "Doe", "email": email, "dob": "1970-01-01",
            "terrain": 5, "penetration": 5, "tissue_layer": 2, "response": 2, "commitment": 10,
            "terms": {"agreed": True, "signature": "Pat Doe", "date": "2026-07-20"}}


def test_submit_success_then_double_submit_409(client):
    tok = _start(client).get_json()["token"]
    assert client.post(f"/api/intake/public/submit?token={tok}", json={"answers": _valid()}).status_code == 200
    r2 = client.post(f"/api/intake/public/submit?token={tok}", json={"answers": _valid()})
    assert r2.status_code == 409 and r2.get_json()["error"] == "already_submitted"


def test_submit_validation_lists_missing(client):
    tok = _start(client).get_json()["token"]
    r = client.post(f"/api/intake/public/submit?token={tok}", json={"answers": {}})
    assert r.status_code == 400 and "terrain" in r.get_json()["errors"]


def test_submit_keyed_by_token_email_ignores_body_email(client):
    tok = _start(client, email="owner@x.com").get_json()["token"]
    client.post(f"/api/intake/public/submit?token={tok}",
                json={"answers": _valid(email="victim@y.com")})
    from dashboard import intake
    with sqlite3.connect(client._appmod.LOG_DB) as cx:
        cx.row_factory = sqlite3.Row
        assert intake.is_submitted(cx, "owner@x.com")
        assert not intake.is_submitted(cx, "victim@y.com")


def test_chat_turn_fills_form_and_persists(client, monkeypatch):
    tok = _start(client).get_json()["token"]
    fake = types.SimpleNamespace(content=[types.SimpleNamespace(
        type="text", text='{"say":"Got it.","updates":{"terrain":5,"sleep":"Yes"},"done":false}')])
    monkeypatch.setattr(client._appmod._cl.messages, "create", lambda **k: fake)
    r = client.post(f"/api/intake/public/chat?token={tok}",
                    json={"messages": [{"role": "user", "content": "I have stress and poor sleep"}]})
    body = r.get_json()
    assert body["say"] == "Got it."
    assert body["answers"]["terrain"] == 5 and body["answers"]["sleep"] == "Yes"
    # persisted to the draft
    r2 = client.get(f"/api/intake/public/form?token={tok}")
    assert r2.get_json()["answers"]["terrain"] == 5


def test_chat_requires_token(client):
    assert client.post("/api/intake/public/chat?token=bad", json={"messages": []}).status_code == 401


# --- the confirmed disclosure: a submitted intake reachable by email alone ------------
#
# Confirmed against production on 2026-09-18, on Glen's own record and with his
# permission: with no credential, start minted a token and form returned 36 stored
# fields. Knowing the email was the whole of the "auth". Fixed at both the mint (start
# gives no reading token for a submitted email) and the read (form never serves a
# submitted record's answers). Two locks, one door.

def _submit(c, email, answers):
    """Put a genuinely submitted intake in the store for `email`, as a client would."""
    from dashboard import intake, intake_public as ip
    with sqlite3.connect(c._appmod.LOG_DB) as cx:
        ip.public_submit(cx, email, answers, "2026-09-18T00:00:00")
        assert intake.is_submitted(cx, email)


def test_start_mints_no_token_for_an_already_submitted_email(client):
    _submit(client, "victim@x.com", {"diagnoses": "MS", "medications": "tysabri"})
    r = _start(client, email="victim@x.com")
    body = r.get_json()
    assert r.status_code == 200
    assert not body.get("token"), "a reading token was minted for a submitted intake"
    assert body.get("existing") is True, "the owner is not pointed at their emailed link"


def test_the_owner_still_gets_the_portal_link_emailed(client):
    """Closing the funnel path must not strand the real owner. The master credential
    still goes to their inbox, which is the legitimate way back in."""
    _submit(client, "victim@x.com", {"diagnoses": "MS"})
    client._sent.clear()
    _start(client, email="victim@x.com")
    assert client._sent.get("email") == "victim@x.com"
    assert "token=" in (client._sent.get("url") or "")


def test_form_never_returns_a_submitted_records_answers(client):
    """Belt-and-braces. Even if a token resolves to a submitted email, the clinical
    record is not served. This is the assertion that would have caught the live bug."""
    from dashboard import intake_public as ip
    _submit(client, "victim@x.com", {"diagnoses": "MS", "medications": "tysabri"})
    # Forge exactly what the exploit had: a session bound to the victim's email.
    with sqlite3.connect(client._appmod.LOG_DB) as cx:
        from datetime import datetime
        tok = ip.create_session(cx, "victim@x.com", "attacker", datetime(2026, 9, 18))
    d = client.get("/api/intake/public/form?token=" + tok).get_json()
    assert d.get("submitted") is True
    assert d.get("answers") == {}, "the submitted clinical record was disclosed"


def test_a_new_email_still_gets_a_working_funnel(client):
    """The fix must not break the ordinary case: a brand-new person fills the intake."""
    r = _start(client, email="new@x.com")
    body = r.get_json()
    assert body.get("token"), "a genuine new intake got no session"
    assert not body.get("existing")
    f = client.get("/api/intake/public/form?token=" + body["token"]).get_json()
    assert f.get("submitted") is False
    assert "form" in f


def test_form_still_prefills_for_a_non_submitted_email(client):
    """The exact contrast the fix draws. A submitted email gets {}; a non-submitted one
    gets its draft answers, so the funnel prefill still works. start writes the name into
    the draft, so that is what comes back."""
    r = _start(client, name="Sam Rae", email="draft@x.com")
    tok = r.get_json().get("token")
    assert tok
    f = client.get("/api/intake/public/form?token=" + tok).get_json()
    assert f.get("submitted") is False
    assert f.get("answers", {}).get("first_name") == "Sam", (
        "a non-submitted intake returned no answers, so prefill is broken"
    )
