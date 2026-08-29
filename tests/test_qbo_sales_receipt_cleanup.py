import sqlite3

import pytest

from dashboard import qbo_billing, qbo_sale
from dashboard import qbo_sales_receipt_cleanup as cleanup


class FakeQBO:
    def __init__(self, note="order:tok1", account="Bank of Hawaii Checking", total=12.34):
        self.note, self.account, self.total = note, account, total
        self.deleted = []

    def get_sales_receipt(self, rid):
        return {"Id": str(rid), "DocNumber": "1042", "TotalAmt": self.total,
                "PrivateNote": self.note,
                "DepositToAccountRef": {"value": "BANK1"}, "SyncToken": "0"}

    def get_account(self, account_id):
        return {"Id": account_id, "Name": self.account}

    def delete_sales_receipt(self, rid):
        self.deleted.append(str(rid))


@pytest.fixture
def cx():
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.execute("CREATE TABLE orders (id INTEGER PRIMARY KEY, external_ref TEXT, "
               "email TEXT, total_cents INTEGER, qbo_sales_receipt_id TEXT)")
    db.execute("INSERT INTO orders VALUES (1, 'tok1', 'a@example.com', 1234, '99')")
    return db


def test_audit_all_guards_match(cx):
    assert cleanup.audit(cx, FakeQBO())[0]["deletable"] is True


@pytest.mark.parametrize("qbo", [
    FakeQBO(note="order:somebody-else"),
    FakeQBO(account="Mechanics Bank"),
    FakeQBO(total=99.00),
])
def test_audit_blocks_any_identity_mismatch(cx, qbo):
    assert cleanup.audit(cx, qbo)[0]["status"] == "blocked"


def test_delete_reaudits_marks_reference_and_leaves_order_amount(cx):
    qbo = FakeQBO()
    cleanup.delete_confirmed(cx, qbo, ["99"])
    row = cx.execute("SELECT total_cents, qbo_sales_receipt_id FROM orders").fetchone()
    assert qbo.deleted == ["99"]
    assert row["total_cents"] == 1234
    assert row["qbo_sales_receipt_id"] == "DELETED:99"


def test_delete_refuses_ambiguous_id(cx):
    qbo = FakeQBO(account="Mechanics Bank")
    with pytest.raises(ValueError, match="not currently safe"):
        cleanup.delete_confirmed(cx, qbo, ["99"])
    assert qbo.deleted == []


def test_qbo_delete_uses_current_sync_token(monkeypatch):
    calls = []
    monkeypatch.setattr(qbo_billing, "get_sales_receipt",
                        lambda rid: {"Id": str(rid), "SyncToken": "7"})
    monkeypatch.setattr(qbo_billing, "_post",
                        lambda path, body: calls.append((path, body)) or {})
    qbo_billing.delete_sales_receipt("99")
    assert calls == [("/salesreceipt?operation=delete",
                      {"Id": "99", "SyncToken": "7"})]
