"""Ingesting an FMP export must not delete the legacy invoice history.

The four `fmp_*` projection tables are a UNION of two sources:

  * `fmp_orders` — the Remedy Match.fmp12 export, loaded here.
  * `legacy_fmp_invoices` — Healing Oasis Invoices.fmp12, which only ever APPENDS.

Until 2026-09-11 both entry points here called `_replace`, which DROPs the table.
So an ingest from one source silently deleted the other's rows.

Measured that day before changing anything: a fresh export carried 483 invoices
while production held about 16,300 — the remainder being 15,822 legacy invoices
going back to 1999-05-21.

And it was NOT recoverable. `legacy_fmp_invoices.build_payload` needs an invoice
summary carrying Contact ID; the only retained legacy files are line items, whose
columns are Invoice ID, Invoice Date and Line Items::* with no Contact ID at all.
That history exists solely inside a FileMaker file nobody had open.

So these tests pin the survival of rows the payload does not mention.
"""

import sqlite3

import pytest

from dashboard import fmp_orders as FO


LEGACY = ("legacy-1", "c-legacy", "1999-05-21", "Historical", "10", "10", "0", "0")
FRESH = ("fresh-1", "c-fresh", "2026-09-10", "Active", "70", "70", "0", "0")


@pytest.fixture
def cx():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    FO.ensure_tables(conn)
    # Stand in for what legacy_fmp_invoices appended: one 1999 invoice.
    conn.execute(
        "INSERT INTO fmp_invoices (" + ",".join(FO._INV_COLS) + ") VALUES (?,?,?,?,?,?,?,?)",
        LEGACY)
    conn.commit()
    yield conn
    conn.close()


def _ids(conn):
    return {r[0] for r in conn.execute("SELECT id_pk FROM fmp_invoices").fetchall()}


def test_an_ingest_keeps_the_legacy_history(cx):
    """The whole point. The payload knows nothing about the 1999 invoice."""
    FO.ingest_payload(cx, {"invoices": [FRESH]})
    assert _ids(cx) == {"legacy-1", "fresh-1"}


def test_the_old_behaviour_would_have_destroyed_it(cx):
    """Pinned deliberately, so the danger is visible rather than remembered."""
    FO.ingest_payload(cx, {"invoices": [FRESH]}, replace=True)
    assert _ids(cx) == {"fresh-1"}
    assert "legacy-1" not in _ids(cx)


def test_a_row_the_payload_does_mention_is_updated_not_duplicated(cx):
    FO.ingest_payload(cx, {"invoices": [FRESH]})
    changed = ("fresh-1", "c-fresh", "2026-09-10", "Active", "99", "99", "0", "0")
    FO.ingest_payload(cx, {"invoices": [changed]})
    rows = cx.execute("SELECT * FROM fmp_invoices WHERE id_pk='fresh-1'").fetchall()
    assert len(rows) == 1
    assert rows[0]["total"] == "99"
    assert _ids(cx) == {"legacy-1", "fresh-1"}


def test_ingesting_twice_changes_nothing(cx):
    FO.ingest_payload(cx, {"invoices": [FRESH]})
    first = sorted(tuple(r) for r in cx.execute("SELECT * FROM fmp_invoices"))
    FO.ingest_payload(cx, {"invoices": [FRESH]})
    second = sorted(tuple(r) for r in cx.execute("SELECT * FROM fmp_invoices"))
    assert first == second


def test_an_empty_payload_deletes_nothing(cx):
    """A failed or partial export must not empty the projection."""
    FO.ingest_payload(cx, {})
    assert _ids(cx) == {"legacy-1"}


def test_duplicate_ids_already_present_do_not_break_the_merge(cx):
    """The legacy README records two duplicated invoice ids in its source, which is
    why this upserts by delete-then-insert rather than ON CONFLICT: these tables
    carry no unique constraint and one could not be added to live data."""
    cx.execute("INSERT INTO fmp_invoices (" + ",".join(FO._INV_COLS) + ") "
               "VALUES (?,?,?,?,?,?,?,?)", LEGACY)   # a second identical row
    cx.commit()
    assert cx.execute("SELECT COUNT(*) FROM fmp_invoices").fetchone()[0] == 2

    FO.ingest_payload(cx, {"invoices": [FRESH]})

    # Both legacy rows survive untouched; the fresh one is added.
    assert cx.execute(
        "SELECT COUNT(*) FROM fmp_invoices WHERE id_pk='legacy-1'").fetchone()[0] == 2
    assert cx.execute(
        "SELECT COUNT(*) FROM fmp_invoices WHERE id_pk='fresh-1'").fetchone()[0] == 1


def test_a_payload_row_collapses_pre_existing_duplicates_of_its_own_id(cx):
    """If the payload owns an id, it becomes the single authority for that id."""
    dup = ("fresh-1", "c-fresh", "2026-01-01", "Old", "1", "1", "0", "0")
    for _ in range(2):
        cx.execute("INSERT INTO fmp_invoices (" + ",".join(FO._INV_COLS) + ") "
                   "VALUES (?,?,?,?,?,?,?,?)", dup)
    cx.commit()

    FO.ingest_payload(cx, {"invoices": [FRESH]})

    rows = cx.execute("SELECT * FROM fmp_invoices WHERE id_pk='fresh-1'").fetchall()
    assert len(rows) == 1 and rows[0]["invoice_date"] == "2026-09-10"


