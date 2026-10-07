"""Text-message consent (Glen approved the wording 2026-10-06; communication's build request).

The opt-in box is optional and unticked, a ticked box records consent with time and source,
STOP and START write the record, only a request Twilio signed is acted on, and no reply is
sent unless SMS_AUTO_REPLIES is "on".
"""
import base64
import hashlib
import hmac
import json
import re
import sqlite3
from html.parser import HTMLParser
from pathlib import Path

import pytest

from dashboard import sms_consent as sc

ROOT = Path(__file__).resolve().parent.parent
TOKEN = "test-twilio-token"
URL = "https://illtowell.com/sms/inbound"


def _sign(params, url=URL, token=TOKEN):
    # Written from Twilio's published rule, not from sms_consent, so it can catch a bug there.
    payload = url + "".join(k + v for k, v in sorted(params.items()))
    return base64.b64encode(hmac.new(token.encode(), payload.encode(), hashlib.sha1).digest()).decode()


@pytest.fixture
def client(monkeypatch, tmp_path):
    import app as appmod
    monkeypatch.setattr(appmod, "LOG_DB", str(tmp_path / "chat_log.db"))
    appmod._init_auth_tables()
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", TOKEN)
    monkeypatch.delenv("SMS_AUTO_REPLIES", raising=False)
    appmod.app.config["TESTING"] = True
    cx = sqlite3.connect(appmod.LOG_DB)
    cx.execute("CREATE TABLE IF NOT EXISTS people (id INTEGER PRIMARY KEY, email TEXT, phone TEXT, "
               "tags TEXT, updated_at TEXT)")
    cx.commit()
    cx.close()
    return appmod.app.test_client(), appmod


def _text(c, body, frm="+18085550123", sign=True):
    data = {"From": frm, "Body": body, "To": "+18087462212"}
    headers = {"X-Twilio-Signature": _sign(data)} if sign else {}
    return c.post("/sms/inbound", data=data, headers=headers, base_url="https://illtowell.com")


def _db(appmod):
    return sqlite3.connect(appmod.LOG_DB)


# ── the signature ────────────────────────────────────────────────────────────

def test_twilio_published_example_signature():
    p = [("CallSid", "CA1234567890ABCDE"), ("Caller", "+12349013030"), ("Digits", "1234"),
         ("From", "+12349013030"), ("To", "+18005551212")]
    assert sc.twilio_signature_ok("12345", "https://mycompany.com/myapp.php?foo=1&bar=2", p,
                                  "0/KCTR6DLpKmkAf8muzZqo1nDgQ=")


def test_unsigned_stop_is_refused_and_records_nothing(client):
    c, appmod = client
    assert _text(c, "STOP", sign=False).status_code == 403
    assert sc.status(_db(appmod), "+18085550123") is None


def test_wrongly_signed_stop_is_refused(client):
    c, appmod = client
    data = {"From": "+18085550123", "Body": "STOP"}
    r = c.post("/sms/inbound", data=data, headers={"X-Twilio-Signature": _sign(data, token="other")},
               base_url="https://illtowell.com")
    assert r.status_code == 403
    assert sc.status(_db(appmod), "+18085550123") is None


def test_no_token_means_nothing_is_acted_on(client, monkeypatch):
    c, appmod = client
    monkeypatch.delenv("TWILIO_AUTH_TOKEN")
    assert _text(c, "STOP").status_code == 403
    assert sc.status(_db(appmod), "+18085550123") is None


def test_no_token_refuses_a_request_signed_with_an_empty_key(client, monkeypatch):
    """Without the token, a forger could sign with an empty key. That must still be refused."""
    c, appmod = client
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "")
    data = {"From": "+18085550123", "Body": "STOP"}
    r = c.post("/sms/inbound", data=data, headers={"X-Twilio-Signature": _sign(data, token="")},
               base_url="https://illtowell.com")
    assert r.status_code == 403
    assert sc.status(_db(appmod), "+18085550123") is None


def test_signature_over_http_behind_the_proxy_still_matches(client):
    """Render hands the app http://; Twilio signed the https:// URL it was given."""
    c, appmod = client
    data = {"From": "+18085550123", "Body": "STOP"}
    r = c.post("/sms/inbound", data=data, headers={"X-Twilio-Signature": _sign(data)},
               base_url="http://illtowell.com")
    assert r.status_code == 204
    assert sc.status(_db(appmod), "+18085550123") == "out"


# ── STOP, START, HELP ────────────────────────────────────────────────────────

