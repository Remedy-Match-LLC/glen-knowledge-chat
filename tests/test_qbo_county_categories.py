from dashboard import qbo_billing as qb
from dashboard.hawaii_counties import category_for_zip


def test_fmp_zip_mapping_and_non_hawaii_fallback():
    assert category_for_zip("96720") == "Hawaii County"
    assert category_for_zip("96744-1234") == "Honolulu County"
    assert category_for_zip("96793") == "Maui County"
    assert category_for_zip("96766") == "Kauai County"
    assert category_for_zip("90210") == "Non-Hawaii"
    assert category_for_zip("") is None
    assert category_for_zip("V6B 1A1") == "Non-Hawaii"


def test_sales_receipt_has_no_qbo_county_class(monkeypatch):
    captured = {}
    monkeypatch.setattr(qb, "_post", lambda path, body: captured.update(
        path=path, body=body) or {"SalesReceipt": {"Id": "SR1"}})
    monkeypatch.setattr(qb, "_first_bank_account_id", lambda: "BANK1")
    monkeypatch.setattr(qb, "find_or_create_item", lambda *a, **k: {"Id": "ITEM1"})
    qb.create_sales_receipt(
        {"Id": "C1"}, [{"name": "Order Total", "amount": 10, "qty": 1}])

    detail = captured["body"]["Line"][0]["SalesItemLineDetail"]
    assert "ClassRef" not in detail

