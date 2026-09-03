"""Zoom's per-registrant daily cap must not read as "please retry".

Zoom allows THREE add-registrant calls per day per registrant email:

    429 {"code":429,"message":"You have exceeded the daily rate limit of (3)
         for Add meeting registrant API requests for the registrant (x@y.com).
         You can resume these API requests at GMT 00:00:00."}

A member who fumbles registration three times is locked out until GMT midnight.
The portal told them "Your spot was not reserved. Please retry." -- advice that
can only make it worse, and the log recorded a bare HTTPError with none of
Zoom's explanation, which is what made this take so long to identify.
"""
import io
import json
import urllib.error

import pytest

from dashboard import zoom


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _raise(code, payload):
    def _opener(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, code, "err", {},
                                     io.BytesIO(json.dumps(payload).encode()))
    return _opener


def test_the_daily_cap_is_reported_as_its_own_error():
    payload = {"code": 429, "message": "You have exceeded the daily rate limit of (3) "
                                       "for Add meeting registrant API requests for the "
                                       "registrant (a@x.com). You can resume these API "
                                       "requests at GMT 00:00:00."}
    with pytest.raises(zoom.RegistrantRateLimited) as e:
        zoom.add_meeting_registrant("t", meeting_id="1", email="a@x.com",
                                    first_name="A", opener=_raise(429, payload))
    assert "GMT 00:00:00" in str(e.value), "keep Zoom's own reset time in the message"


def test_zooms_explanation_survives_into_other_errors():
    """A bare HTTPError hid the reason; every failure must carry Zoom's message."""
    payload = {"code": 300, "message": "Meeting is not found or has expired."}
    with pytest.raises(zoom.RegistrantError) as e:
        zoom.add_meeting_registrant("t", meeting_id="1", email="a@x.com",
                                    first_name="A", opener=_raise(400, payload))
    assert "Meeting is not found or has expired." in str(e.value)
    assert not isinstance(e.value, zoom.RegistrantRateLimited)


def test_a_normal_registration_is_unaffected():
    def _ok(req, timeout=None):
        return _Resp(json.dumps({"registrant_id": "R1", "join_url": "https://zoom.us/w/1?tk=x",
                                 "topic": "Group Coaching"}).encode())
    got = zoom.add_meeting_registrant("t", meeting_id="1", email="a@x.com",
                                      first_name="A", opener=_ok)
    assert got["registrant_id"] == "R1"
    assert got["join_url"].startswith("https://zoom.us/")


def test_the_portal_tells_the_member_to_come_back_tomorrow():
    import app as _app
    msg = _app._reserve_failure_message(zoom.RegistrantRateLimited(
        "daily rate limit of (3) ... resume at GMT 00:00:00"))
    assert "retry" not in msg.lower(), "telling them to retry makes it worse"
    assert "tomorrow" in msg.lower() or "GMT" in msg
    other = _app._reserve_failure_message(RuntimeError("something else"))
    assert "retry" in other.lower(), "the ordinary failure keeps its retry advice"
