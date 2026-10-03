# EVOX in the client portal: the home checklist item, and the setup gate on the
# Private Appointments routes. Imports app, so it needs the fake env keys.
import os, sqlite3, pytest
from pathlib import Path
from urllib.parse import urlparse, parse_qs
if not os.environ.get("PINECONE_API_KEY"):
    pytest.skip("needs doppler env for import app", allow_module_level=True)
import app as appmod
from dashboard import evox as _ev, appointment_proposals as _ap
from dashboard import portal_onboarding as _ob


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setattr(appmod, "LOG_DB", str(tmp_path / "chat_log.db"))
    monkeypatch.setattr(appmod, "send_evox_email", lambda *a, **k: ("console-log", None))
    links = []
    monkeypatch.setattr(appmod, "send_evox_setup_link",
                        lambda to, name, url: links.append(url) or ("console-log", None))
    sent = []
    monkeypatch.setattr(appmod, "_notify_staff_of_appointment_proposal",
                        lambda p: sent.append(p))
    monkeypatch.setattr(appmod, "_is_paid_member", lambda email: True)
    appmod.app.config["TESTING"] = True
    c = appmod.app.test_client()
    c.links, c.sent = links, sent
    return c


def _token(client, email="c@x.com"):
    assert client.post("/api/evox/start", json={"email": email, "name": "C"}).get_json()["ok"]
    return parse_qs(urlparse(client.links[-1]).query)["token"][0]


def _ready(email="c@x.com"):
    with sqlite3.connect(appmod.LOG_DB) as cx:
        for item in _ev.READINESS_ITEMS:
            _ev.set_readiness_item(cx, email, item, True)


def _propose(client, token, kind):
    return client.post(f"/api/portal/{token}/appointment-proposals",
                       json={"session_type": kind, "proposed_start": "2099-01-05T10:00",
                             "timezone": "Pacific/Honolulu"})


def test_evox_proposal_refused_until_setup_complete(client):
    token = _token(client)
    r = _propose(client, token, "evox")
    assert r.status_code == 403
    assert r.get_json()["error"] == "evox_not_ready"
    assert r.get_json()["setup_url"] == f"/evox?token={token}"
    assert client.sent == []
    _ready()
    assert _propose(client, token, "evox").status_code == 201
    assert len(client.sent) == 1


def test_biofield_proposal_is_not_gated_by_evox_setup(client):
    token = _token(client)
    assert _propose(client, token, "biofield-consult").status_code == 201


def test_client_cannot_confirm_staff_evox_time_before_setup(client):
    token = _token(client)
    with sqlite3.connect(appmod.LOG_DB) as cx:
        pid = _ap.create(cx, email="c@x.com", session_type="evox",
                         start="2099-01-05T10:00:00", proposed_by="rae")
    url = f"/api/portal/{token}/appointment-proposals/{pid}/confirm"
    r = client.post(url)
    assert r.status_code == 403 and r.get_json()["error"] == "evox_not_ready"
    with sqlite3.connect(appmod.LOG_DB) as cx:
        assert _ap.get(cx, pid)["client_confirmed"] == 0


def test_get_reports_evox_readiness(client):
    token = _token(client)
    d = client.get(f"/api/portal/{token}/appointment-proposals").get_json()
    assert d["evox_readiness"]["complete"] is False
    _ready()
    d = client.get(f"/api/portal/{token}/appointment-proposals").get_json()
    assert d["evox_readiness"]["complete"] is True


def test_onboarding_evox_item_links_to_setup_with_token(client, monkeypatch):
    monkeypatch.setattr(appmod, "_PORTAL_ONBOARDING_ENABLED", True)
    token = _token(client)
    d = client.get(f"/api/portal/{token}/onboarding").get_json()
    heal = {s["key"]: s for s in d["status"]["phases"][2]["steps"]}
    assert heal["evox"]["label"] == "EVOX session with Rae"
    assert heal["evox"]["href"] == f"/evox?token={token}"
    assert heal["evox"]["done"] is False
    assert not heal["evox"].get("checkable")


def test_evox_item_ticks_on_setup_complete():
    cx = sqlite3.connect(":memory:")
    assert _ob.evox_done(cx, "c@x.com") is False
    for item in _ev.READINESS_ITEMS[:-1]:
        _ev.set_readiness_item(cx, "c@x.com", item, True)
    assert _ob.evox_done(cx, "c@x.com") is False
    _ev.set_readiness_item(cx, "c@x.com", _ev.READINESS_ITEMS[-1], True)
    assert _ob.evox_done(cx, "c@x.com") is True


def test_evox_item_ticks_on_booked_session_only():
    cx = sqlite3.connect(":memory:")
    _ev.init_evox_tables(cx)
    cx.execute("INSERT INTO evox_bookings (email,start_ts,end_ts,status) "
               "VALUES ('c@x.com','2099-01-05T10:00','2099-01-05T11:00','cancelled')")
    assert _ob.evox_done(cx, "C@x.com") is False
    cx.execute("INSERT INTO evox_bookings (email,start_ts,end_ts,status,session_type) "
               "VALUES ('c@x.com','2099-01-06T10:00','2099-01-06T11:00','booked','consult')")
    assert _ob.evox_done(cx, "C@x.com") is False
    cx.execute("INSERT INTO evox_bookings (email,start_ts,end_ts,status) "
               "VALUES ('c@x.com','2099-01-07T10:00','2099-01-07T11:00','booked')")
    assert _ob.evox_done(cx, "C@x.com") is True


def test_portal_card_shows_setup_instead_of_time_form():
    html = (Path(__file__).parents[1] / "static" / "client-portal.html").read_text()
    assert "Finish EVOX setup" in html
    assert "kind==='evox'&&!evoxReady()" in html
    assert "time.hidden=blocked" in html
    assert "p.session_type==='evox'&&!evoxReady()" in html