def test_stop_then_start_writes_the_record_and_the_people_tag(client):
    c, appmod = client
    cx = _db(appmod)
    cx.execute("INSERT INTO people (id, email, phone, tags) VALUES (1, 'a@x.com', '(808) 555-0123', "
               "'[\"consent:opted-in\"]')")
    cx.commit()
    assert _text(c, " stop ").status_code == 204
    assert sc.status(_db(appmod), "8085550123") == "out"
    tags = json.loads(_db(appmod).execute("SELECT tags FROM people WHERE id=1").fetchone()[0])
    assert tags == ["consent:opted-in", "consent:sms-unsubscribed"]
    assert "consent:unsubscribed" not in tags          # a text STOP never touches email
    assert _text(c, "START").status_code == 204
    assert sc.status(_db(appmod), "8085550123") == "in"
    tags = json.loads(_db(appmod).execute("SELECT tags FROM people WHERE id=1").fetchone()[0])
    assert tags == ["consent:opted-in"]
    rows = _db(appmod).execute("SELECT action, source FROM sms_consent_events ORDER BY created_at").fetchall()
    assert rows == [("out", "sms:STOP"), ("in", "sms:START")]


def test_stop_from_an_unknown_number_is_still_recorded(client):
    c, appmod = client
    _text(c, "STOP", frm="+18085559999")
    assert sc.status(_db(appmod), "+18085559999") == "out"


def test_stop_sets_portal_notify_out_but_start_never_undoes_an_email_unsubscribe(client):
    c, appmod = client
    from dashboard import notify_state as N
    cx = _db(appmod); N.set_phone(cx, "t@y.com", "+15551230000"); N.set_opt(cx, "t@y.com", "out")
    cx.commit()
    _text(c, "START", frm="+15551230000")
    assert N.get_state(_db(appmod), "t@y.com")["opt_status"] == "out"
    assert sc.status(_db(appmod), "+15551230000") == "in"


def test_a_repeated_twilio_message_is_recorded_once(client):
    c, appmod = client
    def send(body, sid):
        data = {"From": "+18085550123", "Body": body, "MessageSid": sid}
        return c.post("/sms/inbound", data=data, headers={"X-Twilio-Signature": _sign(data)},
                      base_url="https://illtowell.com")
    send("START", "SM1"); send("STOP", "SM2"); send("START", "SM1")      # SM1 retried or replayed
    assert sc.status(_db(appmod), "+18085550123") == "out"
    assert _db(appmod).execute("SELECT COUNT(*) FROM sms_consent_events").fetchone()[0] == 2


def test_a_form_tick_cannot_reverse_a_stop(tmp_path):
    cx = sqlite3.connect(tmp_path / "x.db")
    sc.record(cx, "+18085550123", "out", "sms:STOP")
    assert sc.record(cx, "808-555-0123", "in", "form:practitioner-finder-inquiry") is False
    assert sc.status(cx, "8085550123") == "out"
    assert sc.record(cx, "+18085550123", "in", "sms:START") is True
    assert sc.status(cx, "8085550123") == "in"


def test_a_form_tick_after_a_form_tick_is_fine(tmp_path):
    cx = sqlite3.connect(tmp_path / "x.db")
    assert sc.record(cx, "808-555-0123", "in", "form:a") is True
    assert sc.record(cx, "808-555-0123", "in", "form:b") is True


def test_two_countries_with_the_same_last_ten_digits_stay_apart(tmp_path):
    cx = sqlite3.connect(tmp_path / "x.db")
    sc.record(cx, "+44 8085550123", "in", "form:a")
    assert sc.status(cx, "+1 808 555 0123") is None
    assert sc.status(cx, "(808) 555-0123") is None
    assert sc.status(cx, "+448085550123") == "in"


def test_a_phone_with_trailing_text_still_gets_the_tag(tmp_path):
    cx = sqlite3.connect(tmp_path / "x.db")
    cx.execute("CREATE TABLE people (id INTEGER PRIMARY KEY, email TEXT, phone TEXT, tags TEXT, "
               "updated_at TEXT)")
    cx.execute("INSERT INTO people VALUES (1, 'a@x.com', '808-555-0123 (cell)', '[]', NULL)")
    sc.record(cx, "+18085550123", "out", "sms:STOP")
    assert json.loads(cx.execute("SELECT tags FROM people").fetchone()[0]) == [sc.SMS_OPT_OUT_TAG]


def test_a_stop_never_tags_a_different_number_sharing_the_last_four(tmp_path):
    cx = sqlite3.connect(tmp_path / "x.db")
    cx.execute("CREATE TABLE people (id INTEGER PRIMARY KEY, email TEXT, phone TEXT, tags TEXT, "
               "updated_at TEXT)")
    cx.execute("INSERT INTO people VALUES (1, 'a@x.com', '808-555-0123', '[]', NULL)")
    cx.execute("INSERT INTO people VALUES (2, 'b@x.com', '415-777-0123', '[]', NULL)")
    sc.record(cx, "+18085550123", "out", "sms:STOP")
    tags = dict(cx.execute("SELECT id, tags FROM people").fetchall())
    assert json.loads(tags[1]) == [sc.SMS_OPT_OUT_TAG] and json.loads(tags[2]) == []


