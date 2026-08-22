from pathlib import Path


HTML = (Path(__file__).resolve().parents[1] / "static" / "console-money.html").read_text()


def test_money_page_has_monthly_and_custom_date_controls():
    assert "July" in HTML
    assert "August to date" in HTML
    assert 'id="p-start" type="date"' in HTML
    assert 'id="p-end" type="date"' in HTML
    assert "params.set('start',start)" in HTML
    assert "params.set('end',end)" in HTML


def test_money_page_exposes_method_totals_and_export():
    assert "s.by_method" in HTML
    assert "payment_method" in HTML
    assert "Export CSV" in HTML
    assert "paid-invoices-" in HTML
    assert "window.print()" in HTML
