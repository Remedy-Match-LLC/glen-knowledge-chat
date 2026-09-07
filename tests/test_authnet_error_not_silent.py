"""Authorize.net answers HTTP 200 for a rejected credential.

The failure lives in `messages.resultCode`, and the response then carries no
`batchList`. Reading `batchList` off that response yields an empty list, so a
dead API key used to report $0.00 and stamp `last_success` as if the fetch had
worked. That hid an E00007 break from 2026-05-31 to 2026-09-07.
"""

import pytest

from dashboard import money


ERROR_BODY = {
    "messages": {
        "resultCode": "Error",
        "message": [
            {"code": "E00007",
             "text": "User authentication failed due to invalid authentication values."}
        ],
    }
}

OK_EMPTY_BODY = {
    "messages": {"resultCode": "Ok", "message": [{"code": "I00001", "text": "Successful."}]},
    "batchList": [],
}


def test_rejected_credential_raises_instead_of_reading_an_empty_batch_list():
    with pytest.raises(RuntimeError) as e:
        money._an_raise_on_error(ERROR_BODY)
    assert "E00007" in str(e.value)


def test_a_genuine_empty_week_does_not_raise():
    money._an_raise_on_error(OK_EMPTY_BODY)


def test_an_post_checks_result_code_before_returning(monkeypatch):
    class FakeResp:
        text = '﻿{"messages":{"resultCode":"Error","message":[{"code":"E00007","text":"nope"}]}}'

        def raise_for_status(self):
            return None

    monkeypatch.setattr(money.requests, "post", lambda *a, **k: FakeResp())
    with pytest.raises(RuntimeError):
        money.an_post({"getSettledBatchListRequest": {}})


def test_an_data_propagates_the_rejection(monkeypatch):
    """A rejection must not surface as a $0.00 week."""
    from dashboard import cache

    cache.clear("money.an")
    monkeypatch.setattr(money, "an_post", lambda payload: money._an_raise_on_error(ERROR_BODY))
    with pytest.raises(RuntimeError):
        money.an_data(days=30)
    assert cache.last_success("money.an") is None