def test_a_failed_tag_update_keeps_the_consent_event(tmp_path):
    cx = sqlite3.connect(tmp_path / "x.db")                 # no people table at all
    assert sc.record(cx, "+18085550123", "out", "sms:STOP") is True
    assert sc.status(cx, "+18085550123") == "out"


def test_sms_inbound_is_open_during_maintenance():
    import app as appmod
    assert any("/sms/inbound".startswith(p) for p in appmod._MAINTENANCE_EXEMPT_PREFIXES)


def test_help_and_chatter_record_nothing(client):
    c, appmod = client
    assert _text(c, "HELP").status_code == 204
    assert _text(c, "stop texting me so much please").status_code == 204
    assert sc.status(_db(appmod), "+18085550123") is None


@pytest.mark.parametrize("body,reply", [("STOP", sc.REPLY_STOP), ("START", sc.REPLY_OPT_IN),
                                        ("HELP", sc.REPLY_HELP)])
def test_no_reply_is_sent_unless_switched_on(client, monkeypatch, body, reply):
    c, _ = client
    r = _text(c, body)
    assert r.status_code == 204 and r.get_data() == b""
    monkeypatch.setenv("SMS_AUTO_REPLIES", "on")
    r = _text(c, body)
    assert r.status_code == 200 and r.mimetype == "text/xml"
    assert "<Message>" + reply + "</Message>" in r.get_data(as_text=True)


def test_replies_are_the_approved_wording_and_never_name_the_land_line():
    assert sc.REPLY_OPT_IN == ("Remedy Match: you will now get texts about your orders, appointments "
                               "and account. Message frequency varies. Msg and data rates may apply. "
                               "Reply HELP for help, STOP to stop.")
    assert sc.REPLY_STOP == "Remedy Match: you will get no more texts from us. Reply START to receive them again."
    assert sc.REPLY_HELP == ("Remedy Match: for help, reply to this text or email "
                             "support@remedymatch.com. Reply STOP to stop.")
    for r in (sc.REPLY_OPT_IN, sc.REPLY_STOP, sc.REPLY_HELP):
        assert "217" not in re.sub(r"\D", "", r)


# ── the record itself ────────────────────────────────────────────────────────

def test_a_number_with_no_event_may_not_be_texted(tmp_path):
    cx = sqlite3.connect(tmp_path / "x.db")
    assert sc.may_text(cx, "+18085550123") is False
    sc.record(cx, "808-555-0123", "in", "form:test", email="A@X.com")
    assert sc.may_text(cx, "+1 (808) 555-0123") is True
    sc.record(cx, "+18085550123", "out", "sms:STOP")
    assert sc.may_text(cx, "8085550123") is False
    email, src, ts = cx.execute("SELECT email, source, created_at FROM sms_consent_events "
                                "WHERE action='in'").fetchone()
    assert (email, src) == ("a@x.com", "form:test") and ts.endswith("Z")


def test_a_short_number_is_not_recorded(tmp_path):
    cx = sqlite3.connect(tmp_path / "x.db")
    assert sc.record(cx, "555-0123", "in", "form:test") is False


# ── the forms ────────────────────────────────────────────────────────────────

def _register_stubs(appmod, monkeypatch):
    monkeypatch.setattr(appmod._pp, "register_practitioner", lambda clean: (1, False))
    monkeypatch.setattr(appmod, "ghl_upsert_contact", lambda *a, **k: ("c1", True, None))
    monkeypatch.setattr(appmod._pp, "create_magic_link_token", lambda *a, **k: "t")
    monkeypatch.setattr(appmod, "_send_practitioner_magic_link", lambda *a, **k: None)
    monkeypatch.setattr(appmod, "_send_full_report_email", lambda *a, **k: ("stub", None))


_REG = {"name": "Pat Lee", "email": "pat@x.com", "phone": "808-555-0123", "portal_role": "licensed",
        "license_number": "L-1", "practice_name": "P"}


@pytest.mark.parametrize("value,want", [(True, "in"), (False, None), ("true", None), (None, None)])
def test_registration_records_only_a_ticked_box(client, monkeypatch, value, want):
    c, appmod = client
    _register_stubs(appmod, monkeypatch)
    body = dict(_REG)
    if value is not None:
        body["sms_consent"] = value
    r = c.post("/api/practitioner/register", json=body)
    assert r.status_code == 201, r.get_data(as_text=True)
    assert sc.status(_db(appmod), "8085550123") == want
    if want:
        assert _db(appmod).execute("SELECT source FROM sms_consent_events").fetchone()[0] == \
            "form:practitioner-register"


