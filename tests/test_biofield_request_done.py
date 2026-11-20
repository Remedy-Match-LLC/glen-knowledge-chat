"""Done on a Possible request (Glen, 2026-11-20): handled elsewhere, moves to Finished."""
import sqlite3

import pytest

from dashboard import biofield_requests as br


@pytest.fixture
def cx(tmp_path):
    c = sqlite3.connect(str(tmp_path / "t.db"))
    br.ensure_schema(c)
    yield c
    c.close()


def _add(cx, status):
    cx.execute(f"INSERT INTO {br.TABLE} (email, name, source, source_ref, requested_at, status, certainty, created_at) "
               "VALUES ('a@x.com','A','chat',?, '2026-11-20', ?, 'possible', '2026-11-20')", (status + "-ref", status))
    cx.commit()
    return cx.execute("SELECT max(id) FROM " + br.TABLE).fetchone()[0]


def test_done_closes_a_possible_request_and_lists_it_as_done(cx):
    rid = _add(cx, "pending_yes")
    assert br.mark_done(cx, rid) == (True, "")
    assert [r["id"] for r in br.list_open(cx)] == []
    done = br.list_done(cx)
    assert [r["id"] for r in done] == [rid] and done[0]["decided_at"]


@pytest.mark.parametrize("status", ["requested", "started", "declined", "done", "duplicate"])
def test_done_touches_nothing_but_a_possible_request(cx, status):
    rid = _add(cx, status)
    ok, err = br.mark_done(cx, rid)
    assert not ok and "no longer waiting" in err
    assert cx.execute(f"SELECT status FROM {br.TABLE} WHERE id=?", (rid,)).fetchone()[0] == status


def test_a_done_request_does_not_block_a_new_one(cx):
    """Duplicates are judged against open lines only, as for a declined request."""
    rid = _add(cx, "pending_yes")
    br.mark_done(cx, rid)
    assert br.list_open(cx) == []
    src = open(br.__file__).read()
    assert '"WHERE status IN (?,?) ORDER BY id", OPEN_STATUSES' in src
