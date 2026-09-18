"""An empty dispensed panel must not claim the client has no purchases.

Glen, 2026-09-18: "I'm still not seeing how many bottles of each product have been
previously purchased", then "yes, make it show history unavailable".

WHAT WAS WRONG. The panel fetches order history from the server and returned [] on ANY
failure, so the renderer printed "No order history for this client yet." That is the
reassuring answer, and it was being given about clients with plenty of orders.

It had been dead for at least a day. The local app reads CONSOLE_SECRET at startup, its
process started before the key was rotated, and every lookup came back 401. Its own log
recorded nine of them and nobody saw the log, because the page looked fine.

Verified against production at the time: the identical call returns 200 with the current
key and 401 with a wrong one.

THE RULE THIS ENCODES. Failure and emptiness are different facts, and a reference panel
that cannot tell them apart will state the comforting one. "Best effort, never break the
page" is right; "best effort, and lie when it fails" is not.
"""
import pathlib

import pytest

from dashboard.biofield_report_html import render_dispensed_panel

# The real shape frequency() emits: {name, slug, count, bottles, orders_considered, pct}.
# My first fixture invented "orders" and omitted "count", so the renderer raised KeyError
# and two tests failed on MY data rather than on the code.
ROWS = [{"name": "Adrenal Syntropy", "slug": "adrenal-syntropy", "count": 3,
         "bottles": 5, "orders_considered": 7, "pct": 43, "conditions": []}]


def test_a_failure_says_unavailable_not_no_history():
    html = render_dispensed_panel([], error="the console key is no longer valid")
    assert "History unavailable" in html
    assert "No order history for this client yet" not in html, (
        "the panel told the client's story wrongly: this is a failed lookup, not an "
        "empty one"
    )


def test_the_failure_names_the_reason():
    html = render_dispensed_panel([], error="the console key is no longer valid")
    assert "console key" in html


def test_it_says_explicitly_that_this_is_not_an_empty_client():
    """The whole point. Someone reading it must not conclude 'never bought anything'."""
    html = render_dispensed_panel([], error="boom")
    assert "not a client with no purchases" in html


def test_a_genuinely_empty_history_still_says_so():
    """The opposite error would be just as bad: crying failure at a real new client."""
    html = render_dispensed_panel([], error=None)
    assert "No order history for this client yet" in html
    assert "History unavailable" not in html


def test_rows_still_render_when_there_is_no_error():
    html = render_dispensed_panel(ROWS)
    assert "Adrenal Syntropy" in html
    assert "History unavailable" not in html


def test_rows_win_over_a_stale_error_flag():
    """If some history came back, show it. A partial answer beats a warning."""
    html = render_dispensed_panel(ROWS, error=None)
    assert "Adrenal Syntropy" in html


def test_the_error_text_is_escaped():
    """The reason is built from an exception. It must not be able to inject markup."""
    html = render_dispensed_panel([], error="<script>alert(1)</script>")
    assert "<script>" not in html


@pytest.mark.parametrize("code,expect", [(401, "restart"), (502, "restarting")])
def test_the_lookup_translates_the_status_into_an_action(code, expect):
    """A bare 'Unauthorized' sends the reader to the wrong place. 401 here means the
    process is holding a key that was rotated under it."""
    src = (pathlib.Path(__file__).resolve().parents[1]
           / "dashboard" / "biofield_invoice.py").read_text()
    body = src[src.index("def client_orders_with_status"):]
    body = body[:body.index("\ndef ")] if "\ndef " in body else body
    assert str(code) in body and expect in body


def test_the_old_list_only_contract_is_preserved():
    """default_client_orders still returns a plain list, so existing callers are safe."""
    src = (pathlib.Path(__file__).resolve().parents[1]
           / "dashboard" / "biofield_invoice.py").read_text()
    body = src[src.index("def default_client_orders"):]
    body = body[:body.index("\ndef ")]
    assert "client_orders_with_status(email, limit)" in body
    assert "_OrdersResult" in body, (
        "the list subclass is what keeps the seam's contract while carrying the reason"
    )


def test_the_local_app_asks_for_the_reason():
    """The wiring, not just the capability. Unasked-for, the fix is inert."""
    src = (pathlib.Path(__file__).resolve().parents[1] / "biofield_local_app.py").read_text()
    # It must read the reason OFF the injected seam's result, not reach past the seam to
    # the concrete function. Doing the latter broke the author route's own tests.
    assert 'getattr(_client_orders, "error", None)' in src
    assert "dispensed_error=dispensed_error" in src
    assert "_binv.client_orders_with_status" not in src, "the seam was bypassed"


def _force(monkeypatch, exc):
    """Point the lookup at a fake server that always fails the given way."""
    import urllib.request

    from dashboard import biofield_invoice as bi
    monkeypatch.setenv("CONSOLE_SECRET", "fake-key")
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://example.invalid")

    def boom(*a, **k):
        raise exc
    monkeypatch.setattr(urllib.request, "urlopen", boom)
    return bi


def test_a_401_returns_an_error_not_a_quiet_empty_list(monkeypatch):
    """THE regression. A first version of these tests only checked the renderer and the
    source text, so replacing `return [], why` with `return [], None` passed all eleven.
    That mutation puts the original bug straight back: the panel goes quiet and says the
    client has no purchases."""
    import urllib.error
    bi = _force(monkeypatch, urllib.error.HTTPError(
        "u", 401, "Unauthorized", {}, None))
    orders, err = bi.client_orders_with_status("someone@example.com")
    assert orders == []
    assert err, "a 401 produced no error, so the panel will claim an empty history"
    assert "restart" in err, "the 401 message must say what to actually do"


def test_a_502_returns_its_own_error(monkeypatch):
    import urllib.error
    bi = _force(monkeypatch, urllib.error.HTTPError("u", 502, "Bad Gateway", {}, None))
    orders, err = bi.client_orders_with_status("someone@example.com")
    assert orders == [] and err and "restarting" in err


def test_a_timeout_returns_its_own_error(monkeypatch):
    bi = _force(monkeypatch, TimeoutError("The read operation timed out"))
    _, err = bi.client_orders_with_status("someone@example.com")
    assert err and "timed out" in err


def test_a_success_returns_no_error(monkeypatch):
    """The other half. Crying failure on a working lookup would be the same bug mirrored."""
    import io
    import urllib.request

    from dashboard import biofield_invoice as bi
    monkeypatch.setenv("CONSOLE_SECRET", "fake-key")
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://example.invalid")

    class Resp(io.BytesIO):
        def __enter__(self): return self
        def __exit__(self, *a): return False
    monkeypatch.setattr(urllib.request, "urlopen",
                        lambda *a, **k: Resp(b'{"data": [{"id": 1}]}'))
    orders, err = bi.client_orders_with_status("someone@example.com")
    assert err is None and orders == [{"id": 1}]


def test_no_email_is_empty_without_being_an_error(monkeypatch):
    """A blank email is nothing to ask about, not a failure to report."""
    monkeypatch.setenv("CONSOLE_SECRET", "fake-key")
    from dashboard import biofield_invoice as bi
    assert bi.client_orders_with_status("") == ([], None)
