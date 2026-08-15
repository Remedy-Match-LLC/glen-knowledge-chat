import os, pytest
if not os.environ.get("PINECONE_API_KEY"):
    pytest.skip("needs doppler env for import app", allow_module_level=True)
import app as appmod
from dashboard import masterclass as mc

@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setattr(appmod, "LOG_DB", str(tmp_path / "chat_log.db"))
    monkeypatch.setattr(appmod, "CONSOLE_SECRET", "test-secret")
    monkeypatch.setattr(appmod, "send_evox_email", lambda *a, **k: ("console-log", None), raising=False)
    monkeypatch.setattr("dashboard.zoom.get_token", lambda *a, **k: "tok")
    monkeypatch.setattr("dashboard.zoom.create_meeting",
                        lambda *a, **k: {"join_url": "https://zoom.us/j/mc", "meeting_id": "mc1", "start_url": "x"})
    monkeypatch.setattr("dashboard.zoom.add_meeting_registrant",
                        lambda *a, **k: {"registrant_id": "reg-1",
                                        "join_url": "https://zoom.us/w/private-person"})
    appmod.app.config["TESTING"] = True
    return appmod.app.test_client()

ADMIN = {"X-Console-Key": "test-secret"}

def test_console_create_requires_auth(client):
    r = client.post("/api/console/masterclass", json={"topic": "T", "start_ts": "2026-07-10T18:00:00"})
    assert r.status_code == 401

def test_console_create_makes_event_and_zoom(client):
    r = client.post("/api/console/masterclass",
                    json={"topic": "Terrain 101", "description": "d", "start_ts": "2026-07-10T18:00:00",
                          "duration_min": 60, "price_cents": 5000, "member_price_cents": 0}, headers=ADMIN)
    assert r.status_code == 200
    d = r.get_json()
    assert d["ok"] is True and d["zoom_ok"] is True and "/masterclass/" in d["event_url"]
    import sqlite3
    from dashboard import masterclass as mc
    with sqlite3.connect(appmod.LOG_DB) as cx:
        cx.row_factory = sqlite3.Row
        ev = mc.get_event(cx, d["event_id"])
        assert ev["zoom_join_url"] == ""
        assert ev["zoom_meeting_id"] == "mc1"
        assert ev["registration_required"] == 1


def _mk_event(client, price=0, mprice=0):
    r = client.post("/api/console/masterclass",
                    json={"topic": "T", "description": "d", "start_ts": "2026-07-10T18:00:00",
                          "duration_min": 60, "price_cents": price, "member_price_cents": mprice}, headers=ADMIN)
    return r.get_json()["event_id"]


def _seed_ambassador(slug="ambassador-one", email="member@example.com"):
    with appmod.db.connect(appmod.LOG_DB) as cx:
        cx.execute("CREATE TABLE IF NOT EXISTS affiliate_signups ("
                   "id INTEGER PRIMARY KEY AUTOINCREMENT,name TEXT,email TEXT,"
                   "slug TEXT,status TEXT)")
        cx.execute("CREATE TABLE IF NOT EXISTS referral_events ("
                   "id INTEGER PRIMARY KEY AUTOINCREMENT,received_at TEXT,lead_id INTEGER,"
                   "email TEXT,first_name TEXT,last_name TEXT,utm_source TEXT,utm_medium TEXT,"
                   "utm_campaign TEXT,utm_content TEXT,utm_term TEXT,quiz_score TEXT,raw_json TEXT)")
        cx.execute("INSERT INTO affiliate_signups (name,email,slug,status) VALUES (?,?,?,'approved')",
                   ("Morgan Ambassador", email, slug))
        cx.commit()

def test_public_get_event(client):
    eid = _mk_event(client, price=5000)
    d = client.get(f"/api/masterclass/{eid}").get_json()
    assert d["topic"] == "T" and d["price_cents"] == 5000 and "zoom_join_url" not in d

def test_register_free_emails_and_stores_private_link_without_exposing_it(client, monkeypatch):
    eid = _mk_event(client, price=0, mprice=0)
    r = client.post(f"/api/masterclass/{eid}/register", json={"email": "free@x.com", "name": "F"})
    d = r.get_json()
    assert r.status_code == 200 and d["registered"] is True
    assert "join_url" not in d
    assert d["join_status"] == "ready"
    with appmod.db.connect(appmod.LOG_DB) as cx:
        registration = mc.get_registration(cx, eid, "free@x.com")
    assert registration["zoom_join_url"] == "https://zoom.us/w/private-person"


