"""Zoom refuses a registrant with no last name.

Found by a live probe on 2026-10-15: the Group Coaching Reserve button returned
"Your spot was not reserved" for a one-word name, and a real MasterClass sign-up
the same morning got no Zoom link. Zoom said: HTTP 400 "The parameter is
required: last_name". The registrant call now sends a full stop instead.
"""
import io
import json

from dashboard import zoom


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _capture(calls):
    def _opener(req, timeout=None):
        calls.append(json.loads(req.data.decode()))
        return _Resp(json.dumps({"registrant_id": "r1",
                                 "join_url": "https://zoom.us/w/p1"}).encode())
    return _opener


def _sent(first_name, last_name="", email="maria@example.com"):
    calls = []
    zoom.add_meeting_registrant("tok", meeting_id="123", email=email,
                                first_name=first_name, last_name=last_name,
                                opener=_capture(calls))
    return calls[0]


def test_a_one_word_name_sends_a_full_stop_as_last_name():
    body = _sent("Maria")
    assert body["first_name"] == "Maria"
    assert body["last_name"] == "."


def test_a_blank_or_spaces_last_name_sends_a_full_stop():
    assert _sent("Maria", "   ")["last_name"] == "."
    assert _sent("Maria", None)["last_name"] == "."


def test_a_blank_name_still_sends_both_names():
    body = _sent("", "")
    assert body["first_name"] == "maria"
    assert body["last_name"] == "."


def test_a_real_last_name_is_sent_unchanged():
    assert _sent("Maria", "Ktenas")["last_name"] == "Ktenas"
    assert _sent("Mary", " Ann Lee ")["last_name"] == "Ann Lee"


def test_the_portal_name_split_feeds_a_non_empty_last_name(monkeypatch):
    # The path the Reserve button takes: _zoom_register_person splits the stored
    # name, then calls the registrant API. A one-word stored name must still
    # reach Zoom with a last name.
    import app
    calls = []
    monkeypatch.setattr(zoom, "get_token", lambda *a, **k: "tok")
    real = zoom.add_meeting_registrant
    monkeypatch.setattr(zoom, "add_meeting_registrant",
                        lambda token, **kw: real(token, opener=_capture(calls), **kw))
    for key in ("ZOOM_ACCOUNT_ID", "ZOOM_CLIENT_ID", "ZOOM_CLIENT_SECRET"):
        monkeypatch.setenv(key, "fake")
    app._zoom_register_person("123", "maria@example.com", "Maria")
    assert calls[0]["first_name"] == "Maria"
    assert calls[0]["last_name"] == "."


def test_the_referral_record_keeps_a_blank_last_name():
    # The stand-in belongs to the Zoom call only. Our own split stays blank.
    import app
    assert app._zoom_name_parts("Maria", "maria@example.com") == ("Maria", "")
