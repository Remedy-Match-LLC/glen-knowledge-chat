import sqlite3

import pytest

from dashboard import person_aliases as pa


@pytest.fixture
def cx(tmp_path):
    c = sqlite3.connect(str(tmp_path / "t.db"))
    pa.init_tables(c)
    return c


def test_unknown_address_is_itself_normalised(cx):
    assert pa.canonical_email(cx, "  Mel@Example.com ") == "mel@example.com"


def test_alias_resolves_and_chains(cx):
    pa.add_alias(cx, "a@x.com", "b@x.com", 1)
    pa.add_alias(cx, "b@x.com", "c@x.com", 2)
    assert pa.canonical_email(cx, "A@X.com") == "c@x.com"


def test_cycle_is_refused(cx):
    pa.add_alias(cx, "a@x.com", "b@x.com", 1)
    with pytest.raises(ValueError):
        pa.add_alias(cx, "b@x.com", "a@x.com", 2)


def test_existing_alias_is_refused(cx):
    pa.add_alias(cx, "a@x.com", "b@x.com", 1)
    with pytest.raises(ValueError):
        pa.add_alias(cx, "a@x.com", "c@x.com", 2)


def test_remove_merge_aliases(cx):
    pa.add_alias(cx, "a@x.com", "b@x.com", 7)
    pa.add_token_alias(cx, "hash1", "b@x.com", 7)
    pa.remove_merge_aliases(cx, 7)
    assert pa.canonical_email(cx, "a@x.com") == "a@x.com"
    assert pa.token_alias(cx, "hash1") is None


def test_token_alias(cx):
    pa.add_token_alias(cx, "hash1", "b@x.com", 3)
    assert pa.token_alias(cx, "hash1") == "b@x.com"
