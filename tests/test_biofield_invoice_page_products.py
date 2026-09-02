from dashboard.biofield_report_html import render_invoice_page


def test_invoice_page_previews_authored_products_and_requires_explicit_sync():
    report = {"test_id": "a33", "client": {"name": "Debra", "email": "d@x.com"},
              "layers": [
                  {"remedy": ""},
                  {"remedy": "OcuFlow Bedtime"},
                  {"remedy": "IOP Syntropy"},
                  {"remedy": "OcuFlow Bedtime"},
              ]}
    fee = {"email": "d@x.com", "has_email": True, "available": True,
           "value_cents": 99700, "standard_cents": 30000,
           "courtesy_cents": None, "note": ""}
    html = render_invoice_page(report, fee)
    assert "Products ready to sync" in html
    assert html.count("<li>OcuFlow Bedtime</li>") == 1
    assert "<li>IOP Syntropy</li>" in html
    assert "Sync 2 products to invoice" in html
    assert "Opening this tab does not change the draft invoice" in html
