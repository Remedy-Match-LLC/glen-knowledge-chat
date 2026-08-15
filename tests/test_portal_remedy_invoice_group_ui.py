from pathlib import Path


HTML = (Path(__file__).resolve().parents[1] / "static" / "client-portal.html").read_text()


def test_current_invoice_and_history_have_distinct_groups():
    assert "Current invoice" in HTML
    assert "Newly prescribed items awaiting payment." in HTML
    assert "Previously ordered" in HTML
    assert "View &amp; pay invoice" in HTML


def test_current_invoice_items_do_not_receive_reorder_controls():
    invoice_block = HTML.split('let invoiceGroups = "";', 1)[1].split(
        'const historyHtml =', 1)[0]
    assert 'class="remitem current-invoice-item"' in invoice_block
    assert "qtybtn" not in invoice_block
    assert "remBtn" not in invoice_block
    assert 'line.service ? `<span class="prov">Service</span>`' in invoice_block
