"""Reading a shipment row must work on the backend production actually runs.

_activate_coaching_for_shipment used
`row["k"] if isinstance(row, sqlite3.Row) else row.get("k")`, which assumes
anything that is not a sqlite3.Row is a dict. On Postgres it is neither: it is a
pgcompat.HybridRow, which indexes by column name but has no .get(). Production is
Postgres, so the function raised AttributeError on its first line every time.

Nothing caught it because nothing reached it. Both EasyPost paths are no-ops while
the production key is held, so the only live callers were the two manual "mark
delivered" routes. The USPS status sweep is the first scheduled caller, and it
surfaced as one unexplained errors=1 per run on 2026-09-10.

These tests use the real HybridRow rather than a stand-in, because a hand-built
fake row is exactly what let the bug through: a dict passes the old code fine.
"""

import sqlite3

import pytest

from dashboard.pgcompat import HybridRow


COLUMNS = ["id", "tracking_number", "order_uuid", "resolved_email", "delivered_at"]
VALUES = (7, "9405530109355412869056", "019e3cbe-bd74", "client@example.com", None)


@pytest.fixture
def app_module():
    import importlib
    import sys
    from pathlib import Path
    repo = Path(__file__).resolve().parent.parent
    if str(repo) not in sys.path:
        sys.path.insert(0, str(repo))
    try:
        return importlib.import_module("app")
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"app not importable: {e}")


def test_hybridrow_still_has_no_get():
    """The premise. If HybridRow ever gains .get() this test should be revisited,
    but note that adding one flips scripts/weekly_live_invitation.py's
    hasattr(row, "get") branch over a `SELECT lower(email)` whose column is named
    "lower", silently emptying a weekly email audience."""
    row = HybridRow(COLUMNS, VALUES)
    assert not isinstance(row, sqlite3.Row)
    assert not hasattr(row, "get")
    assert row["tracking_number"] == "9405530109355412869056"


def test_reads_a_postgres_row(app_module):
    row = HybridRow(COLUMNS, VALUES)
    assert app_module._shipment_field(row, "id") == 7
    assert app_module._shipment_field(row, "order_uuid") == "019e3cbe-bd74"
    assert app_module._shipment_field(row, "resolved_email") == "client@example.com"
    assert app_module._shipment_field(row, "delivered_at") is None


def test_reads_a_sqlite_row(app_module, tmp_path):
    """The other live backend. Built with sqlite3 itself, not imitated."""
    cx = sqlite3.connect(str(tmp_path / "t.db"))
    cx.row_factory = sqlite3.Row
    cx.execute("CREATE TABLE shipments (id INTEGER, order_uuid TEXT, "
               "delivered_at TEXT)")
    cx.execute("INSERT INTO shipments VALUES (7, '019e3cbe-bd74', NULL)")
    row = cx.execute("SELECT * FROM shipments").fetchone()
    cx.close()
    assert isinstance(row, sqlite3.Row)
    assert app_module._shipment_field(row, "id") == 7
    assert app_module._shipment_field(row, "order_uuid") == "019e3cbe-bd74"
    assert app_module._shipment_field(row, "delivered_at") is None


def test_reads_a_plain_dict(app_module):
    """Some callers pass a dict. It must keep working."""
    d = dict(zip(COLUMNS, VALUES))
    assert app_module._shipment_field(d, "id") == 7
    assert app_module._shipment_field(d, "delivered_at") is None


@pytest.mark.parametrize("row", [
    HybridRow(COLUMNS, VALUES),
    dict(zip(COLUMNS, VALUES)),
])
def test_an_absent_column_returns_the_default_rather_than_raising(app_module, row):
    """A shipments table missing a column mid-migration must not 500 the cron."""
    assert app_module._shipment_field(row, "no_such_column") is None
    assert app_module._shipment_field(row, "no_such_column", "fallback") == "fallback"


def test_a_delivered_postgres_row_is_recognised_as_already_processed(app_module):
    """The exact line that raised. delivered_at set means skip, and reaching that
    verdict at all was impossible on Postgres before this fix."""
    row = HybridRow(COLUMNS, (7, "9405", "uuid", "c@e.com", "2026-09-09T10:58:00Z"))
    assert app_module._shipment_field(row, "delivered_at") == "2026-09-09T10:58:00Z"
    out = app_module._activate_coaching_for_shipment(
        None, row, delivered_at="2026-09-10T22:00:00Z")
    assert out == {"skipped": "already_processed"}


def test_a_none_row_does_not_raise(app_module):
    assert app_module._shipment_field(None, "id") is None