def test_a_failed_consent_write_does_not_fail_the_form(client, monkeypatch):
    c, appmod = client
    _register_stubs(appmod, monkeypatch)
    def boom(*a, **k):
        raise RuntimeError("db down")
    monkeypatch.setattr(sc, "record", boom)
    r = c.post("/api/practitioner/register", json=dict(_REG, sms_consent=True))
    assert r.status_code == 201


class _Inputs(HTMLParser):
    def __init__(self):
        super().__init__()
        self.inputs = []

    def handle_starttag(self, tag, attrs):
        if tag == "input":
            self.inputs.append(dict(attrs))


APPROVED = ("Text me about my orders, appointments and account. Message frequency varies. Message and "
            "data rates may apply. Reply STOP to stop or HELP for help. See our ")


@pytest.mark.parametrize("page,box_id,payload", [
    ("practitioner-register.html", "sms_consent", "sms_consent: $('sms_consent').checked"),
    ("wholesale-apply.html", "sms_consent", 'sms_consent: $("sms_consent").checked'),
    ("practitioner.html", "f_sms_consent", "sms_consent:    form.sms_consent.checked"),
])
def test_each_form_has_an_unticked_optional_box_that_is_sent(page, box_id, payload):
    src = (ROOT / "static" / page).read_text()
    p = _Inputs(); p.feed(src)
    box = [i for i in p.inputs if i.get("id") == box_id]
    assert len(box) == 1 and box[0].get("type") == "checkbox"
    assert "checked" not in box[0] and "required" not in box[0]
    assert APPROVED in src
    assert payload in src


def test_finder_inquiry_box_is_unticked_optional_and_sent():
    src = (ROOT / "static" / "practitioner-finder.html").read_text()
    line = next(l for l in src.splitlines() if "smsWrap.innerHTML" in l)
    html = json.loads(line.split("=", 1)[1].strip().rstrip(";"))
    p = _Inputs(); p.feed(html)
    assert len(p.inputs) == 1 and p.inputs[0].get("type") == "checkbox"
    assert "checked" not in p.inputs[0] and "required" not in p.inputs[0]
    assert APPROVED in html
    assert "sms_consent: smsInput.checked" in src


def test_privacy_page_carries_the_text_messages_section(client):
    c, _ = client
    body = c.get("/privacy").get_data(as_text=True)
    assert "<h2>8. Text messages</h2>" in body
    for s in ("We never send promotional texts under this consent.",
              "Your mobile number and your consent to receive texts are never shared with third "
              "parties or affiliates for their marketing.",
              "Agreeing to texts is never a condition of buying anything from us."):
        assert s in body
    nums = [int(n) for n in re.findall(r"<h2>(\d+)\. ", body)]
    assert nums == list(range(1, 20))


def test_every_phone_form_route_records_its_own_source():
    """Each route that takes a phone from a public form passes its box to the recorder."""
    import ast
    tree = ast.parse((ROOT / "app.py").read_text())
    want = {"api_practitioner_register": "form:practitioner-register",
            "api_wholesale_apply": "form:wholesale-apply",
            "practitioner_application": "form:practitioner-application",
            "practitioner_finder_inquiry": "form:practitioner-finder-inquiry"}
    found = {}
    for fn in ast.walk(tree):
        if isinstance(fn, ast.FunctionDef) and fn.name in want:
            for call in ast.walk(fn):
                if (isinstance(call, ast.Call) and getattr(call.func, "id", "") == "_record_sms_opt_in"
                        and isinstance(call.args[-1], ast.Constant)):
                    found[fn.name] = call.args[-1].value
    assert found == want


@pytest.mark.parametrize("body,phone,want", [
    ({"sms_consent": True}, "808-555-0123", "in"),
    ({"sms_consent": True}, "", None),
    ({"sms_consent": 1}, "808-555-0123", None),
    ({}, "808-555-0123", None),
    (None, "808-555-0123", None),
])
def test_the_recorder_takes_only_a_real_tick_with_a_phone(client, body, phone, want):
    _, appmod = client
    appmod._record_sms_opt_in(body, phone, "a@x.com", "form:test")
    assert sc.status(_db(appmod), "8085550123") == want


def test_a_text_stop_does_not_drop_someone_from_an_email_workflow(client):
    _, appmod = client
    cx = _db(appmod)
    cx.execute("INSERT INTO people (id, email, phone, tags) VALUES (1, 'a@x.com', '', ?)",
               (json.dumps(["type:client", "consent:opted-in", "consent:sms-unsubscribed"]),))
    cx.execute("INSERT INTO people (id, email, phone, tags) VALUES (2, 'b@x.com', '', ?)",
               (json.dumps(["type:client", "consent:opted-in", "consent:unsubscribed"]),))
    cx.commit()
    out = appmod.enroll_segment_in_workflow("wf-test-1234", dry_run=True)
    assert out["matched"] == 1          # the text opt-out stays in; the email one stays out
