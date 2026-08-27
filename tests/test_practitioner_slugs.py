"""Unit tests for the practitioner slug namespace. Imports no Flask app."""
import pytest
from werkzeug.routing import Map, Rule

from dashboard import practitioner_slugs as ps


def test_normalize_lowercases_and_strips():
    assert ps.normalize("  Mary-Boyd  ") == "mary-boyd"


def test_normalize_handles_none_and_empty():
    assert ps.normalize(None) == ""
    assert ps.normalize("") == ""


@pytest.mark.parametrize("good", ["mary-boyd", "abc", "a1-b2-c3", "healing-oasis-hilo"])
def test_check_shape_accepts_valid(good):
    ps.check_shape(good)  # must not raise


@pytest.mark.parametrize("bad", [
    "-mary",          # leading hyphen
    "mary-",          # trailing hyphen
    "mary--boyd",     # doubled hyphen
    "Mary-Boyd",      # uppercase
    "mary boyd",      # space
    "mary_boyd",      # underscore
    "mary.boyd",      # dot
    "ab",             # too short
    "a" * 41,         # too long
    "",               # empty
])
def test_check_shape_rejects_invalid(bad):
    with pytest.raises(ps.SlugError):
        ps.check_shape(bad)


def _fake_map():
    return Map([
        Rule("/"),
        Rule("/portal"),
        Rule("/api/p/<slug>"),
        Rule("/begin/explore"),
        Rule("/<slug>"),          # our own catch-all must NOT reserve itself
    ])


def test_route_segments_extracts_static_first_segments():
    segs = ps.route_segments(_fake_map())
    assert "portal" in segs
    assert "api" in segs
    assert "begin" in segs


def test_route_segments_ignores_dynamic_and_root():
    segs = ps.route_segments(_fake_map())
    assert "<slug>" not in segs
    assert "" not in segs
    # The catch-all itself contributes nothing, or no slug could ever be valid.
    assert segs == {"portal", "api", "begin"}


def test_reserved_for_unions_route_segments_and_extras():
    reserved = ps.reserved_for(_fake_map())
    assert "portal" in reserved          # from the map
    assert "login" in reserved           # from EXTRA_RESERVED
    assert "www" in reserved


def test_check_not_reserved_rejects_a_reserved_word():
    with pytest.raises(ps.SlugError):
        ps.check_not_reserved("portal", ps.reserved_for(_fake_map()))


def test_check_not_reserved_accepts_a_free_word():
    ps.check_not_reserved("mary-boyd", ps.reserved_for(_fake_map()))