def test_every_table_merges_not_just_invoices(cx):
    for table, cols, legacy_id in (
            ("fmp_clients", FO._CLIENT_COLS, "cl-legacy"),
            ("fmp_invoice_items", FO._ITEM_COLS, "it-legacy"),
            ("fmp_client_addresses", FO._ADDR_COLS, "ad-legacy")):
        cx.execute(f"INSERT INTO {table} ({','.join(cols)}) VALUES ("
                   + ",".join("?" * len(cols)) + ")",
                   tuple([legacy_id] + [""] * (len(cols) - 1)))
    cx.commit()

    FO.ingest_payload(cx, {
        "clients": [tuple(["cl-new"] + [""] * (len(FO._CLIENT_COLS) - 1))],
        "items": [tuple(["it-new"] + [""] * (len(FO._ITEM_COLS) - 1))],
        "addresses": [tuple(["ad-new"] + [""] * (len(FO._ADDR_COLS) - 1))],
    })

    for table, legacy_id, new_id in (
            ("fmp_clients", "cl-legacy", "cl-new"),
            ("fmp_invoice_items", "it-legacy", "it-new"),
            ("fmp_client_addresses", "ad-legacy", "ad-new")):
        ids = {r[0] for r in cx.execute(f"SELECT id_pk FROM {table}").fetchall()}
        assert legacy_id in ids, f"{table} lost its legacy row"
        assert new_id in ids, f"{table} did not take the new row"


def test_the_indexes_survive_a_merge(cx):
    """_replace dropped the table and with it every index. A merge must not."""
    FO.ingest_payload(cx, {"invoices": [FRESH]})
    idx = {r[0] for r in cx.execute(
        "SELECT name FROM sqlite_master WHERE type='index'").fetchall()}
    assert "idx_fmp_invoices_client" in idx


def test_a_row_with_a_blank_key_is_still_inserted(cx):
    """A blank id_pk cannot be targeted for delete, but the row must not vanish."""
    blank = ("", "c-x", "2026-09-10", "Active", "1", "1", "0", "0")
    FO.ingest_payload(cx, {"invoices": [blank]})
    assert cx.execute(
        "SELECT COUNT(*) FROM fmp_invoices WHERE id_pk=''").fetchone()[0] == 1
    assert "legacy-1" in _ids(cx)


# ── NUL bytes from the FileMaker export (2026-09-11) ─────────────────────────
#
# FileMaker's AppleScript export writes the occasional field as UTF-16LE inside an
# otherwise UTF-8 file, so real text arrives with a NUL between every character.
# SQLite stores that happily. Postgres refuses the whole INSERT with "text fields
# cannot contain NUL (0x00) bytes", and one address failed an entire 17,420-row
# ingest against production.

def test_nul_bytes_are_stripped_and_the_text_is_recovered(tmp_path):
    """Stripping is a repair, not a workaround: removing the NULs from a UTF-16LE
    run yields exactly the intended characters. Verified against the real row."""
    d = tmp_path / "export"
    d.mkdir()
    addr = "2001 Miraloma Ave"
    utf16ish = "".join(ch + "\x00" for ch in addr)      # what the export contained
    (d / "clients_address.csv").write_text(
        "id_pk,id_fk_client,type,address_street,address_city,address_province,"
        "address_postal_code,address_country\n"
        f"a1,c1,home,{utf16ish},Placentia,CA,92870,US\n", encoding="utf-8")
    for name, header in (("clients.csv", ",".join(FO._CLIENT_COLS)),
                         ("invoices.csv", "id_pk,id_fk_client,invoice_date,closed,"
                                          "zc_invoice_subtotal,zc_invoice_total,"
                                          "shipping_fee,zc_overdue_balance"),
                         ("invoice_items.csv", "id_pk,id_fk_invoice,id_fk_product,"
                                               "description,qty,price,zc_ext_price")):
        (d / name).write_text(header + "\n", encoding="utf-8")

    conn = sqlite3.connect(":memory:")
    FO.ensure_tables(conn)
    FO.build_projection_from_csv(conn, str(d))
    got = conn.execute("SELECT street FROM fmp_client_addresses WHERE id_pk='a1'"
                       ).fetchone()[0]
    conn.close()

    assert "\x00" not in got, "a NUL would fail the whole INSERT on Postgres"
    assert got == addr


def test_no_field_anywhere_keeps_a_nul(tmp_path):
    """Every column goes through the same reader, so pin the whole row rather than
    the one field that happened to break."""
    d = tmp_path / "export"
    d.mkdir()
    poisoned = "x\x00y"
    (d / "clients.csv").write_text(
        ",".join(FO._CLIENT_COLS) + "\n"
        + ",".join([poisoned] * len(FO._CLIENT_COLS)) + "\n", encoding="utf-8")
    (d / "clients_address.csv").write_text(
        "id_pk,id_fk_client,type,address_street,address_city,address_province,"
        "address_postal_code,address_country\n", encoding="utf-8")
    (d / "invoices.csv").write_text(
        "id_pk,id_fk_client,invoice_date,closed,zc_invoice_subtotal,"
        "zc_invoice_total,shipping_fee,zc_overdue_balance\n", encoding="utf-8")
    (d / "invoice_items.csv").write_text(
        "id_pk,id_fk_invoice,id_fk_product,description,qty,price,zc_ext_price\n",
        encoding="utf-8")

    conn = sqlite3.connect(":memory:")
    FO.ensure_tables(conn)
    FO.build_projection_from_csv(conn, str(d))
    row = conn.execute("SELECT * FROM fmp_clients").fetchone()
    conn.close()
    assert all("\x00" not in (v or "") for v in row)
    assert row[0] == "xy"