def test_free_registration_links_guest_to_approved_ambassador(client):
    _seed_ambassador()
    eid = _mk_event(client, price=0, mprice=0)
    page = client.get(f"/masterclass/{eid}?ref=ambassador-one")
    assert "rm_ref=ambassador-one" in page.headers.get("Set-Cookie", "")
    event = client.get(f"/api/masterclass/{eid}?ref=ambassador-one").get_json()
    assert event["invited_by"] == "Morgan Ambassador"
    response = client.post(
        f"/api/masterclass/{eid}/register",
        json={"email": "guest@example.com", "name": "Guest Person",
              "ref": "ambassador-one"})
    assert response.status_code == 200
    assert "join_url" not in response.get_json()
    with appmod.db.connect(appmod.LOG_DB) as cx:
        registration = mc.get_registration(cx, eid, "guest@example.com")
        event_referrals = cx.execute(
            "SELECT COUNT(*) FROM referral_events WHERE email=? AND utm_source=? "
            "AND utm_medium='event-invite'",
            ("guest@example.com", "ambassador-one")).fetchone()[0]
    assert registration["referrer_slug"] == "ambassador-one"
    assert event_referrals == 1

    self_event = _mk_event(client, price=0, mprice=0)
    self_response = client.post(
        f"/api/masterclass/{self_event}/register",
        json={"email": "member@example.com", "name": "Morgan Ambassador",
              "ref": "ambassador-one"})
    assert self_response.status_code == 200
    with appmod.db.connect(appmod.LOG_DB) as cx:
        self_registration = mc.get_registration(cx, self_event, "member@example.com")
    assert not self_registration["referrer_slug"]

def test_register_nonmember_paid_returns_checkout(client, monkeypatch):
    monkeypatch.setattr(appmod, "_STRIPE_ACTIVE", True, raising=False)
    import dashboard.stripe_pay as _sp
    cap = {}
    def fake_session(amount_cents, *, customer_email, description, metadata, success_url, cancel_url, save_card=False):
        cap["amount"] = amount_cents; cap["metadata"] = metadata
        return {"id": "cs_test", "url": "https://stripe/mc"}
    monkeypatch.setattr(_sp, "create_checkout_session", fake_session)
    monkeypatch.setattr(appmod.stripe_pay, "create_checkout_session", fake_session, raising=False)
    eid = _mk_event(client, price=5000, mprice=0)
    r = client.post(f"/api/masterclass/{eid}/register", json={"email": "nonmember@x.com", "name": "N"})
    d = r.get_json()
    assert r.status_code == 200 and d["checkout_url"] == "https://stripe/mc"
    assert cap["amount"] == 5000 and cap["metadata"]["kind"] == "masterclass" and cap["metadata"]["event_id"] == str(eid)

def test_fulfill_masterclass_marks_paid_and_sends(client, monkeypatch):
    sent = []
    monkeypatch.setattr(appmod, "send_evox_email", lambda to, *a, **k: sent.append(to) or ("console-log", None), raising=False)
    eid = _mk_event(client, price=5000, mprice=0)
    import sqlite3
    from dashboard import masterclass as mc
    with sqlite3.connect(appmod.LOG_DB) as cx:
        mc.register(cx, eid, "buyer@x.com", "B", is_member=False, amount_cents=5000, paid=False)
        cx.commit()
    import dashboard.stripe_pay as _sp
    monkeypatch.setattr(_sp, "get_session",
                        lambda sid: {"metadata": {"kind": "masterclass", "event_id": str(eid), "email": "buyer@x.com", "name": "B"},
                                     "payment_intent": "pi_1"})
    monkeypatch.setattr(_sp, "get_payment_intent", lambda pi: {"status": "succeeded"})
    out = appmod._fulfill_masterclass("cs_test")
    assert out["ok"] is True
    with sqlite3.connect(appmod.LOG_DB) as cx:
        assert mc.is_registered(cx, eid, "buyer@x.com") is True
    assert "buyer@x.com" in sent
    # non-masterclass session is a no-op
    monkeypatch.setattr(_sp, "get_session", lambda sid: {"metadata": {"kind": "retail"}})
    assert appmod._fulfill_masterclass("cs_other")["ok"] is False


def test_masterclass_page_served(client):
    r = client.get("/masterclass/1")
    assert r.status_code == 200 and b"MasterClass" in r.data
