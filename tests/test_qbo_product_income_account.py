from dashboard import qbo_billing as qb


def _account_response(accounts):
    return {"QueryResponse": {"Account": accounts}}


def test_product_income_account_prefers_qbo_product_sales_subtype(monkeypatch):
    monkeypatch.delenv("QBO_PRODUCT_INCOME_ACCOUNT_ID", raising=False)
    monkeypatch.setattr(qb, "_query", lambda q: _account_response([
        {"Id": "B", "Name": "Billable Expense Income", "AccountSubType": "OtherPrimaryIncome"},
        {"Id": "S", "Name": "Sales of Product Income", "AccountSubType": "SalesOfProductIncome"},
        {"Id": "O", "Name": "Other Income", "AccountSubType": "OtherPrimaryIncome"},
    ]))
    assert qb._product_income_account_id() == "S"


def test_product_income_account_never_falls_back_to_billable_expenses(monkeypatch):
    monkeypatch.delenv("QBO_PRODUCT_INCOME_ACCOUNT_ID", raising=False)
    monkeypatch.setattr(qb, "_query", lambda q: _account_response([
        {"Id": "B", "Name": "Billable Expense Income"},
    ]))
    assert qb._product_income_account_id() is None


def test_product_income_account_does_not_guess_an_unrelated_income_account(monkeypatch):
    monkeypatch.delenv("QBO_PRODUCT_INCOME_ACCOUNT_ID", raising=False)
    monkeypatch.setattr(qb, "_query", lambda q: _account_response([
        {"Id": "O", "Name": "Uncategorized Income"},
    ]))
    assert qb._product_income_account_id() is None


def test_configured_product_income_account_wins_without_query(monkeypatch):
    monkeypatch.setenv("QBO_PRODUCT_INCOME_ACCOUNT_ID", "CONFIGURED")
    monkeypatch.setattr(qb, "_query", lambda q: (_ for _ in ()).throw(
        AssertionError("configured account must avoid a lookup")))
    assert qb._product_income_account_id() == "CONFIGURED"


def test_existing_billable_expense_item_is_repaired(monkeypatch):
    calls = []
    monkeypatch.setattr(qb, "_query", lambda q: {
        "QueryResponse": {"Item": [{
            "Id": "I1", "SyncToken": "4", "Name": "Terrain Restore",
            "IncomeAccountRef": {"value": "B", "name": "Billable Expense Income"},
        }]}})
    monkeypatch.setattr(qb, "_product_income_account_id", lambda: "SALES")
    monkeypatch.setattr(qb, "_post", lambda path, body: calls.append((path, body)) or {
        "Item": {"Id": "I1", "IncomeAccountRef": {"value": "SALES"}}})

    item = qb.find_or_create_item("Terrain Restore", 69.97)

    assert item["IncomeAccountRef"]["value"] == "SALES"
    assert calls == [("/item", {
        "Id": "I1", "SyncToken": "4", "sparse": True,
        "IncomeAccountRef": {"value": "SALES"},
    })]


def test_new_item_uses_product_sales_account(monkeypatch):
    calls = []
    monkeypatch.setattr(qb, "_query", lambda q: {"QueryResponse": {"Item": []}})
    monkeypatch.setattr(qb, "_product_income_account_id", lambda: "SALES")
    monkeypatch.setattr(qb, "_post", lambda path, body: calls.append((path, body)) or {
        "Item": {"Id": "I2"}})

    qb.find_or_create_item("New Product", 25)

    assert calls[0][1]["IncomeAccountRef"] == {"value": "SALES"}
