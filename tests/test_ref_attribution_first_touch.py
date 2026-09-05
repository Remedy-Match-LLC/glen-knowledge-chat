"""An existing active referral attribution wins over a later one.

Glen's ruling 2026-09-05. The cookie's own 90-day life IS the attribution window, so
"is there an active attribution?" is simply "is there already a cookie?".

Before this, all seven places that set rm_ref overwrote unconditionally, and the
behaviour contradicted itself: the same request READ the existing cookie in preference
to the new ?ref= and then OVERWROTE it for next time. The affiliate who got used and the
affiliate who got stored could differ on a single page view.
"""
import re
from pathlib import Path

APP = Path(__file__).resolve().parent.parent / "app.py"
SRC = APP.read_text()


class _Req:
    def __init__(self, cookies=None, secure=True):
        self.cookies = cookies or {}
        self.is_secure = secure


class _Resp:
    def __init__(self):
        self.set = []

    def set_cookie(self, name, value, **kw):
        self.set.append((name, value, kw))


def _helper():
    start = SRC.index("_REF_SLUG_RE = re.compile")
    end = SRC.index('@app.route("/begin/explore")')
    ns = {"re": re}
    exec(compile(SRC[start:end], "app_excerpt", "exec"), ns)
    return ns["_persist_ref_attribution"]


def test_a_first_visit_records_the_referrer():
    resp, f = _Resp(), _helper()
    assert f(resp, _Req(), "alice") is True
    assert resp.set and resp.set[0][:2] == ("rm_ref", "alice")


def test_an_existing_attribution_is_not_overwritten():
    """The ruling. A later affiliate must not take a referral already attributed."""
    resp, f = _Resp(), _helper()
    assert f(resp, _Req({"rm_ref": "alice"}), "bob") is False
    assert resp.set == [], "bob overwrote alice's active attribution"


def test_the_window_is_ninety_days():
    resp, f = _Resp(), _helper()
    f(resp, _Req(), "alice")
    assert resp.set[0][2]["max_age"] == 90 * 24 * 3600


def test_a_junk_or_empty_ref_records_nothing():
    f = _helper()
    for bad in ("", None, "  ", "has space", "a" * 65, "semi;colon"):
        resp = _Resp()
        assert f(resp, _Req(), bad) is False, f"{bad!r} should not be recorded"
        assert resp.set == []


def test_every_setter_goes_through_the_helper():
    """Seven routes used to set this cookie by hand. If one is added back raw, the
    ruling is silently half-applied on whichever page that is."""
    raw = [ln for ln in SRC.splitlines()
           if 'set_cookie("rm_ref"' in ln and "_REF_COOKIE_MAX_AGE" not in ln]
    assert not raw, f"rm_ref set outside the helper: {raw}"
