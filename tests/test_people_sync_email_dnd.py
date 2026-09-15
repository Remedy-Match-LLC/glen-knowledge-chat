"""sync_people_from_ghl carries GHL's per-channel email DND to the hub.

v1 /contacts/ has only the all-channel `dnd` flag, which read false on all 13 of 25
sampled contacts whose email DND was active. The sync now reads v2 POST
/contacts/search once per run and adds `email_dnd` and `email_dnd_message`. Any v2
failure must leave the payload exactly as it was before this existed.
"""
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import console_push_cron as cron  # noqa: E402
from dashboard import ghl_email  # noqa: E402

EMAIL_SERVICE = "Received Permanent Bounce/Spam/Unsubscribe from Email Service"

V1_CONTACTS = [
    {"id": "c1", "email": "one@example.com", "firstName": "One", "tags": ["email bounced"],
     "dnd": False, "dateUpdated": "2026-09-15T00:00:00Z"},
    {"id": "c2", "email": "two@example.com", "firstName": "Two", "tags": [],
     "dnd": False, "dateUpdated": "2026-09-15T00:00:00Z"},
]

V2_C1 = {"id": "c1", "dndSettings": {"Email": {"status": "active", "message": EMAIL_SERVICE}},
         "searchAfter": [1, "c1"]}
V2_C2 = {"id": "c2", "dndSettings": {}, "searchAfter": [2, "c2"]}

# The payload keys the sync sent before this change.
BEFORE_KEYS = {"email", "first_name", "last_name", "name", "phone", "dob", "city", "state",
               "country", "island", "source", "ghl_id", "tags", "dnd", "organizations",
               "last_contact_date"}


class _Resp:
    def __init__(self, status, payload):
        self.status_code = status
        self._payload = payload

    def json(self):
        return self._payload


@pytest.fixture
def ghl(monkeypatch):
    """Fake v1 list, v2 search and the Render upsert. `v2` is a queue of responses:
    (status, contacts) tuples or exceptions, consumed one per search call."""
    state = {"v2": [], "v2_calls": [], "posted": [],
             "secrets": {"GHL_PIT": "pit-fake", "GHL_LOCATION_ID": "loc-fake"}}
    monkeypatch.setattr(cron, "GHL_API_KEY", "v1-fake")
    monkeypatch.setattr(cron, "_get_secret", lambda name: state["secrets"].get(name, ""))
    monkeypatch.setattr(cron.time, "sleep", lambda s: None)

    def fake_get(url, headers=None, params=None, timeout=None):
        assert url == "https://rest.gohighlevel.com/v1/contacts/"
        return _Resp(200, {"contacts": V1_CONTACTS if params["page"] == 1 else []})

    def fake_post(url, headers=None, json=None, timeout=None):
        if url == "https://services.leadconnectorhq.com/contacts/search":
            state["v2_calls"].append({"headers": headers, "body": dict(json)})
            nxt = state["v2"].pop(0) if state["v2"] else (200, [])
            if isinstance(nxt, Exception):
                raise nxt
            status, contacts = nxt
            return _Resp(status, {"contacts": contacts} if status == 200 else {"message": "no"})
        if url.startswith(cron.RENDER_BASE + "/api/people"):
            state["posted"].extend(json)
            return _Resp(200, {"inserted": len(json), "updated": 0})
        raise AssertionError(f"unexpected POST {url}")

    monkeypatch.setattr(cron.requests, "get", fake_get)
    monkeypatch.setattr(cron.requests, "post", fake_post)
    return state


def _by_id(posted):
    return {p["ghl_id"]: p for p in posted}


def _baseline(ghl):
    """What the sync posts with no v2 read at all."""
    pit = ghl["secrets"].pop("GHL_PIT")
    cron.sync_people_from_ghl()
    out = [dict(p) for p in ghl["posted"]]
    ghl["posted"].clear()
    ghl["v2_calls"].clear()
    ghl["secrets"]["GHL_PIT"] = pit
    return out


def test_the_payload_carries_email_dnd(ghl):
    ghl["v2"] = [(200, [V2_C1, V2_C2])]
    cron.sync_people_from_ghl()

    people = _by_id(ghl["posted"])
    assert people["c1"]["email_dnd"] == "active"
    assert people["c1"]["email_dnd_message"] == EMAIL_SERVICE
    assert "email_dnd" not in people["c2"], "no email DND settings means no new field"
    assert set(people["c2"]) == BEFORE_KEYS

    call = ghl["v2_calls"][0]
    assert call["headers"]["User-Agent"] == ghl_email._UA
    assert "python" not in call["headers"]["User-Agent"].lower()
    assert call["headers"]["Version"] == "2021-07-28"
    assert call["headers"]["Authorization"] == "Bearer pit-fake"
    assert call["body"] == {"locationId": "loc-fake", "pageLimit": 100}


def test_pages_are_followed_with_search_after(ghl, monkeypatch):
    monkeypatch.setattr(cron, "GHL_V2_PAGE", 1)
    ghl["v2"] = [(200, [V2_C2]), (200, [V2_C1]), (200, [])]
    cron.sync_people_from_ghl()
    assert ghl["v2_calls"][1]["body"]["searchAfter"] == [2, "c2"]
    assert _by_id(ghl["posted"])["c1"]["email_dnd"] == "active"


@pytest.mark.parametrize("failure", [(403, []), (500, []), RuntimeError("cloudflare 1010")])
def test_a_failed_v2_read_leaves_the_payload_as_before(ghl, failure):
    before = _baseline(ghl)
    assert all(set(p) == BEFORE_KEYS for p in before)

    ghl["v2"] = [failure]
    cron.sync_people_from_ghl()

    assert len(ghl["v2_calls"]) == 1, "the v2 read was never attempted"
    assert ghl["posted"] == before


def test_a_failure_on_a_later_page_carries_nothing(ghl, monkeypatch):
    before = _baseline(ghl)
    monkeypatch.setattr(cron, "GHL_V2_PAGE", 1)
    ghl["v2"] = [(200, [V2_C1]), (500, [])]
    cron.sync_people_from_ghl()
    assert len(ghl["v2_calls"]) == 2
    assert ghl["posted"] == before, "a partial read must be discarded"


def test_no_token_means_no_v2_call_and_the_old_payload(ghl):
    before = _baseline(ghl)
    assert ghl["v2_calls"] == []
    assert all("email_dnd" not in p for p in before)
